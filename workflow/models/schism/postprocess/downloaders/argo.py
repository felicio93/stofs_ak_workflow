"""
models/schism/postprocess/downloaders/argo.py
=============================================
Phase 5 step "download_argo" (DTN, internet required).

Downloads Argo float profile data from the IFREMER GDAC via OCSTrack's
``get_argo`` and stores the cleaned / time-filtered / domain-cropped profiles
under:

    M{ID}/obs/argo/{region}/processed/cropped_*.nc

These profiles are later collocated against SCHISM temperature / salinity by
the ``collocate_argo`` step (workflow.models.schism.postprocess.collocate_argo),
which loads them with OCSTrack's ``ArgoData`` class.

Longitude convention
---------------------
The workflow domain (domain.yaml) is expressed with ``lon_reference`` "360"
(Bering Sea; 0..360, 0 at Greenwich) or "180" (-180..180). Argo GDAC data
uses the -180..180 standard, and OCSTrack's ``crop_by_box_argo`` crosses the
dateline when ``lon_min > lon_max``. This module converts the domain bounds to
the -180..180 frame OCSTrack expects, handling the dateline crossing that a
"360" domain spanning the 180 meridian implies (e.g. 150..230 in 0..360 becomes
lon_min=150, lon_max=-130 in -180..180).

Region
------
Argo GDAC serves data per ocean region. The Alaska / Bering Sea domain lives in
``pacific_ocean`` (the default here). Override with ``argo_region`` in
postprocess.yaml if your domain is elsewhere.

Resume-safe: ``get_argo`` skips raw files already downloaded, and this step is
cheap to re-run.
Requires: swf_plot (ocstrack + xarray + netCDF4) on the DTN, internet access.
"""

import argparse
import sys
from pathlib import Path

from workflow.core.config import load_config, model_dir
from workflow.core.environment import check_dtn, check_active_env


def _to_180(lon: float) -> float:
    """Map a longitude in any convention to the -180..180 frame."""
    lon = float(lon) % 360.0
    if lon > 180.0:
        lon -= 360.0
    return lon


def _domain_box_180(cfg: dict):
    """Return (lat_min, lat_max, lon_min, lon_max) in the -180..180 frame that
    OCSTrack's crop_by_box_argo expects.

    A "360" domain that spans the 180 meridian (lon_min < 180 < lon_max) maps
    to lon_min > lon_max in the -180..180 frame, which crop_by_box_argo
    interprets as a dateline-crossing box (an OR mask). Non-crossing domains
    keep lon_min < lon_max as usual.
    """
    lat_min = float(cfg["lat_min"])
    lat_max = float(cfg["lat_max"])
    lon_min_180 = _to_180(cfg["lon_min"])
    lon_max_180 = _to_180(cfg["lon_max"])
    return lat_min, lat_max, lon_min_180, lon_max_180


def run_download_argo(cfg: dict):
    check_dtn("Argo float download")
    check_active_env(cfg, "download_argo")

    try:
        from ocstrack.Observation.get_argo import get_argo
    except ImportError:
        print("ERROR: ocstrack is not installed in the active environment.")
        print("  Install it into swf_plot:  pip install ocstrack")
        print("  or re-run:  stofs-ak --setup-envs --config <cfg>")
        sys.exit(1)

    mdir   = model_dir(cfg)
    region = str(cfg.get("argo_region", "pacific_ocean"))
    start  = str(cfg["start_date"])
    end    = str(cfg["end_date"])

    lat_min, lat_max, lon_min, lon_max = _domain_box_180(cfg)

    out_dir = mdir / "obs" / "argo"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Argo float download (IFREMER GDAC): {start} -> {end}")
    print(f"  Region: {region}")
    print(f"  Domain (-180..180): lon [{lon_min}, {lon_max}]  "
          f"lat [{lat_min}, {lat_max}]"
          + ("  (dateline-crossing)" if lon_min > lon_max else ""))
    print(f"  Output: {out_dir / region / 'processed'}")
    print(f"{'='*60}\n")

    processed = get_argo(
        start_date=start,
        end_date=end,
        region=region,
        output_dir=str(out_dir),
        lat_min=lat_min,
        lat_max=lat_max,
        lon_min=lon_min,
        lon_max=lon_max,
    )

    print(f"\n{'='*60}")
    if processed is None:
        print("  Argo download finished: no profiles found for the domain/period.")
        print("  (The Bering Sea has sparse Argo coverage; try a wider window.)")
    else:
        n = len(list(Path(processed).glob("*.nc")))
        print(f"  Argo download complete. {n} processed profile file(s) in:")
        print(f"    {processed}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Download Argo float profiles")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    run_download_argo(cfg)
