"""
downloaders/era5.py
================
Phase 1b (DTN, internet required) — Download ERA5 monthly raw files from the
Copernicus Climate Data Store (CDS) using the cdsapi library.

One raw NetCDF file per calendar month:
    raw/era5/{YYYY}/era5_{YYYYMM}.nc

ERA5 is always downloaded by calendar month regardless of the project
grouping setting. For ndays grouping the function collects the unique
calendar months that span all groups before downloading.

Variables downloaded (ERA5 hourly single-level reanalysis):
    10m_u_component_of_wind         -> u10
    10m_v_component_of_wind         -> v10
    mean_sea_level_pressure         -> msl
    2m_temperature                  -> t2m
    2m_dewpoint_temperature         -> d2m  (used to compute spfh)
    mean_total_precipitation_rate   -> mtpr (or avg_tprate in newer API)
    mean_surface_downward_long_wave_radiation_flux   -> msdwlwrf
    mean_surface_downward_short_wave_radiation_flux  -> msdwswrf

Domain: same bbox as the HYCOM download (lon_min/max, lat_min/max from
domain.yaml) with a 0.5-degree buffer. CDS accepts east longitudes > 180,
so the Bering Sea domain (150-230E) is handled with a single request — no
split needed (same approach as pyschism).

Resume-safe: skips months whose output file already exists and is non-empty.
Stale-data check: after each month, compares the field mean of the first and
last day's data. Identical means indicate a server-side issue (e.g. API
returning repeated fields). Stops immediately on stale detection.

Requires:
    ~/.cdsapirc   on the DTN with CDS API credentials:
        url: https://cds.climate.copernicus.eu/api
        key: <uid>:<api-key>
"""

import sys
import tempfile
from calendar import monthrange
from datetime import date
from pathlib import Path
from zipfile import ZipFile, BadZipFile

import numpy as np

from workflow.core.config import (
    load_config,
    list_groups,
    group_date_range,
    model_dir,
    ProgressTracker,
)
from workflow.core.environment import (
    check_dtn,
    check_cdsapi,
    check_active_env,
)

CDS_VARIABLES = [
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "mean_sea_level_pressure",
    "2m_temperature",
    "2m_dewpoint_temperature",
    "mean_total_precipitation_rate",
    "mean_surface_downward_long_wave_radiation_flux",
    "mean_surface_downward_short_wave_radiation_flux",
]


# =============================================================================
# Helper: collect unique calendar months from all groups
# =============================================================================

def _calendar_months_for_groups(cfg: dict) -> list:
    """Return sorted list of 'YYYYMM' strings covering all groups.

    ERA5 raw files are always one file per calendar month. For ndays
    grouping a single group may touch two calendar months (e.g. a
    7-day group starting 2025-09-29 spans Sep and Oct). This function
    collects every calendar month touched by any group so that the full
    ERA5 coverage needed by gen_sflux is available.

    Returns a sorted list of unique 'YYYYMM' strings.
    """
    from datetime import timedelta
    from dateutil.relativedelta import relativedelta

    groups = list_groups(cfg)
    seen   = set()

    for gid in groups:
        gstart, gend = group_date_range(cfg, gid)
        # Walk calendar months from group start to group end
        cur = date(gstart.year, gstart.month, 1)
        end = date(gend.year,   gend.month,   1)
        while cur <= end:
            seen.add(cur.strftime("%Y%m"))
            cur += relativedelta(months=1)

    return sorted(seen)


# =============================================================================
# Stale-data check
# =============================================================================

def stale_check_era5(nc_path: Path, var: str = "u10") -> bool:
    """Return True if the ERA5 file looks stale (all time steps identical).
    Compares the spatial mean of the first vs last time record.
    """
    try:
        import netCDF4 as nc4
        with nc4.Dataset(nc_path) as ds:
            v = ds.variables.get(var)
            if v is None:
                return False
            ntime = v.shape[0]
            if ntime < 2:
                return False
            first = float(np.ma.filled(v[0, :, :], np.nan).mean())
            last  = float(np.ma.filled(v[-1, :, :], np.nan).mean())
            if not (np.isfinite(first) and np.isfinite(last)):
                return False
            return abs(first - last) < 1e-9
    except Exception:
        return False


# =============================================================================
# Download
# =============================================================================

