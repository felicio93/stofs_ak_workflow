"""
models/schism/postprocess/downloaders/sst_leo.py
================================================
Phase 5 step "download_sst" (DTN, internet required).

Downloads the NOAA/STAR ACSPO L3S-LEO-DY (Daily) satellite SST product and
subsets it to the project domain, writing one file per day:

    M{ID}/obs/sst_leo/leosst_YYYYMMDD.nc   (vars: lon, lat, sst  in degC)

The L3S-LEO-DY product is a DAILY collated field (all day/night LEO passes
aggregated into one 0.02-degree grid, timestamped 12:00:00Z), one file per
24 h. It is therefore compared against a DAILY-MEAN of SCHISM SST downstream
(compare_sst).

Source URL pattern (per day):
    https://coastwatch.noaa.gov/pub/socd2/coastwatch/sst/ran/l3s/leo/daily/
        {YYYY}/{DOY}/{YYYYMMDD}120000-STAR-L3S_GHRSST-SSTsubskin-LEO_Daily-
        ACSPO_V2.81-v02.0-fv01.0.nc

Subsetting (replaces the hard-coded subset.nco / extract.sh):
  * Convert sea_surface_temperature (K) -> sst (degC).
  * Crop to the domain bbox from domain.yaml (lon_min/max, lat_min/max).
  * Handle the longitude convention: the product uses -180..180; when the
    project uses lon_reference "360" (Bering Sea, 180 meridian in-domain) the
    two longitude halves are shifted to 0..360 and stitched, exactly like the
    original subset.nco did — but computed from the domain bounds rather than
    hard-coded index ranges.

Resume-safe: days already present in obs/sst_leo/ are skipped.
Requires: swf_main (wget + NCO + netCDF4) on the DTN.
"""

import argparse
import subprocess
import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import load_config, model_dir
from workflow.core.environment import check_dtn, check_active_env

BASE_URL = ("https://coastwatch.noaa.gov/pub/socd2/coastwatch/sst/ran/"
            "l3s/leo/daily")
FILE_TMPL = ("{ymd}120000-STAR-L3S_GHRSST-SSTsubskin-LEO_Daily-"
             "ACSPO_V2.81-v02.0-fv01.0.nc")

REQUIRED_TOOLS = ["wget", "ncks", "ncap2", "ncrename", "ncatted"]


