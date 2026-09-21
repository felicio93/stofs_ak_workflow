"""
models/schism_wwm/postprocess/collocate_altimetry.py
=====================================================
Phase 5 step "collocate_altimetry".

Two-stage SLURM design (parallel per-day array -> serial merge),
mirroring collocate_argo.py exactly.

Stage 1 — one SLURM array task per calendar day:
    Loads the merged multisat satellite file produced by download_altimetry,
    filters it to the day's time window, and collocates against SCHISM+WWM
    sigWaveHeight from out2d_*.nc using OCSTrack's SCHISM model class +
    Collocate engine.
    Per-day output:
        P{ID}/P{ID}_collocate_altimetry/daily/collocated_hs_{YYYYMMDD}.nc

Stage 2 — serial merge (afterok on Stage 1):
    Concatenates all daily files into one combined NetCDF.
    If collocate_altimetry_dist_threshold_km is set (not null), also writes
    a distance-filtered clean file:
        P{ID}/P{ID}_collocate_altimetry/collocated_hs.nc       <- all points
        P{ID}/P{ID}_collocate_altimetry/collocated_hs_clean.nc <- filtered
    Writes the collocate_altimetry.done sentinel.

Longitude convention:
    CCI/CoastWatch files use -180..180.
    SCHISM+WWM mesh uses 0..360 (lon_reference: "360").
    Mesh longitudes are converted to -180..180 before passing to OCSTrack.

Works for all grouping modes (monthly, ndays/weekly/daily).
"""

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import (
    load_config,
    list_groups,
    group_date_range,
    model_dir,
)


# =============================================================================
# Helpers
# =============================================================================

def _out_dir(cfg: dict) -> Path:
    pid = cfg["project_id"]
    return (model_dir(cfg)
            / f"P{pid}"
            / f"P{pid}_collocate_altimetry")


def _daily_dir(out_dir: Path) -> Path:
    d = out_dir / "daily"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _altimetry_obs_dir(cfg: dict) -> Path:
    source = str(cfg.get(
        "altimetry_source", "cci")).lower()
    return (model_dir(cfg)
            / "obs" / "altimetry" / source)


def _find_merged_sat_file(obs_dir: Path):
    """Find the merged multisat NetCDF file.

    OCSTrack names it:
      CCI:        multisat_cci_cropped_{start}_{end}.nc
      CoastWatch: multisat_coastwatch_cropped_{start}_{end}.nc

    If multiple exist, return the most recently modified.
    Returns None if not found.
    """
    candidates = list(obs_dir.glob(
        "multisat_*.nc"))
    if not candidates:
        return None
    return max(candidates,
               key=lambda p: p.stat().st_mtime)


def _window(cfg: dict) -> tuple:
    start = (cfg.get("collocate_altimetry_start")
             or cfg["start_date"])
    end   = (cfg.get("collocate_altimetry_end")
             or cfg["end_date"])
    return str(start), str(end)


def _build_day_list(cfg: dict) -> list:
    """Return every calendar day in the collocation window."""
    win_start, win_end = _window(cfg)
    s = date.fromisoformat(win_start)
    e = date.fromisoformat(win_end)
    days = []
    d = s
    while d <= e:
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return days


def _group_for_day(cfg: dict,
                    day_str: str):
    """Return the group_id whose date range contains day_str.

    Works for both monthly (YYYYMM) and ndays (YYYYMMDD) grouping.
    Returns None if no group covers the day.
    """
    d = date(int(day_str[:4]),
             int(day_str[4:6]),
             int(day_str[6:8]))
    for gid in list_groups(cfg):
        gstart, gend = group_date_range(cfg, gid)
        if gstart <= d <= gend:
            return gid
    return None


def _to_180(lon_arr):
    """Convert longitude array from 0..360 to -180..180."""
    import numpy as np
    arr = np.asarray(lon_arr, dtype=float)
    arr = arr % 360.0
    arr[arr > 180.0] -= 360.0
    return arr


