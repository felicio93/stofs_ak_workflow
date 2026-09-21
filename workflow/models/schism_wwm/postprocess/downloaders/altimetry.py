"""
models/schism_wwm/postprocess/downloaders/altimetry.py
=======================================================
Phase 5 step "download_altimetry" (DTN, internet required).

Downloads satellite altimetry data for collocation against SCHISM+WWM
significant wave height (Hs). Supports two sources:

  CCI (default)
      ESA CCI Sea State v5 from IFREMER FTP. Requires credentials stored
      as environment variables:
          export CCI_FTP_USER="your_username"
          export CCI_FTP_PASS="your_password"
      Register at https://eftp.ifremer.fr (free).
      No SWH cap; stable long-term archive; includes quality flags.

  CoastWatch
      NOAA STAR CoastWatch daily merged files. No credentials required.
      SWH capped at 8 m; old data may be deleted without notice.

Output directory:
    M{ID}/obs/altimetry/{source}/

Longitude convention
--------------------
The project domain (domain.yaml) may use lon_reference "360" (0..360),
which means lon_max can exceed 180 (e.g. 230 for the Bering Sea domain).
OCSTrack's crop_by_box uses -180..180 and supports dateline-crossing boxes
when lon_min > lon_max (OR mask). We therefore convert the domain bounds
to -180..180 before passing to OCSTrack — exactly the same approach used
for Argo float downloads.

For the Alaska domain (lon_min=150, lon_max=230):
    150 -> 150   (unchanged, already in -180..180)
    230 -> -130  (230 - 360 = -130)
    -> lon_min=150 > lon_max=-130 -> dateline-crossing OR mask
    -> captures lon >= 150 OR lon <= -130 (correct)

Resume-safe: OCSTrack skips already-downloaded raw files internally.

CCI altimeter list (all selected by default when altimetry_sat_list is null):
    cfosat, cryosat-2, envisat, ers-1, ers-2, gfo,
    jason-1, jason-2, jason-3, saral, sentinel-3a, sentinel-3b,
    sentinel-6a, swot, topex-poseidon_poseidon, topex-poseidon_topex

CoastWatch altimeter list (all selected by default):
    sentinel3a, sentinel3b, jason3
"""

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from workflow.core.config import load_config, model_dir
from workflow.core.environment import check_dtn, check_active_env

# =============================================================================
# Satellite lists
# =============================================================================

CCI_ALL_SATELLITES = [
    "cfosat",
    "cryosat-2",
    "envisat",
    "ers-1",
    "ers-2",
    "gfo",
    "jason-1",
    "jason-2",
    "jason-3",
    "saral",
    "sentinel-3a",
    "sentinel-3b",
    "sentinel-6a",
    "swot",
    "topex-poseidon_poseidon",
    "topex-poseidon_topex",
]

COASTWATCH_ALL_SATELLITES = [
    "sentinel3a",
    "sentinel3b",
    "jason3",
]


# =============================================================================
# Longitude conversion helpers
# =============================================================================

def _to_180(lon: float) -> float:
    """Map a single longitude value to the -180..180 frame."""
    lon = float(lon) % 360.0
    if lon > 180.0:
        lon -= 360.0
    return lon


def _domain_box_180(cfg: dict) -> tuple:
    """Return (lat_min, lat_max, lon_min, lon_max) in the -180..180 frame.

    For a domain that crosses the 180 meridian (e.g. lon_min=150,
    lon_max=230 in 0..360), this returns lon_min=150, lon_max=-130.
    OCSTrack's crop_by_box interprets lon_min > lon_max as a
    dateline-crossing OR mask:
        lon >= lon_min OR lon <= lon_max
    which correctly captures both sides of the 180 meridian.
    """
    lat_min = float(cfg["lat_min"])
    lat_max = float(cfg["lat_max"])
    lon_min_180 = _to_180(cfg["lon_min"])
    lon_max_180 = _to_180(cfg["lon_max"])
    return lat_min, lat_max, lon_min_180, lon_max_180


# =============================================================================
# Helpers
# =============================================================================

def _out_dir(cfg: dict) -> Path:
    """Return the altimetry observation directory."""
    source = str(cfg.get("altimetry_source", "cci")).lower()
    return model_dir(cfg) / "obs" / "altimetry" / source


def _sat_list(cfg: dict) -> list:
    """Return the satellite list from config, or all for the source."""
    source  = str(cfg.get("altimetry_source", "cci")).lower()
    sat_cfg = cfg.get("altimetry_sat_list")
    if sat_cfg:
        return [str(s) for s in sat_cfg]
    if source == "cci":
        return CCI_ALL_SATELLITES
    return COASTWATCH_ALL_SATELLITES


def _check_cci_credentials() -> tuple:
    """Read CCI credentials from environment variables."""
    user = os.environ.get("CCI_FTP_USER", "").strip()
    pwd  = os.environ.get("CCI_FTP_PASS", "").strip()
    if not user or not pwd:
        print("ERROR: CCI credentials not found in environment.")
        print("  Set them before running download_altimetry:")
        print("    export CCI_FTP_USER='your_username'")
        print("    export CCI_FTP_PASS='your_password'")
        print("  Register at https://eftp.ifremer.fr (free).")
        sys.exit(1)
    return user, pwd


