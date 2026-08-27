import argparse
import sys
from calendar import monthrange
from datetime import datetime
from pathlib import Path

import numpy as np
import netCDF4 as nc4

from workflow.core.config import load_config, model_dir

# sflux name -> DATM name
SFLUX_TO_DATM = {
    "uwind": "UGRD_10maboveground",
    "vwind": "VGRD_10maboveground",
    "stmp":  "TMP_2maboveground",
    "spfh":  "SPFH_2maboveground",
    "prmsl": "MSLMA_meansealevel",
    "prate": "PRATE_surface",
    "dswrf": "DSWRF_surface",
    "dlwrf": "DLWRF_surface",
}

VAR_META = {
    "UGRD_10maboveground": ("U-Component of Wind", "m s-1"),
    "VGRD_10maboveground": ("V-Component of Wind", "m s-1"),
    "TMP_2maboveground": ("Temperature", "K"),
    "SPFH_2maboveground": ("Specific Humidity", "kg kg-1"),
    "PRATE_surface": ("Precipitation Rate", "kg m-2 s-1"),
    "DSWRF_surface": ("Downward Short-Wave Radiation Flux", "W m-2"),
    "DLWRF_surface": ("Downward Long-Wave Radiation Flux", "W m-2"),
    "MSLMA_meansealevel": ("Pressure Reduced to MSL", "Pa"),
}


def _datm_out_path(cfg: dict, ym: str) -> Path:
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    subdir = str(cfg.get("datm_subdir", "forcing"))
    tmpl = str(cfg.get("datm_filename_template", "datm_{YYYYMM}.nc"))
    name = tmpl.replace("{YYYYMM}", ym)
    return mdir / f"I{pid}" / f"I{pid}_{ym}" / subdir / name


def _sentinel_path(out_path: Path) -> Path:
    return out_path.parent / "gen_datm.done"


def _require_sflux(cfg: dict, ym: str) -> Path:
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    sflux_dir = mdir / f"I{pid}" / f"I{pid}_{ym}" / "sflux"
    sentinel = sflux_dir / "gen_sflux.done"
    if not sentinel.exists():
        print(f"ERROR: gen_sflux not complete for {ym} (missing {sentinel})")
        sys.exit(1)
    return sflux_dir


def _read_lon_lat(sflux_air_1_1: Path):
    with nc4.Dataset(str(sflux_air_1_1)) as ds:
        lon2d = ds.variables["lon"][:].astype("float32")
        lat2d = ds.variables["lat"][:].astype("float32")
    return lon2d, lat2d


def _append_day(varname: str, src_path: Path, out_list: list):
    with nc4.Dataset(str(src_path)) as ds:
        if varname not in ds.variables:
            raise KeyError(f"{varname} not found in {src_path.name}")
        v = ds.variables[varname][:]  # shape (time, ny, nx)
        # take first 24 hours only (drop overlap hour at idx=24)
        v24 = v[:24, :, :].astype("float32")
        out_list.append(v24)


