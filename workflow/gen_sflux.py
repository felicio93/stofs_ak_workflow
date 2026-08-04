"""
gen_sflux.py
============
SLURM worker — converts one month of raw ERA5 data into SCHISM sflux files.

For month YYYYMM, reads raw/era5/YYYY/era5_YYYYMM.nc and writes:
    I{ID}_YYYYMM/sflux/sflux_air_1.{N}.nc   (N = 1..ndays)
    I{ID}_YYYYMM/sflux/sflux_prc_1.{N}.nc
    I{ID}_YYYYMM/sflux/sflux_rad_1.{N}.nc
    I{ID}_YYYYMM/sflux/sflux_inputs.txt

Each daily file contains 25 hourly timesteps (00Z day N to 00Z day N+1),
matching the pyschism convention for overlap between consecutive files.

SCHISM sflux file naming uses UNPADDED integers (current SCHISM source):
    sflux_air_1.1.nc, sflux_air_1.2.nc ... sflux_air_1.30.nc

Variable derivation:
    spfh: specific humidity computed from 2m dewpoint (d2m) and MSL pressure
          using the Magnus formula (same as pyschism):
          e  = 6.112 * exp(17.67 * Td / (Td + 243.5))    [hPa]
          spfh = 0.622 * e / (msl*0.01 - 0.378 * e)      [kg/kg]
    All other variables are passed through directly.

Longitude convention: 0-360 (matching the Bering Sea mesh and HYCOM files).
ERA5 CDS returns lons in 0-360 for domains east of 180 — no conversion needed.

Usage (called by SLURM via submit_era5.py):
    python gen_sflux.py --config <config_dir> --month YYYYMM
"""

import argparse
import sys
from calendar import monthrange
from datetime import date
from pathlib import Path

import numpy as np
import netCDF4 as nc4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow.config import load_config, model_dir

SFLUX_CONVENTIONS = "CF-1.0"


def dewpoint_to_spfh(d2m_K: np.ndarray, msl_Pa: np.ndarray) -> np.ndarray:
    """
    Convert 2m dewpoint (K) and MSL pressure (Pa) to specific humidity (kg/kg).
    Uses the Magnus formula — same as pyschism.
    """
    Td   = d2m_K - 273.15          # Kelvin → Celsius
    e    = 6.112 * np.exp((17.67 * Td) / (Td + 243.5))   # vapour pressure [hPa]
    spfh = (0.622 * e) / (msl_Pa * 0.01 - 0.378 * e)     # specific humidity [kg/kg]
    return spfh.astype(np.float32)


def write_sflux_file(path: Path, ftype: str, day_date: date,
                     lon2d: np.ndarray, lat2d: np.ndarray,
                     times_days: np.ndarray, data: dict):
    """
    Write one daily sflux NetCDF file (NETCDF3_CLASSIC format).

    ftype: 'air' | 'prc' | 'rad'
    times_days: array of time in days since midnight of day_date (25 values)
    data: dict of {varname: array (ntime, ny, nx)}
    """
    ny, nx = lon2d.shape
    ntime  = len(times_days)

    with nc4.Dataset(path, "w", format="NETCDF3_CLASSIC") as dst:
        dst.setncatts({"Conventions": SFLUX_CONVENTIONS})
        dst.createDimension("nx_grid", nx)
        dst.createDimension("ny_grid", ny)
        dst.createDimension("time", None)   # unlimited

        # lon
        v = dst.createVariable("lon", "f4", ("ny_grid", "nx_grid"))
        v.long_name = "Longitude"; v.standard_name = "longitude"
        v.units = "degrees_east"
        v[:] = lon2d

        # lat
        v = dst.createVariable("lat", "f4", ("ny_grid", "nx_grid"))
        v.long_name = "Latitude"; v.standard_name = "latitude"
        v.units = "degrees_north"
        v[:] = lat2d

        # time
        v = dst.createVariable("time", "f4", ("time",))
        v.long_name = "Time"; v.standard_name = "time"
        v.units = f"days since {day_date.year}-{day_date.month}-{day_date.day} 00:00 UTC"
        v.base_date = (day_date.year, day_date.month, day_date.day, 0)
        v[:] = times_days

        # data variables
        var_meta = {
            "prmsl": ("Pressure reduced to MSL",        "air_pressure_at_sea_level",             "Pa"),
            "spfh":  ("Surface Specific Humidity (2m AGL)", "specific_humidity",                  "1"),
            "stmp":  ("Surface Air Temperature (2m AGL)", "air_temperature",                      "K"),
            "uwind": ("Surface Eastward Air Velocity (10m AGL)", "eastward_wind",                "m/s"),
            "vwind": ("Surface Northward Air Velocity (10m AGL)", "northward_wind",              "m/s"),
            "prate": ("Surface Precipitation Rate",      "precipitation_flux",             "kg/m^2/s"),
            "dlwrf": ("Downward Long Wave Radiation Flux", "surface_downwelling_longwave_flux_in_air", "W/m^2"),
            "dswrf": ("Downward Short Wave Radiation Flux", "surface_downwelling_shortwave_flux_in_air", "W/m^2"),
        }
        for varname, arr in data.items():
            meta = var_meta[varname]
            v = dst.createVariable(varname, "f4", ("time", "ny_grid", "nx_grid"))
            v.long_name = meta[0]; v.standard_name = meta[1]; v.units = meta[2]
            v[:] = arr