# =============================================================================
# Download implementations
# =============================================================================

def _download_cci(cfg: dict, out_dir: Path):
    """Download CCI Sea State data for the project date range."""
    try:
        from ocstrack.Observation.get_sat_cci import (
            get_multi_sat_cci,
        )
    except ImportError:
        print("ERROR: ocstrack is not installed. "
              "Run: pip install ocstrack")
        sys.exit(1)

    start_str = str(cfg["start_date"])
    end_str   = str(cfg["end_date"])
    sat_list  = _sat_list(cfg)
    user, pwd = _check_cci_credentials()

    # Convert domain bounds to -180..180 for OCSTrack.
    # For dateline-crossing domains (e.g. Alaska 150..230),
    # lon_min_180=150 > lon_max_180=-130 triggers OCSTrack's
    # OR mask: lon >= 150 OR lon <= -130.
    lat_min, lat_max, lon_min_180, lon_max_180 = (
        _domain_box_180(cfg))

    print(f"  Source       : CCI (IFREMER FTP)")
    print(f"  Satellites   : {sat_list}")
    print(f"  Period       : {start_str} -> {end_str}")
    print(f"  Domain (cfg) : lon [{cfg['lon_min']}, "
          f"{cfg['lon_max']}]  "
          f"lat [{cfg['lat_min']}, {cfg['lat_max']}]")
    print(f"  Domain (-180..180): "
          f"lon [{lon_min_180}, {lon_max_180}]  "
          f"lat [{lat_min}, {lat_max}]"
          + ("  (dateline-crossing)"
             if lon_min_180 > lon_max_180 else ""))
    print(f"  Output       : {out_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)

    get_multi_sat_cci(
        start_date=start_str,
        end_date=end_str,
        sat_list=sat_list,
        output_dir=str(out_dir),
        ftp_user=user,
        ftp_pass=pwd,
        lat_min=lat_min,
        lat_max=lat_max,
        lon_min=lon_min_180,
        lon_max=lon_max_180,
    )


def _download_coastwatch(cfg: dict, out_dir: Path):
    """Download CoastWatch satellite altimetry data."""
    try:
        from ocstrack.Observation.get_sat_coastwatch import (
            get_multi_sat_coastwatch,
        )
    except ImportError:
        print("ERROR: ocstrack is not installed. "
              "Run: pip install ocstrack")
        sys.exit(1)

    start_str = str(cfg["start_date"])
    end_str   = str(cfg["end_date"])
    sat_list  = _sat_list(cfg)

    # Convert domain bounds to -180..180 for OCSTrack.
    lat_min, lat_max, lon_min_180, lon_max_180 = (
        _domain_box_180(cfg))

    print(f"  Source       : CoastWatch (NOAA STAR)")
    print(f"  Satellites   : {sat_list}")
    print(f"  Period       : {start_str} -> {end_str}")
    print(f"  Domain (cfg) : lon [{cfg['lon_min']}, "
          f"{cfg['lon_max']}]  "
          f"lat [{cfg['lat_min']}, {cfg['lat_max']}]")
    print(f"  Domain (-180..180): "
          f"lon [{lon_min_180}, {lon_max_180}]  "
          f"lat [{lat_min}, {lat_max}]"
          + ("  (dateline-crossing)"
             if lon_min_180 > lon_max_180 else ""))
    print(f"  Output       : {out_dir}")
    print(f"  NOTE: CoastWatch SWH is capped at 8 m.")

    out_dir.mkdir(parents=True, exist_ok=True)

    get_multi_sat_coastwatch(
        start_date=start_str,
        end_date=end_str,
        sat_list=sat_list,
        output_dir=str(out_dir),
        lat_min=lat_min,
        lat_max=lat_max,
        lon_min=lon_min_180,
        lon_max=lon_max_180,
    )


# =============================================================================
# Main entry point
# =============================================================================

def run_download_altimetry(cfg: dict):
    """Download satellite altimetry data (DTN step)."""
    check_dtn("Satellite altimetry download")
    check_active_env(cfg, "download_altimetry")

    source  = str(cfg.get(
        "altimetry_source", "cci")).lower()
    out_dir = _out_dir(cfg)

    print(f"\n{'='*60}")
    print(f"  Satellite altimetry download")

    if source == "cci":
        _download_cci(cfg, out_dir)
    elif source == "coastwatch":
        _download_coastwatch(cfg, out_dir)
    else:
        print(f"ERROR: unknown altimetry_source "
              f"'{source}'. "
              f"Valid options: 'cci', 'coastwatch'")
        sys.exit(1)

    print(f"\n  Download complete -> {out_dir}")
    print(f"{'='*60}\n")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download satellite altimetry data")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    run_download_altimetry(
        load_config(Path(args.config)))