def _is_zip(path: Path) -> bool:
    """Return True if path is a zip archive."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"PK\x03\x04"
    except Exception:
        return False


def _unzip_and_merge(zip_path: Path, out_nc: Path):
    """Unzip a CDS zip response and merge all NetCDF files into one."""
    import xarray as xr

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        print("  ZIP detected — extracting and merging variables...")
        with ZipFile(zip_path, "r") as zf:
            zf.extractall(tmpdir)
            nc_files = list(tmpdir.glob("*.nc"))

        if not nc_files:
            raise RuntimeError("ZIP contained no .nc files")

        datasets = [xr.open_dataset(f) for f in nc_files]
        merged   = xr.merge(datasets, compat="override")

        rename_map = {
            "avg_tprate": "mtpr",
            "avg_sdlwrf": "msdwlwrf",
            "avg_sdswrf": "msdwswrf",
        }
        actual_renames = {k: v for k, v in rename_map.items()
                          if k in merged.data_vars}
        if actual_renames:
            merged = merged.rename(actual_renames)

        merged.to_netcdf(str(out_nc))
        for ds in datasets:
            ds.close()

    print(f"  Merged {len(nc_files)} variable file(s) -> {out_nc.name}")


def download_month(client, ym: str, out_path: Path, cfg: dict):
    """Download one calendar month of ERA5 data via the CDS API."""
    year  = int(ym[:4])
    month = int(ym[4:])
    ndays = monthrange(year, month)[1]

    lon_min = float(cfg["lon_min"]); lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"]); lat_max = float(cfg["lat_max"])
    buf = 0.5

    area = [lat_max + buf, lon_min - buf,
            lat_min - buf, lon_max + buf]

    print(f"  Requesting ERA5 for {ym} "
          f"({year}-{month:02d}-01 to "
          f"{year}-{month:02d}-{ndays:02d})")
    print(f"  Area: N={area[0]} W={area[1]} "
          f"S={area[2]} E={area[3]}")

    request = {
        "variable":        CDS_VARIABLES,
        "product_type":    "reanalysis",
        "year":            str(year),
        "month":           f"{month:02d}",
        "day":             [f"{d:02d}" for d in range(1, ndays + 1)],
        "time":            [f"{h:02d}:00" for h in range(24)],
        "area":            area,
        "data_format":     "netcdf",
        "download_format": "unarchived",
    }

    raw_tmp = out_path.parent / f"{out_path.stem}.raw.tmp"
    raw_tmp.unlink(missing_ok=True)

    client.retrieve("reanalysis-era5-single-levels",
                    request, str(raw_tmp))

    if not (raw_tmp.exists() and raw_tmp.stat().st_size > 0):
        raise RuntimeError(f"CDS returned empty file for {ym}")

    if _is_zip(raw_tmp):
        try:
            _unzip_and_merge(raw_tmp, out_path)
            raw_tmp.unlink(missing_ok=True)
        except BadZipFile as exc:
            raw_tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"CDS response is not a valid zip: {exc}")
        except Exception as exc:
            raw_tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"Failed to unzip/merge CDS response: {exc}")
    else:
        raw_tmp.replace(out_path)

    print(f"  Downloaded: {out_path.name}  "
          f"({out_path.stat().st_size // 1024 // 1024} MB)")


# =============================================================================
# Main
# =============================================================================

def run_download_era5(cfg: dict):
    check_dtn("ERA5 download")
    check_cdsapi("https://cds.climate.copernicus.eu/api")
    check_active_env(cfg, "download_era5")

    import cdsapi

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)

    # Collect the unique calendar months needed to cover all groups.
    # For monthly grouping this is identical to the original behaviour.
    # For ndays grouping it adds any extra months touched by groups
    # that straddle a month boundary.
    months  = _calendar_months_for_groups(cfg)
    groups  = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")

    print(f"\n{'='*60}")
    print(f"  ERA5 download")
    print(f"  Grouping : {grouping}"
          + (f"  (group_ndays={cfg['group_ndays']})"
             if grouping == "ndays" else ""))
    print(f"  Groups   : {groups[0]} -> {groups[-1]}")
    print(f"  Calendar months to download: "
          f"{months[0]} -> {months[-1]}  "
          f"({len(months)} month(s))")
    print(f"  Domain   : lon [{cfg['lon_min']}, {cfg['lon_max']}]  "
          f"lat [{cfg['lat_min']}, {cfg['lat_max']}]")
    print(f"  Raw output: {mdir}/raw/era5/")
    print(f"{'='*60}\n")

    client = cdsapi.Client()
    prog   = ProgressTracker(
        total=len(months), label="ERA5 download")
    failed = []

    for ym in months:
        year     = int(ym[:4])
        era5_dir = mdir / "raw" / "era5" / str(year)
        era5_dir.mkdir(parents=True, exist_ok=True)
        out_path = era5_dir / f"era5_{ym}.nc"

        if out_path.exists() and out_path.stat().st_size > 0:
            print(f"\n--- {ym}: already downloaded, skipping.")
            prog.update(ym)
            continue

        print(f"\n--- Downloading {ym} ---")
        try:
            download_month(client, ym, out_path, cfg)
        except Exception as exc:
            print(f"  ERROR: download failed for {ym}: {exc}")
            failed.append(ym)
            prog.update(ym)
            continue

        print(f"  Stale-data check for {ym}...")
        if stale_check_era5(out_path):
            print(f"\n{'='*60}")
            print(f"  STOPPING: {ym} ERA5 data is stale "
                  f"(all timesteps identical).")
            print(f"  This may indicate a CDS API issue. "
                  f"Investigate and re-run.")
            print(f"{'='*60}\n")
            out_path.unlink(missing_ok=True)
            sys.exit(1)
        print(f"  Stale-data check passed for {ym}.")
        prog.update(ym)

    print(f"\n{'='*60}")
    if not failed:
        print("  ERA5 download complete. No failures.")
    else:
        print(f"  ERA5 download complete with "
              f"{len(failed)} failure(s):")
        for m in failed:
            print(f"    {m}")
        print("  Re-run to retry (existing valid files are "
              "skipped).")
    print(f"{'='*60}\n")