# =============================================================================
# Stage 1 worker: collocate one day
# =============================================================================

def _collocate_one_day(cfg: dict,
                        day_str: str,
                        out_dir: Path):
    """Collocate satellite altimetry Hs against SCHISM+WWM for one day.

    Returns the path to the output NetCDF, or None if no collocation
    was possible.
    """
    import numpy as np
    from ocstrack.Model.model import SCHISM
    from ocstrack.Observation.satellite import SatelliteData
    from ocstrack.Collocation.collocate import Collocate

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    d        = date(int(day_str[:4]),
                    int(day_str[4:6]),
                    int(day_str[6:8]))
    d_next   = d + timedelta(days=1)
    day_iso  = d.isoformat()
    dnxt_iso = d_next.isoformat()

    daily_dir = _daily_dir(out_dir)
    out_nc    = (daily_dir
                 / f"collocated_hs_{day_str}.nc")

    if out_nc.exists() and out_nc.stat().st_size > 0:
        print(f"  [{day_str}] already done, "
              f"skipping.")
        return out_nc

    # ---- Find the merged satellite file ----
    obs_dir  = _altimetry_obs_dir(cfg)
    sat_file = _find_merged_sat_file(obs_dir)
    if sat_file is None:
        print(f"  [{day_str}] no merged satellite "
              f"file found in {obs_dir}. "
              f"Run download_altimetry first.")
        return None

    # ---- Find the group run directory ----
    gid = _group_for_day(cfg, day_str)
    if gid is None:
        print(f"  [{day_str}] no group covers "
              f"this day, skipping.")
        return None

    rundir  = mdir / f"R{pid}" / f"R{pid}_{gid}"
    outputs = rundir / "outputs"

    if not outputs.is_dir():
        print(f"  [{day_str}] no outputs/ dir "
              f"for group {gid}, skipping.")
        return None

    if not list(outputs.glob("out2d_*.nc")):
        print(f"  [{day_str}] no out2d_*.nc "
              f"files in {outputs}, skipping.")
        return None

    if not (rundir / "hgrid.gr3").exists():
        print(f"  [{day_str}] no hgrid.gr3 in "
              f"{rundir}, skipping.")
        return None

    n_nearest  = int(cfg.get(
        "collocate_altimetry_n_nearest", 3))
    model_dict = {
        "startswith": "out2d_",
        "var":        "sigWaveHeight",
        "var_type":   "2D",
    }

    # ---- Load satellite data ----
    try:
        sat_data = SatelliteData(str(sat_file))
    except Exception as exc:
        print(f"  [{day_str}] SatelliteData load "
              f"failed: "
              f"{type(exc).__name__}: {exc}")
        return None

    # ---- Load model ----
    try:
        model = SCHISM(
            rundir=str(rundir),
            model_dict=model_dict,
            start_date=np.datetime64(day_iso),
            end_date=np.datetime64(dnxt_iso),
        )
    except Exception as exc:
        print(f"  [{day_str}] SCHISM init failed: "
              f"{type(exc).__name__}: {exc}")
        return None

    if not model.files:
        print(f"  [{day_str}] no model files "
              f"overlap this day, skipping.")
        return None

    # Convert mesh longitudes 0..360 -> -180..180
    # to match CCI/CoastWatch convention
    model.mesh_x = _to_180(model.mesh_x)

    # ---- Collocate ----
    try:
        print(f"  [{day_str}] collocating against "
              f"{len(model.files)} model file(s) "
              f"-> {out_nc.name}")
        coll = Collocate(
            model_run=model,
            observation=sat_data,
            n_nearest=n_nearest,
        )
        ds = coll.run(output_path=str(out_nc))
    except Exception as exc:
        print(f"  [{day_str}] collocation failed: "
              f"{type(exc).__name__}: {exc}")
        out_nc.unlink(missing_ok=True)
        return None

    if (ds is None
            or (hasattr(ds, "sizes")
                and ds.sizes.get("time", 0) == 0)):
        print(f"  [{day_str}] no collocated points.")
        out_nc.unlink(missing_ok=True)
        return None

    print(f"  [{day_str}] "
          f"{ds.sizes.get('time', '?')} "
          f"collocated point(s) -> {out_nc.name}")
    return out_nc