def _dates(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _run(cmd, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        last = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "(no stderr)"
        raise RuntimeError(f"command failed: {' '.join(str(c) for c in cmd)}\n  {last}")
    return result


def _download_day(d: date, cfg: dict, obs_dir: Path, tmp_dir: Path) -> bool:
    """Download + subset one day. Returns True on success."""
    import numpy as np

    ymd  = d.strftime("%Y%m%d")
    out  = obs_dir / f"leosst_{ymd}.nc"
    if out.exists() and out.stat().st_size > 0:
        print(f"  {ymd}: already present, skipping.")
        return True

    doy  = d.strftime("%j")
    url  = f"{BASE_URL}/{d.year}/{doy}/{FILE_TMPL.format(ymd=ymd)}"
    raw  = tmp_dir / f"leosst_raw_{ymd}.nc"
    sub  = tmp_dir / f"leosst_sub_{ymd}.nc"
    for f in (raw, sub):
        f.unlink(missing_ok=True)

    # --- download ---
    dl = subprocess.run(["wget", "-q", url, "-O", str(raw)],
                        capture_output=True, text=True)
    if dl.returncode != 0 or not (raw.exists() and raw.stat().st_size > 0):
        print(f"  {ymd}: download failed ({url})")
        raw.unlink(missing_ok=True)
        return False

    # --- subset to domain + convert K->degC + rename coords ---
    lon_min = float(cfg["lon_min"]); lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"]); lat_max = float(cfg["lat_max"])
    lon_ref = str(cfg.get("lon_reference", "360"))

    try:
        _subset_to_domain(raw, sub, lon_min, lon_max, lat_min, lat_max, lon_ref)
    except Exception as exc:
        print(f"  {ymd}: subset failed: {exc}")
        raw.unlink(missing_ok=True); sub.unlink(missing_ok=True)
        return False

    sub.replace(out)
    raw.unlink(missing_ok=True)
    print(f"  {ymd}: downloaded + subset -> {out.name}")
    return True


def _subset_to_domain(raw: Path, out: Path,
                      lon_min, lon_max, lat_min, lat_max, lon_ref):
    """Crop the global L3S-LEO file to the domain, convert K->degC, and write
    a file with variables lon, lat, sst.

    Uses xarray so the longitude 180/360 stitching is expressed by value
    rather than by the hard-coded index ranges in the original subset.nco.
    """
    import numpy as np
    import xarray as xr

    ds = xr.open_dataset(str(raw), engine="netcdf4")

    # The product stores sea_surface_temperature(time, lat, lon) in Kelvin,
    # lon in -180..180 ascending, lat ascending.
    sst_k = ds["sea_surface_temperature"].isel(time=0)
    lon   = ds["lon"].values
    lat   = ds["lat"].values

    # Latitude crop (simple value slice).
    lat_mask = (lat >= lat_min) & (lat <= lat_max)

    if lon_ref == "360":
        # Domain given in 0..360 (e.g. 150..230). Shift product lon to 0..360
        # so the 180-meridian domain is contiguous, then crop by value.
        lon360 = np.where(lon < 0, lon + 360.0, lon)
        order  = np.argsort(lon360)
        lon_s  = lon360[order]
        sst_s  = sst_k.values[:, order]        # (lat, lon) reordered on lon
        lon_mask = (lon_s >= lon_min) & (lon_s <= lon_max)
        lon_out  = lon_s[lon_mask]
        sst_out  = sst_s[np.ix_(lat_mask, lon_mask)]
    else:
        # Domain given in -180..180.
        lon_mask = (lon >= lon_min) & (lon <= lon_max)
        lon_out  = lon[lon_mask]
        sst_out  = sst_k.values[np.ix_(lat_mask, lon_mask)]

    lat_out = lat[lat_mask]
    sst_c   = sst_out.astype("float32") - 273.15   # Kelvin -> degC

    ds.close()

    out_ds = xr.Dataset(
        {"sst": (("time", "lat", "lon"), sst_c[np.newaxis, :, :])},
        coords={"time": [0], "lat": lat_out.astype("float32"),
                "lon": lon_out.astype("float32")},
    )
    out_ds["sst"].attrs.update(units="degC",
                               long_name="sea surface temperature")
    out_ds["lon"].attrs.update(units="degrees_east",  standard_name="longitude")
    out_ds["lat"].attrs.update(units="degrees_north", standard_name="latitude")
    out_ds.to_netcdf(str(out))
    out_ds.close()


def run_download_sst(cfg: dict):
    import shutil
    check_dtn("Satellite SST download")
    check_active_env(cfg, "download_sst")

    missing = [t for t in ("wget",) if shutil.which(t) is None]
    if missing:
        print(f"ERROR: required tool(s) not found on PATH: {missing}")
        sys.exit(1)

    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    start = date.fromisoformat(cfg["start_date"])
    end   = date.fromisoformat(cfg["end_date"])

    obs_dir = mdir / "obs" / "sst_leo"
    tmp_dir = mdir / "obs" / "sst_leo" / "tmp"
    obs_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Satellite SST download (LEO L3S-DY): {start} -> {end}")
    print(f"  Domain: lon [{cfg['lon_min']}, {cfg['lon_max']}]  "
          f"lat [{cfg['lat_min']}, {cfg['lat_max']}]  ref {cfg.get('lon_reference','360')}")
    print(f"  Output: {obs_dir}")
    print(f"{'='*60}\n")

    failed = []
    for d in _dates(start, end):
        try:
            ok = _download_day(d, cfg, obs_dir, tmp_dir)
        except Exception as exc:
            print(f"  {d:%Y%m%d}: ERROR {exc}")
            ok = False
        if not ok:
            failed.append(d.strftime("%Y%m%d"))

    # clean tmp
    try:
        for f in tmp_dir.glob("*"):
            f.unlink()
        tmp_dir.rmdir()
    except OSError:
        pass

    print(f"\n{'='*60}")
    if not failed:
        print("  SST download complete. No failures.")
    else:
        print(f"  SST download complete with {len(failed)} missing day(s):")
        print("   " + ", ".join(failed))
        print("  (Some days may simply be unavailable in the archive.)")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Download LEO L3S-DY satellite SST")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    run_download_sst(cfg)
