"""
download_era5.py
================
Phase 1b (DTN, internet required) — Download ERA5 monthly raw files from the
Copernicus Climate Data Store (CDS) using the cdsapi library.

One raw NetCDF file per month:
    raw/era5/{YYYY}/era5_{YYYYMM}.nc

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

import os
import sys
import socket
import tempfile
from calendar import monthrange
from datetime import date
from pathlib import Path
from zipfile import ZipFile, BadZipFile

import numpy as np

from workflow.config import load_config, list_months, model_dir, ProgressTracker

DTN_HOSTNAME_HINT = "dtn"
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
# Checks
# =============================================================================

def check_dtn():
    hostname = socket.gethostname()
    if DTN_HOSTNAME_HINT in hostname.lower():
        print(f"  Host check: '{hostname}' (DTN). OK.")
        return
    if os.environ.get("ALLOW_NON_DTN") == "1":
        print(f"  Host check: not a DTN but ALLOW_NON_DTN=1 set. Proceeding.")
        return
    print(f"ERROR: '{hostname}' is not a DTN. ERA5 download needs internet.")
    print("  ssh hercules-dtn.hpc.msstate.edu && conda activate swf_main")
    sys.exit(1)


def check_cdsapi():
    try:
        import cdsapi  # noqa: F401
    except ImportError:
        print("ERROR: cdsapi not installed. Run: conda install -c conda-forge cdsapi")
        sys.exit(1)
    cdsapirc = Path.home() / ".cdsapirc"
    if not cdsapirc.exists():
        print(f"ERROR: {cdsapirc} not found.")
        print("  Create it with your CDS credentials:")
        print("  url: https://cds.climate.copernicus.eu/api")
        print("  key: <uid>:<api-key>")
        sys.exit(1)
    print("  CDS API check: ~/.cdsapirc found. OK.")


def check_active_env(cfg: dict):
    expected = cfg.get("conda_envs", {}).get("download_era5", "swf_main")
    active   = os.environ.get("CONDA_DEFAULT_ENV", "")
    if active == expected:
        print(f"  Env check: '{active}' matches config. OK.")
    else:
        print(f"  {'='*56}")
        print(f"  WARNING: wrong conda environment for download_era5.")
        print(f"     active:   '{active or '(none)'}'")
        print(f"     expected: '{expected}'")
        print(f"  Activate:  conda activate {expected}")
        print(f"  {'='*56}")


# =============================================================================
# Stale-data check
# =============================================================================

def stale_check_era5(nc_path: Path, var: str = "u10") -> bool:
    """
    Return True if the ERA5 file looks stale (all time steps identical).
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
    """Return True if path is a zip archive (CDS API sometimes returns zips)."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"PK\x03\x04"
    except Exception:
        return False


def _unzip_and_merge(zip_path: Path, out_nc: Path):
    """
    The new CDS API can return a zip of separate per-variable NetCDF files.
    Unzip into a temp directory, merge all NetCDF files with xarray, and
    write the merged result to out_nc. Handles variable renaming for the
    new CDS Beta API (same as pyschism).
    """
    import xarray as xr

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        print(f"  ZIP detected — extracting and merging variables...")
        with ZipFile(zip_path, "r") as zf:
            zf.extractall(tmpdir)
            nc_files = list(tmpdir.glob("*.nc"))

        if not nc_files:
            raise RuntimeError("ZIP contained no .nc files")

        # Open and merge all variable files
        datasets = [xr.open_dataset(f) for f in nc_files]
        merged   = xr.merge(datasets)

        # New CDS Beta API renames some variables — normalise to old names
        rename_map = {
            "avg_tprate":   "mtpr",
            "avg_sdlwrf":   "msdwlwrf",
            "avg_sdswrf":   "msdwswrf",
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
    """Download one month of ERA5 data via the CDS API."""
    year  = int(ym[:4])
    month = int(ym[4:])
    ndays = monthrange(year, month)[1]

    lon_min = float(cfg["lon_min"]); lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"]); lat_max = float(cfg["lat_max"])
    buf = 0.5

    # CDS area: [North, West, South, East]. East > 180 is accepted for
    # 0-360 domains (same approach as pyschism).
    area = [lat_max + buf, lon_min - buf, lat_min - buf, lon_max + buf]

    print(f"  Requesting ERA5 for {ym} ({year}-{month:02d}-01 to {year}-{month:02d}-{ndays:02d})")
    print(f"  Area: N={area[0]} W={area[1]} S={area[2]} E={area[3]}")

    request = {
        "variable": CDS_VARIABLES,
        "product_type": "reanalysis",
        "year": str(year),
        "month": f"{month:02d}",
        "day": [f"{d:02d}" for d in range(1, ndays + 1)],
        "time": [f"{h:02d}:00" for h in range(24)],
        "area": area,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }

    # Download to a temporary file first so we can inspect the format
    raw_tmp = out_path.parent / f"{out_path.stem}.raw.tmp"
    raw_tmp.unlink(missing_ok=True)

    client.retrieve("reanalysis-era5-single-levels", request, str(raw_tmp))

    if not (raw_tmp.exists() and raw_tmp.stat().st_size > 0):
        raise RuntimeError(f"CDS returned empty file for {ym}")

    # Handle zip vs plain NetCDF (new CDS API sometimes returns a zip even
    # when download_format='unarchived')
    if _is_zip(raw_tmp):
        try:
            _unzip_and_merge(raw_tmp, out_path)
            raw_tmp.unlink(missing_ok=True)
        except (BadZipFile, Exception) as exc:
            raw_tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to unzip/merge CDS response: {exc}")
    else:
        raw_tmp.replace(out_path)

    print(f"  Downloaded: {out_path.name}  ({out_path.stat().st_size // 1024 // 1024} MB)")


# =============================================================================
# Main
# =============================================================================

def run_download_era5(cfg: dict):
    check_dtn()
    check_cdsapi()
    check_active_env(cfg)

    import cdsapi

    pid     = cfg["project_id"]
    mdir    = model_dir(cfg)
    months  = list_months(cfg)

    print(f"\n{'='*60}")
    print(f"  ERA5 download: {months[0]} -> {months[-1]}")
    print(f"  Domain: lon [{cfg['lon_min']}, {cfg['lon_max']}]  "
          f"lat [{cfg['lat_min']}, {cfg['lat_max']}]")
    print(f"  Raw output: {mdir}/raw/era5/")
    print(f"{'='*60}\n")

    client = cdsapi.Client()
    prog   = ProgressTracker(total=len(months), label="ERA5 download")
    failed = []

    for ym in months:
        year     = int(ym[:4])
        era5_dir = mdir / "raw" / "era5" / str(year)
        era5_dir.mkdir(parents=True, exist_ok=True)
        out_path = era5_dir / f"era5_{ym}.nc"

        # Skip if valid file already exists
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

        # Stale-data check
        print(f"  Stale-data check for {ym}...")
        if stale_check_era5(out_path):
            print(f"\n{'='*60}")
            print(f"  STOPPING: {ym} ERA5 data is stale (all timesteps identical).")
            print(f"  This may indicate a CDS API issue. Investigate and re-run.")
            print(f"{'='*60}\n")
            out_path.unlink(missing_ok=True)
            sys.exit(1)
        print(f"  Stale-data check passed for {ym}.")
        prog.update(ym)

    print(f"\n{'='*60}")
    if not failed:
        print("  ERA5 download complete. No failures.")
    else:
        print(f"  ERA5 download complete with {len(failed)} failure(s):")
        for m in failed:
            print(f"    {m}")
        print("  Re-run to retry (existing valid files are skipped).")
    print(f"{'='*60}\n")