# =============================================================================
# Stage 2: merge daily files + optional clean file
# =============================================================================

def _write_clean(combined_path: Path,
                  dist_threshold_km: float) -> Path:
    """Write a distance-filtered clean NetCDF alongside the combined file.

    Mirrors _write_clean() in collocate_argo.py.
    Filters observations where min(dist_deltas) > dist_threshold_km.
    Returns the clean file path, or None if the threshold removes all points.
    """
    import xarray as xr

    clean_path = combined_path.parent / (
        combined_path.stem + "_clean"
        + combined_path.suffix)

    ds           = xr.open_dataset(
        str(combined_path), engine="netcdf4")
    nearest_dist = ds["dist_deltas"].min(
        dim="nearest_nodes")
    thresh_m     = dist_threshold_km * 1000.0
    mask         = nearest_dist.values < thresh_m

    n_total = int(ds.sizes["time"])
    n_keep  = int(mask.sum())
    n_drop  = n_total - n_keep

    if n_keep == 0:
        print(f"  [clean] WARNING: threshold "
              f"{dist_threshold_km} km removes "
              f"ALL {n_total} points. "
              f"Clean file not written.")
        ds.close()
        return None

    ds_clean = ds.isel(time=mask)
    ds_clean.attrs[
        "distance_filter_km"] = dist_threshold_km
    ds_clean.attrs[
        "points_total"]   = n_total
    ds_clean.attrs[
        "points_kept"]    = n_keep
    ds_clean.attrs[
        "points_dropped"] = n_drop
    ds_clean.to_netcdf(str(clean_path))
    ds.close()

    print(f"  [clean] kept {n_keep}/{n_total} "
          f"points (dropped {n_drop} > "
          f"{dist_threshold_km} km) "
          f"-> {clean_path.name}")
    return clean_path


def run_merge(cfg: dict):
    """Stage 2: concatenate daily files and optionally write clean file."""
    import xarray as xr

    out_dir    = _out_dir(cfg)
    daily_dir  = out_dir / "daily"
    combined   = out_dir / "collocated_hs.nc"
    sentinel   = out_dir / "collocate_altimetry.done"
    done_daily = out_dir / ".daily_done"

    # Distance threshold for clean file
    # (null/None = no clean file)
    dist_thresh_cfg = cfg.get(
        "collocate_altimetry_dist_threshold_km")
    dist_thresh_km  = (
        float(dist_thresh_cfg)
        if dist_thresh_cfg is not None
        else None)

    print(f"\n{'='*60}")
    print(f"  Altimetry collocation merge")
    print(f"  Output: {out_dir}")
    if dist_thresh_km is not None:
        print(f"  Distance threshold: "
              f"{dist_thresh_km} km "
              f"(will write clean file)")
    else:
        print(f"  Distance threshold: none "
              f"(collocate_altimetry_dist_threshold_km "
              f"is null — no clean file)")
    print(f"{'='*60}\n")

    files = sorted(
        daily_dir.glob(
            "collocated_hs_????????.nc"))
    if not files:
        print("  [merge] no daily files found. "
              "Check that Stage 1 array ran "
              "successfully.")
        return

    print(f"  [merge] concatenating "
          f"{len(files)} daily file(s) ...")

    dsets = []
    for f in files:
        try:
            dsets.append(
                xr.open_dataset(str(f)))
        except (OSError, ValueError) as exc:
            print(f"  [merge] could not open "
                  f"{f.name}: {exc}")

    if not dsets:
        print("  [merge] no readable daily "
              "files found.")
        return

    try:
        merged = xr.concat(
            dsets, dim="time", join="outer")
        merged = merged.sortby("time")
    except (ValueError, KeyError) as exc:
        print(f"  [merge] concat failed: {exc}")
        for ds in dsets:
            ds.close()
        return

    merged.to_netcdf(str(combined))
    for ds in dsets:
        ds.close()

    print(f"  [merge] written -> {combined.name} "
          f"({combined.stat().st_size // 1024 // 1024}"
          f" MB)")

    # Write clean file if threshold is configured
    if dist_thresh_km is not None:
        _write_clean(combined, dist_thresh_km)

    sentinel.touch()
    done_daily.touch()

    print(f"\n{'='*60}")
    print(f"  Altimetry collocation merge complete.")
    print(f"  Combined NetCDF : {combined}")
    if dist_thresh_km is not None:
        clean = (out_dir
                 / "collocated_hs_clean.nc")
        if clean.exists():
            print(f"  Clean NetCDF    : {clean}")
    print(f"  Sentinel        : {sentinel}")
    print(f"{'='*60}\n")


