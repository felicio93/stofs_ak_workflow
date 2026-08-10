"""
downloaders/glofas.py
==================
Phase 1c (DTN, internet required) — Download GloFAS v4.0 reanalysis river
discharge data from the Copernicus Emergency Management Service Early Warning
Data Store (EWDS) and save one raw NetCDF file per year:

    raw/glofas/{YYYY}/glofas_{YYYY}.nc

Variable downloaded:
    average_river_discharge_in_the_last_24_hours  (dis24, m³/s)

Domain: clipped to the Alaska bounding box from domain.yaml (lon_min/max,
lat_min/max) with a small buffer. GloFAS uses 0.05° resolution; the spatial
subset is specified as [North, West, South, East] following the CDS convention.

GloFAS uses east-only longitudes (0–360), same convention as the AK model, so
no wrapping is needed.

Resume-safe: skips years whose output file already exists and is non-empty.
Stale-data check: verifies the first and last daily fields are not identical
(a known CDS API artefact for some datasets).

NOTE: GloFAS is served from the EWDS (ewds.climate.copernicus.eu), not the
standard CDS. Requires ~/.cdsapirc pointing to the EWDS endpoint:
    url: https://ewds.climate.copernicus.eu/api
    key: <your-api-key>
"""

import sys
from pathlib import Path

from workflow.core.config import model_dir, ProgressTracker
from workflow.core.environment import check_dtn, check_cdsapi, check_active_env

EWDS_DATASET = "cems-glofas-historical"
GLOFAS_VARIABLE = "average_river_discharge_in_the_last_24_hours"
EWDS_URL = "https://ewds.climate.copernicus.eu/api"


# =============================================================================
# Stale-data check
# =============================================================================

def stale_check_glofas(nc_path: Path) -> bool:
    """
    Return True if the GloFAS file looks stale (all daily fields identical).
    Compares the spatial mean of the first vs last time record.
    The variable is 'avg_dis' in GloFAS v4.0+ NetCDF output.
    """
    try:
        import numpy as np
        import netCDF4 as nc4
        with nc4.Dataset(nc_path) as ds:
            # GloFAS v4.0+ uses 'avg_dis'; older versions used 'dis24'
            v = ds.variables.get("avg_dis") or ds.variables.get("dis24")
            if v is None:
                return False
            ntime = v.shape[0]
            if ntime < 2:
                return False
            import numpy.ma as ma
            first = float(ma.filled(v[0, :, :], fill_value=np.nan).mean())
            last  = float(ma.filled(v[-1, :, :], fill_value=np.nan).mean())
            if not (np.isfinite(first) and np.isfinite(last)):
                return False
            return abs(first - last) < 1e-9
    except Exception:
        return False


# =============================================================================
# Download one year
# =============================================================================

def download_year(client, year: int, out_path: Path, cfg: dict):
    """Download one year of GloFAS reanalysis discharge via the EWDS API."""
    lon_min = float(cfg["lon_min"])
    lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"])
    lat_max = float(cfg["lat_max"])
    buf = 0.1  # GloFAS is 0.05° grid; small buffer is sufficient

    # API area order: [North, West, South, East]
    area = [
        round(lat_max + buf, 3),
        round(lon_min - buf, 3),
        round(lat_min - buf, 3),
        round(lon_max + buf, 3),
    ]

    months = [f"{m:02d}" for m in range(1, 13)]
    days   = [f"{d:02d}" for d in range(1, 32)]

    print(f"  Requesting GloFAS discharge for {year}")
    print(f"  Area: N={area[0]}  W={area[1]}  S={area[2]}  E={area[3]}")

    request = {
        "system_version":     "version_4_0",
        "hydrological_model": "lisflood",
        "product_type":       "consolidated",
        "variable":           GLOFAS_VARIABLE,
        "timespan":           "time_mean",
        "year":               str(year),
        "month":              months,
        "day":                days,
        "data_format":        "netcdf",
        "download_format":    "unarchived",
        "area":               area,
    }

    tmp_path = out_path.parent / f"{out_path.stem}.tmp.nc"
    tmp_path.unlink(missing_ok=True)

    client.retrieve(EWDS_DATASET, request, str(tmp_path))

    if not (tmp_path.exists() and tmp_path.stat().st_size > 0):
        raise RuntimeError(f"EWDS returned empty file for {year}")

    tmp_path.replace(out_path)
    size_mb = out_path.stat().st_size // 1024 // 1024
    print(f"  Downloaded: {out_path.name}  ({size_mb} MB)")


# =============================================================================
# Main entry point
# =============================================================================

def run_download_glofas(cfg: dict):
    check_dtn("GloFAS download")
    check_cdsapi(EWDS_URL)
    check_active_env(cfg, "download_glofas")
    print(f"  Note: GloFAS uses {EWDS_URL} (overrides url in ~/.cdsapirc)")

    import cdsapi
    from datetime import date

    mdir = model_dir(cfg)

    start_year = date.fromisoformat(cfg["start_date"]).year
    end_year   = date.fromisoformat(cfg["end_date"]).year
    years      = list(range(start_year, end_year + 1))

    print(f"\n{'='*60}")
    print(f"  GloFAS download: {start_year} -> {end_year}  ({len(years)} year(s))")
    print(f"  Domain: lon [{cfg['lon_min']}, {cfg['lon_max']}]  "
          f"lat [{cfg['lat_min']}, {cfg['lat_max']}]")
    print(f"  Raw output: {mdir}/raw/glofas/")
    print(f"  Dataset:    {EWDS_DATASET}  (version_4_0, consolidated)")
    print(f"{'='*60}\n")

    client = cdsapi.Client(url=EWDS_URL)
    prog   = ProgressTracker(total=len(years), label="GloFAS download")
    failed = []

    for year in years:
        glofas_dir = mdir / "raw" / "glofas" / str(year)
        glofas_dir.mkdir(parents=True, exist_ok=True)
        out_path = glofas_dir / f"glofas_{year}.nc"

        if out_path.exists() and out_path.stat().st_size > 0:
            print(f"\n--- {year}: already downloaded, skipping.")
            prog.update(str(year))
            continue

        print(f"\n--- Downloading {year} ---")
        try:
            download_year(client, year, out_path, cfg)
        except Exception as exc:
            print(f"  ERROR: download failed for {year}: {exc}")
            failed.append(year)
            prog.update(str(year))
            continue

        print(f"  Stale-data check for {year}...")
        if stale_check_glofas(out_path):
            print(f"\n{'='*60}")
            print(f"  STOPPING: {year} GloFAS data is stale (all timesteps identical).")
            print(f"  This may indicate an API issue. Investigate and re-run.")
            print(f"{'='*60}\n")
            out_path.unlink(missing_ok=True)
            sys.exit(1)
        print(f"  Stale-data check passed for {year}.")
        prog.update(str(year))

    print(f"\n{'='*60}")
    if not failed:
        print("  GloFAS download complete. No failures.")
    else:
        print(f"  GloFAS download complete with {len(failed)} failure(s):")
        for y in failed:
            print(f"    {y}")
        print("  Re-run to retry (existing valid files are skipped).")
    print(f"{'='*60}\n")
