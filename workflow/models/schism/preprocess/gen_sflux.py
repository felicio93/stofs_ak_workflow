"""
models/schism/preprocess/gen_sflux.py
============
SLURM worker — converts one group of raw ERA5 data into SCHISM sflux files.

For group group_id, reads raw/era5/YYYY/era5_YYYYMM.nc (one or more monthly
ERA5 files covering the group date range) and writes:
    I{ID}_{group_id}/sflux/sflux_air_1.{N}.nc   (N = 1..ndays+1)
    I{ID}_{group_id}/sflux/sflux_prc_1.{N}.nc
    I{ID}_{group_id}/sflux/sflux_rad_1.{N}.nc
    I{ID}_{group_id}/sflux/sflux_inputs.txt

One EXTRA daily stack (N = ndays+1) covering the first day after the group
end is always written as a read-ahead bracket so SCHISM never runs out of
sflux records at the final timestep.

Each daily file contains 25 hourly timesteps (00Z day N to 00Z day N+1).

Variable derivation:
    spfh: specific humidity from 2m dewpoint (d2m) and MSL pressure (msl)
    All other variables passed through directly.

Usage (called by SLURM via workflow.models.schism.preprocess.submit_era5):
    python -m workflow.models.schism.preprocess.gen_sflux \
        --config <dir> --group <group_id>

For backward compatibility --month is also accepted as an alias for --group.
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import netCDF4 as nc4

from workflow.core.config import (
    load_config,
    model_dir,
    group_date_range,
    get_group_ndays,
)

SFLUX_CONVENTIONS = "CF-1.0"


# =============================================================================
# ERA5 file helpers
# =============================================================================

def _era5_path(mdir: Path, d: date) -> Path:
    """Return the ERA5 raw file path for a given date."""
    return mdir / "raw" / "era5" / str(d.year) / f"era5_{d.year}{d.month:02d}.nc"


def _open_era5_for_date(mdir: Path, d: date):
    """Open the ERA5 NetCDF dataset that contains the given date.
    Returns (nc4.Dataset, time_index) or raises FileNotFoundError.
    """
    nc_path = _era5_path(mdir, d)
    if not (nc_path.exists() and nc_path.stat().st_size > 0):
        raise FileNotFoundError(f"ERA5 file not found: {nc_path}")

    ds         = nc4.Dataset(str(nc_path))
    times_nc   = ds.variables["valid_time"]
    all_times  = nc4.num2date(times_nc[:], units=times_nc.units,
                              only_use_cftime_datetimes=False)

    # Find 00Z of the requested date
    idx_start = None
    for i, t in enumerate(all_times):
        if t.year == d.year and t.month == d.month \
                and t.day == d.day and t.hour == 0:
            idx_start = i
            break

    if idx_start is None:
        ds.close()
        raise ValueError(
            f"00Z for {d} not found in {nc_path.name}")

    return ds, idx_start, all_times


# =============================================================================
# Specific humidity derivation
# =============================================================================

def dewpoint_to_spfh(d2m_K: np.ndarray,
                     msl_Pa: np.ndarray) -> np.ndarray:
    """Convert 2m dewpoint (K) and MSL pressure (Pa) to specific humidity
    (kg/kg) using the Magnus formula — same as pyschism.
    """
    Td   = d2m_K - 273.15
    e    = 6.112 * np.exp((17.67 * Td) / (Td + 243.5))
    spfh = (0.622 * e) / (msl_Pa * 0.01 - 0.378 * e)
    return spfh.astype(np.float32)


# =============================================================================
# sflux NetCDF writer
# =============================================================================

def write_sflux_file(path: Path, ftype: str, day_date: date,
                     lon2d: np.ndarray, lat2d: np.ndarray,
                     times_days: np.ndarray, data: dict):
    """Write one daily sflux NetCDF file (NETCDF3_CLASSIC format)."""
    ny, nx = lon2d.shape
    ntime  = len(times_days)

    with nc4.Dataset(path, "w", format="NETCDF3_CLASSIC") as dst:
        dst.setncatts({"Conventions": SFLUX_CONVENTIONS})
        dst.createDimension("nx_grid", nx)
        dst.createDimension("ny_grid", ny)
        dst.createDimension("time", None)

        v = dst.createVariable("lon", "f4", ("ny_grid", "nx_grid"))
        v.long_name = "Longitude"; v.standard_name = "longitude"
        v.units = "degrees_east"; v[:] = lon2d

        v = dst.createVariable("lat", "f4", ("ny_grid", "nx_grid"))
        v.long_name = "Latitude"; v.standard_name = "latitude"
        v.units = "degrees_north"; v[:] = lat2d

        v = dst.createVariable("time", "f4", ("time",))
        v.long_name = "Time"; v.standard_name = "time"
        v.units = (f"days since {day_date.year}-{day_date.month}"
                   f"-{day_date.day} 00:00 UTC")
        v.base_date = (day_date.year, day_date.month, day_date.day, 0)
        v[:] = times_days

        var_meta = {
            "prmsl": ("Pressure reduced to MSL",
                      "air_pressure_at_sea_level", "Pa"),
            "spfh":  ("Surface Specific Humidity (2m AGL)",
                      "specific_humidity", "1"),
            "stmp":  ("Surface Air Temperature (2m AGL)",
                      "air_temperature", "K"),
            "uwind": ("Surface Eastward Air Velocity (10m AGL)",
                      "eastward_wind", "m/s"),
            "vwind": ("Surface Northward Air Velocity (10m AGL)",
                      "northward_wind", "m/s"),
            "prate": ("Surface Precipitation Rate",
                      "precipitation_flux", "kg/m^2/s"),
            "dlwrf": ("Downward Long Wave Radiation Flux",
                      "surface_downwelling_longwave_flux_in_air", "W/m^2"),
            "dswrf": ("Downward Short Wave Radiation Flux",
                      "surface_downwelling_shortwave_flux_in_air", "W/m^2"),
        }
        for varname, arr in data.items():
            meta = var_meta[varname]
            v = dst.createVariable(
                varname, "f4", ("time", "ny_grid", "nx_grid"))
            v.long_name = meta[0]
            v.standard_name = meta[1]
            v.units = meta[2]
            v[:] = arr


# =============================================================================
# Per-day sflux data reader
# =============================================================================

def _pad_last(arr, n):
    """Persist the last time record until arr has n records along axis 0."""
    if arr.shape[0] >= n:
        return arr
    last = arr[-1:, :, :]
    reps = n - arr.shape[0]
    return np.concatenate([arr] + [last] * reps, axis=0)


def _read_day_fields(mdir: Path, d: date) -> dict:
    """Read the 25-hour ERA5 field block for day d (00Z -> next day 00Z).

    Returns a dict with keys: u10, v10, msl, t2m, d2m, mtpr, dlwrf, dswrf.
    All arrays have shape (25, ny, nx) with lat in ASCENDING order.
    Pads the 25th record if it falls in the next month's file.
    Returns None if the ERA5 file for d is missing.
    """
    try:
        ds, idx_start, all_times = _open_era5_for_date(mdir, d)
    except (FileNotFoundError, ValueError) as exc:
        print(f"  WARNING: could not open ERA5 for {d}: {exc}")
        return None

    try:
        idx_end    = idx_start + 25
        actual_end = min(idx_end, len(all_times))
        sl         = slice(idx_start, actual_end)

        def rd(name_opts):
            for nm in name_opts:
                if nm in ds.variables:
                    return ds.variables[nm][sl, ::-1, :].astype(np.float32)
            return None

        fields = {
            "u10":   rd(["u10"]),
            "v10":   rd(["v10"]),
            "msl":   rd(["msl"]),
            "t2m":   rd(["t2m"]),
            "d2m":   rd(["d2m"]),
            "mtpr":  rd(["mtpr", "avg_tprate"]),
            "dlwrf": rd(["msdwlwrf", "avg_sdlwrf"]),
            "dswrf": rd(["msdwswrf", "avg_sdswrf"]),
        }

        # Pad to 25 records if we ran out of data in this file
        n_actual = fields["u10"].shape[0]
        if n_actual < 25:
            # Try the 00Z record from the next day's ERA5 file
            d_next = d + timedelta(days=1)
            try:
                ds_next, idx_next, _ = _open_era5_for_date(mdir, d_next)
                def rd_next(name_opts):
                    for nm in name_opts:
                        if nm in ds_next.variables:
                            return (ds_next.variables[nm]
                                    [idx_next:idx_next+1, ::-1, :]
                                    .astype(np.float32))
                    return None
                nxt = {
                    "u10":   rd_next(["u10"]),
                    "v10":   rd_next(["v10"]),
                    "msl":   rd_next(["msl"]),
                    "t2m":   rd_next(["t2m"]),
                    "d2m":   rd_next(["d2m"]),
                    "mtpr":  rd_next(["mtpr", "avg_tprate"]),
                    "dlwrf": rd_next(["msdwlwrf", "avg_sdlwrf"]),
                    "dswrf": rd_next(["msdwswrf", "avg_sdswrf"]),
                }
                ds_next.close()
                for k in fields:
                    if fields[k] is not None and nxt[k] is not None:
                        fields[k] = np.concatenate(
                            [fields[k], nxt[k]], axis=0)
                print(f"  {d}: 25th step read from next ERA5 file.")
            except Exception:
                pass   # fall through to _pad_last below

        # Final pad for any remaining shortfall
        for k in fields:
            if fields[k] is not None:
                fields[k] = _pad_last(fields[k], 25)
            else:
                # Variable missing from file — fill with zeros and warn
                ny = fields["u10"].shape[1]
                nx = fields["u10"].shape[2]
                fields[k] = np.zeros((25, ny, nx), dtype=np.float32)
                print(f"  WARNING: ERA5 variable missing for {d}, "
                      f"key={k!r} — filled with zeros.")

        return fields

    finally:
        ds.close()


# =============================================================================
# Main sflux generation
# =============================================================================

def gen_sflux_group(cfg: dict, group_id: str):
    """Generate sflux files for one group (group_id is YYYYMM or YYYYMMDD)."""
    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)

    gstart, gend = group_date_range(cfg, group_id)
    ndays        = get_group_ndays(cfg, group_id)

    sflux_dir = mdir / f"I{pid}" / f"I{pid}_{group_id}" / "sflux"
    sentinel  = sflux_dir / "gen_sflux.done"

    if sentinel.exists():
        print(f"  gen_sflux: {group_id} already complete "
              f"(sentinel found). Skipping.")
        return

    sflux_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n--- gen_sflux {group_id}  "
          f"({gstart} -> {gend}, {ndays} days) -> {sflux_dir} ---")

    # Read coordinates from the ERA5 file for the group start date
    era5_first = _era5_path(mdir, gstart)
    if not (era5_first.exists() and era5_first.stat().st_size > 0):
        print(f"ERROR: ERA5 file not found: {era5_first}")
        sys.exit(1)

    with nc4.Dataset(str(era5_first)) as ds:
        lons_1d = ds.variables["longitude"][:]    # 0..360
        lats_1d = ds.variables["latitude"][::-1]  # ascending
    lon2d, lat2d = np.meshgrid(lons_1d, lats_1d)

    _last_fields = None   # saved for read-ahead pad fallback

    # --- Write one daily stack per group day ---
    for day_offset in range(ndays):
        d     = gstart + timedelta(days=day_offset)
        stack = str(day_offset + 1)   # 1-based, unpadded

        fields = _read_day_fields(mdir, d)
        if fields is None:
            print(f"  ERROR: could not read ERA5 for {d}. "
                  f"Check that download_era5 has run.")
            sys.exit(1)

        spfh       = dewpoint_to_spfh(fields["d2m"], fields["msl"])
        times_days = np.array([h / 24.0 for h in range(25)],
                              dtype=np.float32)

        write_sflux_file(
            sflux_dir / f"sflux_air_1.{stack}.nc", "air", d,
            lon2d, lat2d, times_days,
            {"prmsl": fields["msl"], "spfh": spfh,
             "stmp":  fields["t2m"],
             "uwind": fields["u10"], "vwind": fields["v10"]})
        write_sflux_file(
            sflux_dir / f"sflux_prc_1.{stack}.nc", "prc", d,
            lon2d, lat2d, times_days,
            {"prate": fields["mtpr"]})
        write_sflux_file(
            sflux_dir / f"sflux_rad_1.{stack}.nc", "rad", d,
            lon2d, lat2d, times_days,
            {"dlwrf": fields["dlwrf"], "dswrf": fields["dswrf"]})

        print(f"  {d}: air+prc+rad written (stack {stack})")
        _last_fields = fields

    # --- Extra read-ahead stack (ndays+1) ---
    extra_stack = str(ndays + 1)
    d_extra     = gend + timedelta(days=1)

    fields_extra = _read_day_fields(mdir, d_extra)
    if fields_extra is not None:
        src_note = f"from ERA5 ({d_extra})"
    elif _last_fields is not None:
        # Repeat last day shifted to the next calendar day
        fields_extra = _last_fields
        src_note     = "pad-repeated from last day (next ERA5 unavailable)"
    else:
        fields_extra = None
        src_note     = "SKIPPED (no next ERA5 and no last day)"

    if fields_extra is not None:
        spfh       = dewpoint_to_spfh(fields_extra["d2m"],
                                      fields_extra["msl"])
        times_days = np.array([h / 24.0 for h in range(25)],
                              dtype=np.float32)
        write_sflux_file(
            sflux_dir / f"sflux_air_1.{extra_stack}.nc", "air", d_extra,
            lon2d, lat2d, times_days,
            {"prmsl": fields_extra["msl"], "spfh": spfh,
             "stmp":  fields_extra["t2m"],
             "uwind": fields_extra["u10"], "vwind": fields_extra["v10"]})
        write_sflux_file(
            sflux_dir / f"sflux_prc_1.{extra_stack}.nc", "prc", d_extra,
            lon2d, lat2d, times_days,
            {"prate": fields_extra["mtpr"]})
        write_sflux_file(
            sflux_dir / f"sflux_rad_1.{extra_stack}.nc", "rad", d_extra,
            lon2d, lat2d, times_days,
            {"dlwrf": fields_extra["dlwrf"],
             "dswrf": fields_extra["dswrf"]})
        print(f"  {d_extra}: EXTRA read-ahead stack {extra_stack} "
              f"written ({src_note}).")
    else:
        print(f"  WARNING: extra read-ahead stack {extra_stack} {src_note}.")

    # sflux_inputs.txt — empty namelist, SCHISM uses its defaults
    (sflux_dir / "sflux_inputs.txt").write_text("&sflux_inputs\n/\n")
    print("  Written: sflux_inputs.txt")

    sentinel.touch()
    print(f"  Sentinel: {sentinel}")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate sflux files for one group")
    parser.add_argument("--config", required=True)
    # Accept both --group (new) and --month (legacy alias)
    group_arg = parser.add_mutually_exclusive_group(required=True)
    group_arg.add_argument("--group", dest="group_id",
                           help="Group ID (YYYYMM or YYYYMMDD)")
    group_arg.add_argument("--month", dest="group_id",
                           help="Group ID — legacy alias for --group")
    args = parser.parse_args()
    cfg  = load_config(Path(args.config))
    gen_sflux_group(cfg, args.group_id)