# =============================================================================
# Serial fallback
# =============================================================================

def _run_serial(cfg: dict):
    """Serial fallback: collocate day by day without SLURM."""
    obs_dir  = _altimetry_obs_dir(cfg)
    sat_file = _find_merged_sat_file(obs_dir)

    if sat_file is None:
        print(f"ERROR: no merged satellite file "
              f"found in {obs_dir}. "
              f"Run download_altimetry first.")
        return

    days    = _build_day_list(cfg)
    out_dir = _out_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)

    sentinel = out_dir / "collocate_altimetry.done"
    if sentinel.exists():
        print("  collocate_altimetry: already "
              "complete, skipping.")
        return

    print(f"\n{'='*60}")
    print(f"  Altimetry collocation (serial mode)")
    print(f"  Satellite file : {sat_file.name}")
    print(f"  {len(days)} day(s)")
    print(f"{'='*60}\n")

    for day_str in days:
        try:
            _collocate_one_day(
                cfg, day_str, out_dir)
        except Exception as exc:
            print(f"  [{day_str}] ERROR: "
                  f"{type(exc).__name__}: {exc}")

    run_merge(cfg)


# =============================================================================
# Dispatcher
# =============================================================================

def run_collocate_altimetry(cfg: dict,
                             config_dir=None):
    """Dispatcher: submit SLURM pipeline or run serial fallback."""
    import shutil

    allow_serial = (
        os.environ.get("ALLOW_NON_SLURM") == "1")

    if allow_serial or shutil.which("sbatch") is None:
        if shutil.which("sbatch") is None:
            print("  [collocate_altimetry] sbatch "
                  "not found — running serial "
                  "fallback.")
        _run_serial(cfg)
        return

    from workflow.models.schism_wwm.postprocess.submit_collocate_altimetry import (
        submit_collocate_altimetry,
    )
    submit_collocate_altimetry(
        cfg,
        Path(config_dir) if config_dir else None)


# =============================================================================
# CLI
# =============================================================================

def main():
    ap  = argparse.ArgumentParser(
        description="Altimetry collocation CLI "
                    "(SLURM tasks)")
    sub = ap.add_subparsers(dest="stage",
                             required=True)

    pd_p = sub.add_parser(
        "day",
        help="Collocate one day")
    pd_p.add_argument(
        "--date", required=True,
        help="Date to collocate (YYYYMMDD)")

    sub.add_parser(
        "merge",
        help="Merge daily outputs")

    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg  = load_config(Path(args.config))

    if args.stage == "day":
        out_dir = _out_dir(cfg)
        out_dir.mkdir(parents=True, exist_ok=True)
        _collocate_one_day(
            cfg, args.date, out_dir)
    elif args.stage == "merge":
        run_merge(cfg)


if __name__ == "__main__":
    main()