def gen_datm_month(cfg: dict, ym: str):
    year = int(ym[:4])
    month = int(ym[4:])
    ndays = monthrange(year, month)[1]

    out_path = _datm_out_path(cfg, ym)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel = _sentinel_path(out_path)
    fillv = 9.999e+20

    if sentinel.exists() and out_path.exists() and out_path.stat().st_size > 0:
        print(f"  gen_datm: {ym} already complete (sentinel found). Skipping.")
        return

    sflux_dir = _require_sflux(cfg, ym)

    # Read coords from first air file
    air1 = sflux_dir / "sflux_air_1.1.nc"
    if not air1.exists():
        print(f"ERROR: missing {air1}")
        sys.exit(1)
    lon2d, lat2d = _read_lon_lat(air1)
    ny, nx = lon2d.shape

    # Accumulate monthly chunks by day
    chunks = {k: [] for k in SFLUX_TO_DATM.keys()}

    total_days = ndays + 1
    for day in range(1, total_days + 1):
        air = sflux_dir / f"sflux_air_1.{day}.nc"
        prc = sflux_dir / f"sflux_prc_1.{day}.nc"
        rad = sflux_dir / f"sflux_rad_1.{day}.nc"
        if not (air.exists() and prc.exists() and rad.exists()):
            raise FileNotFoundError(f"Missing one of: {air.name}, {prc.name}, {rad.name}")

        _append_day("uwind", air, chunks["uwind"])
        _append_day("vwind", air, chunks["vwind"])
        _append_day("stmp",  air, chunks["stmp"])
        _append_day("spfh",  air, chunks["spfh"])
        _append_day("prmsl", air, chunks["prmsl"])

        _append_day("prate", prc, chunks["prate"])

        _append_day("dswrf", rad, chunks["dswrf"])
        _append_day("dlwrf", rad, chunks["dlwrf"])

        print(f"  {ym} day {day:02d}/{total_days}: read sflux_*_1.{day}.nc")

    # Concatenate into (time, ny, nx)
    data = {}
    for sflux_name, day_list in chunks.items():
        arr = np.concatenate(day_list, axis=0)  # (ndays*24, ny, nx)
        if arr.shape[0] != total_days * 24:
            raise RuntimeError(f"{sflux_name}: expected {total_days*24} times, got {arr.shape[0]}")
        data[sflux_name] = arr

    # Time axis: seconds since 1970-01-01 00:00:00
    nt = total_days * 24
    start_time = datetime(year, month, 1)
    time_vals = np.arange(nt, dtype="float64") * 3600.0
    time_vals += (start_time - datetime(1970, 1, 1)).total_seconds()
    time_units = "seconds since 1970-01-01 00:00:00"

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.unlink(missing_ok=True)

    with nc4.Dataset(str(tmp), "w", format="NETCDF4") as nc:
        nc.createDimension("time", None)
        nc.createDimension("y", ny)
        nc.createDimension("x", nx)

        tvar = nc.createVariable("time", "f8", ("time",))
        tvar.units = time_units
        tvar.calendar = "standard"
        tvar.standard_name = "time"
        tvar.axis = "T"
        tvar[:] = time_vals

        lonv = nc.createVariable("longitude", "f4", ("y", "x"))
        lonv.units = "degrees_east"
        lonv.standard_name = "longitude"
        lonv.long_name = "longitude"
        lonv.axis = "X"
        lonv[:] = lon2d.astype("float32")

        latv = nc.createVariable("latitude", "f4", ("y", "x"))
        latv.units = "degrees_north"
        latv.standard_name = "latitude"
        latv.long_name = "latitude"
        latv.axis = "Y"
        latv[:] = lat2d.astype("float32")

        dsv = nc.createVariable("data_source", "b", ("y", "x"))
        dsv.long_name = "Data source (2=ERA5)"
        dsv[:] = np.full((ny, nx), 2, dtype=np.byte)

        for sflux_name, datm_name in SFLUX_TO_DATM.items():
            long_name, units = VAR_META[datm_name]
            v = nc.createVariable(datm_name, "f4", ("time", "y", "x"), fill_value=fillv)
            v.short_name = datm_name
            v.long_name = long_name
            v.units = units
            v[:] = data[sflux_name].astype("float32")

        nc.title = "DATM forcing for UFS-SCHISM (from ERA5 via sflux)"
        nc.source = "ERA5"
        nc.history = f"Created {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} by stofs_ak_workflow"
        nc.Conventions = "CF-1.6"

    tmp.replace(out_path)
    sentinel.touch()
    print(f"  Wrote {out_path} (nt={nt}, ny={ny}, nx={nx})")
    print(f"  Sentinel: {sentinel}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Convert SCHISM sflux -> CDEPS DATM forcing for one month")
    ap.add_argument("--config", required=True)
    ap.add_argument("--month", required=True, help="YYYYMM")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    gen_datm_month(cfg, args.month)