def gen_sflux_month(cfg: dict, ym: str):
    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    year  = int(ym[:4])
    month = int(ym[4:])
    ndays = monthrange(year, month)[1]

    raw_path  = mdir / "raw" / "era5" / str(year) / f"era5_{ym}.nc"
    sflux_dir = mdir / f"I{pid}" / f"I{pid}_{ym}" / "sflux"
    sentinel  = sflux_dir / "gen_sflux.done"

    if sentinel.exists():
        print(f"  gen_sflux: {ym} already complete (sentinel found). Skipping.")
        return

    if not raw_path.exists():
        print(f"ERROR: raw ERA5 file not found: {raw_path}")
        sys.exit(1)

    sflux_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n--- gen_sflux {ym} -> {sflux_dir} ---")

    with nc4.Dataset(raw_path) as ds:
        # Coordinates — ERA5 returns lat in descending order; flip to ascending
        lons_1d = ds.variables["longitude"][:]   # 0..360
        lats_1d = ds.variables["latitude"][::-1] # ascending
        lon2d, lat2d = np.meshgrid(lons_1d, lats_1d)

        times_nc  = ds.variables["valid_time"]
        all_times = nc4.num2date(times_nc[:], units=times_nc.units,
                                 only_use_cftime_datetimes=False)

        for iday in range(1, ndays + 1):
            day_date = date(year, month, iday)

            # Find the 25-hour window for this day (00Z → next day 00Z)
            day_str = day_date.strftime("%Y-%m-%d")
            idx_start = None
            for i, t in enumerate(all_times):
                if (t.year == year and t.month == month and
                        t.day == iday and t.hour == 0):
                    idx_start = i
                    break

            if idx_start is None:
                print(f"  WARNING: could not find 00Z for {day_str} in ERA5 file, skipping.")
                continue

            # 25 timesteps: 00Z day N to 00Z day N+1
            idx_end = idx_start + 25
            # If last day of file, we may only have 24 hours — pad with last value
            actual_end = min(idx_end, len(all_times))
            sl = slice(idx_start, actual_end)

            # Read raw fields (flip lat to match ascending order)
            u10  = ds.variables["u10"][sl, ::-1, :].astype(np.float32)
            v10  = ds.variables["v10"][sl, ::-1, :].astype(np.float32)
            msl  = ds.variables["msl"][sl, ::-1, :].astype(np.float32)
            t2m  = ds.variables["t2m"][sl, ::-1, :].astype(np.float32)
            d2m  = ds.variables["d2m"][sl, ::-1, :].astype(np.float32)

            # Precipitation: try both variable name conventions
            prc_var = "mtpr" if "mtpr" in ds.variables else \
                      "avg_tprate" if "avg_tprate" in ds.variables else None
            if prc_var:
                mtpr = ds.variables[prc_var][sl, ::-1, :].astype(np.float32)
            else:
                mtpr = np.zeros_like(u10)
                print(f"  WARNING: precipitation variable not found for {day_str}")

            # Radiation: try both variable name conventions
            lw_var = "msdwlwrf" if "msdwlwrf" in ds.variables else \
                     "avg_sdlwrf" if "avg_sdlwrf" in ds.variables else None
            sw_var = "msdwswrf" if "msdwswrf" in ds.variables else \
                     "avg_sdswrf" if "avg_sdswrf" in ds.variables else None

            dlwrf = ds.variables[lw_var][sl, ::-1, :].astype(np.float32) if lw_var else np.zeros_like(u10)
            dswrf = ds.variables[sw_var][sl, ::-1, :].astype(np.float32) if sw_var else np.zeros_like(u10)

            if lw_var is None:
                print(f"  WARNING: longwave radiation variable not found for {day_str}")
            if sw_var is None:
                print(f"  WARNING: shortwave radiation variable not found for {day_str}")

            # Pad to 25 if needed (last day of ERA5 file may only have 24 steps)
            n_actual = u10.shape[0]
            if n_actual < 25:
                def pad(arr):
                    last = arr[-1:, :, :]
                    return np.concatenate([arr] + [last] * (25 - n_actual), axis=0)
                u10 = pad(u10); v10 = pad(v10); msl = pad(msl)
                t2m = pad(t2m); d2m = pad(d2m); mtpr = pad(mtpr)
                dlwrf = pad(dlwrf); dswrf = pad(dswrf)

            spfh = dewpoint_to_spfh(d2m, msl)

            # Time axis: days since midnight of this day (0/24 ... 24/24)
            times_days = np.array([h / 24.0 for h in range(25)], dtype=np.float32)

            # Stack index (1-based, unpadded — current SCHISM convention)
            stack = str(iday)

            # Write sflux_air
            write_sflux_file(
                sflux_dir / f"sflux_air_1.{stack}.nc", "air", day_date,
                lon2d, lat2d, times_days,
                {"prmsl": msl, "spfh": spfh, "stmp": t2m,
                 "uwind": u10, "vwind": v10})

            # Write sflux_prc
            write_sflux_file(
                sflux_dir / f"sflux_prc_1.{stack}.nc", "prc", day_date,
                lon2d, lat2d, times_days,
                {"prate": mtpr})

            # Write sflux_rad
            write_sflux_file(
                sflux_dir / f"sflux_rad_1.{stack}.nc", "rad", day_date,
                lon2d, lat2d, times_days,
                {"dlwrf": dlwrf, "dswrf": dswrf})

            print(f"  {day_str}: air+prc+rad written (stack {stack})")

    # Write sflux_inputs.txt
    start = date(year, month, 1)
    sflux_inputs = f"""&sflux_inputs
start_year  = {start.year}
start_month = {start.month}
start_day   = {start.day}
start_hour  = 0.
utc_start   = 0.
air_1_relative_weight = 1.
air_1_max_window_hours = 120.
air_1_fail_if_missing = .true.
air_1_file = 'sflux_air_1'
rad_1_relative_weight = 1.
rad_1_max_window_hours = 120.
rad_1_fail_if_missing = .true.
rad_1_file = 'sflux_rad_1'
prc_1_relative_weight = 1.
prc_1_max_window_hours = 120.
prc_1_fail_if_missing = .true.
prc_1_file = 'sflux_prc_1'
uwind_name = 'uwind'
vwind_name = 'vwind'
prmsl_name = 'prmsl'
stmp_name  = 'stmp'
spfh_name  = 'spfh'
dlwrf_name = 'dlwrf'
dswrf_name = 'dswrf'
prate_name = 'prate'
/
"""
    (sflux_dir / "sflux_inputs.txt").write_text(sflux_inputs)
    print(f"  Written: sflux_inputs.txt")

    sentinel.touch()
    print(f"  Sentinel: {sentinel}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate sflux files for one month")
    parser.add_argument("--config", required=True)
    parser.add_argument("--month",  required=True, help="YYYYMM")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    gen_sflux_month(cfg, args.month)
