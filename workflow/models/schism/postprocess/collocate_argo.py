"""
models/schism/postprocess/collocate_argo.py
===========================================
Phase 5 step "collocate_argo".

Two-stage SLURM design (parallel per-day array → serial merge/clean):

  Stage 1 (SLURM array, one task per day):
      Each task collocates ALL configured variables (temperature, salinity)
      for a single calendar day.  Building the 2.6 M-node KDTree is the
      expensive setup; doing both variables in one task means it is paid only
      once per day.  Each task reads Argo profiles time-filtered to that day,
      instantiates OCSTrack SCHISM pointing at the correct monthly run dir,
      and writes per-day per-variable NetCDFs under
          P{ID}/P{ID}_collocate_argo/daily/collocated_{var}_{YYYYMMDD}.nc

  Stage 2 (serial, --dependency=afterok on Stage 1):
      Concatenates all daily files per variable into
          P{ID}/P{ID}_collocate_argo/collocated_{var}.nc
      then distance-filters to produce
          P{ID}/P{ID}_collocate_argo/collocated_{var}_clean.nc
      and touches collocate_argo.done.

Fallback (ALLOW_NON_SLURM=1 or no sbatch):
      Runs the original serial month-by-month loop in-process (useful for
      testing or small runs without SLURM).

CLI
---
    # Stage 1 array task (called by SLURM):
    python -m workflow.models.schism.postprocess.collocate_argo \\
        day --date YYYYMMDD --config <cfg>

    # Stage 2 merge (called by SLURM after array):
    python -m workflow.models.schism.postprocess.collocate_argo \\
        merge --config <cfg>

Inputs
------
  * Argo profiles   : M{ID}/obs/argo/{region}/processed/
  * SCHISM outputs  : R{ID}/R{ID}_{YYYYMM}/outputs/{var,zCoordinates}_*.nc

Outputs
-------
    P{ID}/P{ID}_collocate_argo/
        daily/collocated_{var}_{YYYYMMDD}.nc  (per-day, Stage 1)
        collocated_{var}.nc                   (concatenated, Stage 2)
        collocated_{var}_clean.nc             (distance-filtered, Stage 2)
        .daily_done                           (Stage 1 complete sentinel)
        collocate_argo.done                   (Stage 2 complete sentinel)
"""

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import load_config, list_months, model_dir


# ---------------------------------------------------------------------------
# Variable plan
# ---------------------------------------------------------------------------
VAR_PLAN = {
    "temperature": {"startswith": "temperature_"},
    "salinity":    {"startswith": "salinity_"},
}
ZCOR_VAR        = "zCoordinates"
ZCOR_STARTSWITH = "zCoordinates_"


# ---------------------------------------------------------------------------
# Helpers shared by both SLURM and serial paths
# ---------------------------------------------------------------------------

def _out_dir(cfg) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_collocate_argo"


def _daily_dir(out_dir: Path) -> Path:
    d = out_dir / "daily"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _argo_processed_dir(cfg) -> Path:
    region = str(cfg.get("argo_region", "pacific_ocean"))
    return model_dir(cfg) / "obs" / "argo" / region / "processed"


def _window(cfg):
    start = cfg.get("collocate_argo_start") or cfg["start_date"]
    end   = cfg.get("collocate_argo_end")   or cfg["end_date"]
    return str(start), str(end)


def _build_day_list(cfg) -> list:
    """Return every calendar day in the collocation window as 'YYYYMMDD' strings."""
    win_start, win_end = _window(cfg)
    s = date.fromisoformat(win_start)
    e = date.fromisoformat(win_end)
    days = []
    d = s
    while d <= e:
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return days


def _ym_for_day(day_str: str) -> str:
    """Return YYYYMM for a YYYYMMDD string."""
    return day_str[:6]


def _clip(a_start, a_end, b_start, b_end):
    s = max(a_start, b_start)
    e = min(a_end, b_end)
    return (s, e) if s < e else None


# ---------------------------------------------------------------------------
# Stage 1 worker: collocate one day, all variables
# ---------------------------------------------------------------------------

def _collocate_one_day(cfg, day_str: str, out_dir: Path) -> list:
    """Collocate all configured variables for a single calendar day.

    Memory-efficient design: loads only the single processed Argo file
    matching the day (cropped_{YYYYMMDD}_prof.nc) rather than the full
    processed directory, avoiding OOM in the SLURM array tasks.

    Builds the KDTree once (expensive for 2.6 M nodes), then reuses it for
    each variable.  Returns a list of written NetCDF paths (one per variable
    that produced results).

    The SCHISM model object is built per-variable (different file prefix) but
    shares the same run directory, so hgrid.gr3 is parsed once.
    """
    import numpy as np
    from ocstrack.Model.model import SCHISM
    from ocstrack.Observation.argofloat import ArgoData
    from ocstrack.Collocation.collocate import Collocate
    from ocstrack.utils import convert_longitude

    variables = cfg.get("collocate_argo_vars") or ["temperature", "salinity"]
    variables = [v for v in variables if v in VAR_PLAN]

    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    ym    = _ym_for_day(day_str)

    # Day window: [day_str, next_day)
    d        = date(int(day_str[:4]), int(day_str[4:6]), int(day_str[6:8]))
    d_next   = d + timedelta(days=1)
    day_iso  = d.isoformat()
    dnxt_iso = d_next.isoformat()

    daily_dir = _daily_dir(out_dir)
    written   = []

    # Skip if all output files already exist.
    all_done = all(
        (daily_dir / f"collocated_{v}_{day_str}.nc").exists()
        for v in variables
    )
    if all_done:
        print(f"  [{day_str}] all variables already done, skipping.")
        return [daily_dir / f"collocated_{v}_{day_str}.nc" for v in variables]

    # ---- Load Argo profiles for this day (single-file, memory-efficient) ----
    # The processed directory contains one file per day named
    # cropped_{YYYYMMDD}_prof.nc.  Loading only that file instead of the full
    # directory avoids concatenating all 100+ days into memory before
    # time-filtering — which caused OOM kills in the SLURM array tasks.
    argo_dir     = _argo_processed_dir(cfg)
    argo_day_nc  = argo_dir / f"cropped_{day_str}_prof.nc"

    if argo_day_nc.exists() and argo_day_nc.stat().st_size > 0:
        # Fast path: load only the single daily file.
        import tempfile, shutil
        # ArgoData expects a directory; create a temporary directory containing
        # only the one file (symlink avoids any data copy).
        tmp_dir = Path(tempfile.mkdtemp(prefix="argo_day_"))
        try:
            (tmp_dir / argo_day_nc.name).symlink_to(argo_day_nc)
            try:
                argo_full = ArgoData(str(tmp_dir))
            except ValueError as exc:
                print(f"  [{day_str}] Argo load failed ({argo_day_nc.name}): {exc}")
                return []
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        # Fallback: load the full directory and time-filter.
        # Used when filenames don't match the cropped_{YYYYMMDD}_prof.nc pattern.
        print(f"  [{day_str}] no daily file {argo_day_nc.name} — "
              f"loading full directory (slower).")
        try:
            argo_full = ArgoData(str(argo_dir))
        except ValueError as exc:
            print(f"  [{day_str}] Argo load failed: {exc}")
            return []
        argo_full.filter_by_time(day_iso, dnxt_iso)

    if argo_full.ds.sizes.get("JULD", 0) == 0:
        print(f"  [{day_str}] no Argo profiles, skipping.")
        return []

    # Convert longitudes once for this day's Argo data.
    lon_ref = str(cfg.get("lon_reference", "360"))
    if lon_ref == "360":
        argo_full.lon = convert_longitude(argo_full.lon, mode=1)

    n_nearest = int(cfg.get("collocate_argo_n_nearest", 3))
    temporal  = bool(cfg.get("collocate_argo_temporal_interp", True))

    # ---- Run OCSTrack for each variable ----
    # We use the SAME Argo object for all variables (just clone the dataset
    # reference — ArgoData is read-only after construction).
    for var in variables:
        out_nc = daily_dir / f"collocated_{var}_{day_str}.nc"
        if out_nc.exists() and out_nc.stat().st_size > 0:
            print(f"  [{day_str} {var}] already exists, skipping.")
            written.append(out_nc)
            continue

        rundir  = mdir / f"R{pid}" / f"R{pid}_{ym}"
        outputs = rundir / "outputs"

        if not outputs.is_dir():
            print(f"  [{day_str} {var}] no outputs/ dir for {ym}, skipping.")
            continue

        startswith = VAR_PLAN[var]["startswith"]
        if not list(outputs.glob(f"{startswith}*.nc")):
            print(f"  [{day_str} {var}] no {startswith}*.nc stacks, skipping.")
            continue
        if not list(outputs.glob(f"{ZCOR_STARTSWITH}*.nc")):
            print(f"  [{day_str} {var}] no {ZCOR_STARTSWITH}*.nc stacks, skipping.")
            continue
        if not (rundir / "hgrid.gr3").exists():
            print(f"  [{day_str} {var}] no hgrid.gr3, skipping.")
            continue

        model_dict = {
            "var":             var,
            "startswith":      startswith,
            "var_type":        "3D_Profile",
            "zcor_var":        ZCOR_VAR,
            "zcor_startswith": ZCOR_STARTSWITH,
        }

        try:
            model = SCHISM(
                rundir=str(rundir),
                model_dict=model_dict,
                start_date=np.datetime64(day_iso),
                end_date=np.datetime64(dnxt_iso),
            )
        except Exception as exc:   # noqa: BLE001
            print(f"  [{day_str} {var}] SCHISM init failed: {exc}")
            continue

        if not model.files:
            print(f"  [{day_str} {var}] no model files for this day, skipping.")
            continue

        # Re-use the same ArgoData (already lon-converted and time-filtered).
        # OCSTrack reads argo_full.ds directly; we don't mutate it here.
        try:
            print(f"  [{day_str} {var}] collocating "
                  f"{argo_full.ds.sizes['JULD']} profile(s) "
                  f"against {len(model.files)} model file(s) -> {out_nc.name}")
            coll = Collocate(
                model_run=model,
                observation=argo_full,
                n_nearest=n_nearest,
                temporal_interp=temporal,
            )
            ds = coll.run(output_path=str(out_nc))
        except Exception as exc:   # noqa: BLE001
            print(f"  [{day_str} {var}] collocation failed: "
                  f"{type(exc).__name__}: {exc}")
            out_nc.unlink(missing_ok=True)
            continue

        if ds is None or (hasattr(ds, "sizes") and ds.sizes.get("time", 0) == 0):
            print(f"  [{day_str} {var}] no matches.")
            out_nc.unlink(missing_ok=True)
            continue

        written.append(out_nc)

    return written


# ---------------------------------------------------------------------------
# Stage 2: merge daily files, distance-filter, write sentinels
# ---------------------------------------------------------------------------

def _concat_daily(var: str, daily_dir: Path, out_dir: Path):
    """Concatenate all collocated_{var}_{YYYYMMDD}.nc files into one."""
    import xarray as xr

    files = sorted(daily_dir.glob(f"collocated_{var}_????????.nc"))
    if not files:
        print(f"  [merge {var}] no daily files found in {daily_dir}")
        return None

    combined = out_dir / f"collocated_{var}.nc"
    dsets = []
    for f in files:
        try:
            dsets.append(xr.open_dataset(str(f)))
        except (OSError, ValueError) as exc:
            print(f"  [merge {var}] could not open {f.name}: {exc}")
    if not dsets:
        return None

    try:
        merged = xr.concat(dsets, dim="time")
        merged = merged.sortby("time")
    except (ValueError, KeyError) as exc:
        print(f"  [merge {var}] concat failed: {exc}")
        for d in dsets:
            d.close()
        return None

    merged.to_netcdf(str(combined))
    for d in dsets:
        d.close()
    print(f"  [merge {var}] {len(files)} daily file(s) -> {combined.name}")
    return combined


def _write_clean(combined_path: Path, dist_threshold_m: float) -> Path:
    """Write a distance-filtered companion *_clean.nc file."""
    import xarray as xr

    clean_path = combined_path.parent / (
        combined_path.stem + "_clean" + combined_path.suffix
    )
    ds = xr.open_dataset(str(combined_path), engine="netcdf4")
    nearest_dist = ds["dist_deltas"].min(dim="nearest_nodes")
    mask    = nearest_dist.values < dist_threshold_m
    n_total = int(ds.sizes["time"])
    n_keep  = int(mask.sum())
    n_drop  = n_total - n_keep

    if n_keep == 0:
        print(f"  [clean {combined_path.name}] WARNING: threshold "
              f"{dist_threshold_m/1000:.1f} km removes ALL {n_total} profiles. "
              f"Clean file not written.")
        ds.close()
        return None

    ds_clean = ds.isel(time=mask)
    ds_clean.attrs["distance_filter_m"] = dist_threshold_m
    ds_clean.attrs["profiles_total"]    = n_total
    ds_clean.attrs["profiles_kept"]     = n_keep
    ds_clean.attrs["profiles_dropped"]  = n_drop
    ds_clean.to_netcdf(str(clean_path))
    ds.close()
    print(f"  [clean {combined_path.name}] kept {n_keep}/{n_total} profiles "
          f"(dropped {n_drop} > {dist_threshold_m/1000:.1f} km) "
          f"-> {clean_path.name}")
    return clean_path


def run_merge(cfg: dict):
    """Stage 2: merge daily outputs and write clean files.

    Called by the serial SLURM merge job (after the array completes).
    Also callable directly via ``--only collocate_argo`` if daily files
    already exist and only the merge is needed.
    """
    variables = cfg.get("collocate_argo_vars") or ["temperature", "salinity"]
    variables = [v for v in variables if v in VAR_PLAN]
    out_dir   = _out_dir(cfg)
    daily_dir = out_dir / "daily"
    dist_thresh = float(cfg.get("collocate_argo_dist_threshold_km", 5)) * 1000.0

    print(f"\n{'='*60}")
    print(f"  Argo collocation merge")
    print(f"  Variables: {variables}")
    print(f"  Distance threshold: {dist_thresh/1000:.1f} km")
    print(f"  Output: {out_dir}")
    print(f"{'='*60}\n")

    any_output = False
    for var in variables:
        combined = _concat_daily(var, daily_dir, out_dir)
        if combined is not None:
            _write_clean(combined, dist_threshold_m=dist_thresh)
            any_output = True

    if any_output:
        (out_dir / "collocate_argo.done").touch()
        print(f"\n{'='*60}")
        print(f"  Argo collocation merge complete. NetCDFs in {out_dir}")
        print(f"{'='*60}\n")
    else:
        print("\n  Merge found no daily files. Check that Stage 1 array ran.")


# ---------------------------------------------------------------------------
# Serial fallback (ALLOW_NON_SLURM=1 or no sbatch) — month-by-month loop
# ---------------------------------------------------------------------------

def _month_bounds(ym: str):
    from dateutil.relativedelta import relativedelta
    y, m = int(ym[:4]), int(ym[4:])
    first = date(y, m, 1)
    nxt   = first + relativedelta(months=1)
    return first.isoformat(), nxt.isoformat()


def _collocate_one_month(cfg, ym, var, win_start, win_end, out_dir):
    """Serial fallback: collocate one variable for a whole month."""
    import numpy as np
    from ocstrack.Model.model import SCHISM
    from ocstrack.Observation.argofloat import ArgoData
    from ocstrack.Collocation.collocate import Collocate
    from ocstrack.utils import convert_longitude

    pid     = cfg["project_id"]
    mdir    = model_dir(cfg)
    rundir  = mdir / f"R{pid}" / f"R{pid}_{ym}"
    outputs = rundir / "outputs"

    if not outputs.is_dir():
        print(f"  [{ym} {var}] no run outputs/ dir, skipping.")
        return None

    startswith = VAR_PLAN[var]["startswith"]
    if not list(outputs.glob(f"{startswith}*.nc")):
        print(f"  [{ym} {var}] no {startswith}*.nc stacks, skipping.")
        return None
    if not list(outputs.glob(f"{ZCOR_STARTSWITH}*.nc")):
        print(f"  [{ym} {var}] no {ZCOR_STARTSWITH}*.nc stacks, skipping.")
        return None
    if not (rundir / "hgrid.gr3").exists():
        print(f"  [{ym} {var}] no hgrid.gr3, skipping.")
        return None

    m_start, m_end = _month_bounds(ym)
    clip = _clip(win_start, win_end, m_start, m_end)
    if clip is None:
        return None
    c_start, c_end = clip

    argo_dir = _argo_processed_dir(cfg)
    try:
        argo = ArgoData(str(argo_dir))
    except ValueError as exc:
        print(f"  [{ym} {var}] Argo load failed: {exc}")
        return None

    argo.filter_by_time(c_start, c_end)
    if argo.ds.sizes.get("JULD", 0) == 0:
        return None

    lon_ref = str(cfg.get("lon_reference", "360"))
    if lon_ref == "360":
        argo.lon = convert_longitude(argo.lon, mode=1)

    model_dict = {
        "var":             var,
        "startswith":      startswith,
        "var_type":        "3D_Profile",
        "zcor_var":        ZCOR_VAR,
        "zcor_startswith": ZCOR_STARTSWITH,
    }
    model = SCHISM(
        rundir=str(rundir),
        model_dict=model_dict,
        start_date=np.datetime64(c_start),
        end_date=np.datetime64(c_end),
    )
    if not model.files:
        return None

    n_nearest = int(cfg.get("collocate_argo_n_nearest", 3))
    temporal  = bool(cfg.get("collocate_argo_temporal_interp", True))
    out_nc    = out_dir / f"collocated_{var}_{ym}.nc"
    print(f"  [{ym} {var}] collocating {argo.ds.sizes['JULD']} profile(s) "
          f"against {len(model.files)} model file(s) -> {out_nc.name}")

    coll = Collocate(
        model_run=model, observation=argo,
        n_nearest=n_nearest, temporal_interp=temporal,
    )
    ds = coll.run(output_path=str(out_nc))
    if ds is None or (hasattr(ds, "sizes") and ds.sizes.get("time", 0) == 0):
        out_nc.unlink(missing_ok=True)
        return None
    return out_nc


def _concat_months(var, per_month_files, out_dir):
    import xarray as xr
    if not per_month_files:
        return None
    combined = out_dir / f"collocated_{var}.nc"
    dsets = []
    for f in per_month_files:
        try:
            dsets.append(xr.open_dataset(str(f)))
        except (OSError, ValueError) as exc:
            print(f"  [concat {var}] could not open {f.name}: {exc}")
    if not dsets:
        return None
    try:
        merged = xr.concat(dsets, dim="time")
        merged = merged.sortby("time")
    except (ValueError, KeyError) as exc:
        print(f"  [concat {var}] concat failed: {exc}")
        for d in dsets:
            d.close()
        return None
    merged.to_netcdf(str(combined))
    for d in dsets:
        d.close()
    print(f"  [concat {var}] {len(per_month_files)} month(s) -> {combined.name}")
    return combined


def run_collocate_argo_serial(cfg: dict):
    """Serial month-by-month collocation (ALLOW_NON_SLURM=1 fallback)."""
    argo_dir  = _argo_processed_dir(cfg)
    if not argo_dir.is_dir() or not list(argo_dir.glob("*.nc")):
        print(f"ERROR: no processed Argo files in {argo_dir}")
        return

    variables = cfg.get("collocate_argo_vars") or ["temperature", "salinity"]
    variables = [v for v in variables if v in VAR_PLAN]
    if not variables:
        print("ERROR: collocate_argo_vars has no known variables.")
        return

    win_start, win_end = _window(cfg)
    out_dir   = _out_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    months    = list_months(cfg)
    dist_thresh = float(cfg.get("collocate_argo_dist_threshold_km", 5)) * 1000.0

    print(f"\n{'='*60}")
    print(f"  Argo collocation (serial mode)")
    print(f"  Variables: {variables}")
    print(f"  Window: {win_start} -> {win_end}   {len(months)} month(s)")
    print(f"{'='*60}\n")

    any_output = False
    for var in variables:
        per_month = []
        for ym in months:
            try:
                nc = _collocate_one_month(cfg, ym, var, win_start, win_end,
                                          out_dir)
            except Exception as exc:   # noqa: BLE001
                print(f"  [{ym} {var}] ERROR: {type(exc).__name__}: {exc}")
                nc = None
            if nc is not None:
                per_month.append(nc)
        combined = _concat_months(var, per_month, out_dir)
        if combined is not None:
            any_output = True
            _write_clean(combined, dist_threshold_m=dist_thresh)

    if any_output:
        (out_dir / "collocate_argo.done").touch()
        print(f"\n  Argo collocation (serial) complete. NetCDFs in {out_dir}")
    else:
        print("\n  Argo collocation (serial) produced no matches.")


# ---------------------------------------------------------------------------
# run_collocate_argo: dispatches to SLURM launcher or serial fallback
# ---------------------------------------------------------------------------

def run_collocate_argo(cfg: dict, config_dir=None):
    """Dispatch collocate_argo: SLURM two-stage (default) or serial fallback.

    If sbatch is available (and ALLOW_NON_SLURM != 1), submits the two-stage
    SLURM array.  Otherwise falls back to the serial month-by-month loop.
    """
    import shutil
    allow_serial = os.environ.get("ALLOW_NON_SLURM") == "1"

    if allow_serial or shutil.which("sbatch") is None:
        if shutil.which("sbatch") is None:
            print("  [collocate_argo] sbatch not found — running serial fallback.")
            print("  Set ALLOW_NON_SLURM=1 to suppress this message.")
        run_collocate_argo_serial(cfg)
        return

    from workflow.models.schism.postprocess.submit_collocate_argo import (
        submit_collocate_argo,
    )
    submit_collocate_argo(cfg, Path(config_dir) if config_dir else None)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap  = argparse.ArgumentParser(
        description="Argo collocation CLI (called by SLURM tasks)")
    sub = ap.add_subparsers(dest="stage", required=True)

    # Stage 1: per-day task
    pd = sub.add_parser("day", help="Collocate all variables for one day "
                        "(called by SLURM array task)")
    pd.add_argument("--date", required=True,
                    help="Date to collocate (YYYYMMDD)")

    # Stage 2: merge daily outputs
    sub.add_parser("merge", help="Merge daily outputs + write clean file "
                   "(called by SLURM after array)")

    ap.add_argument("--config", required=True,
                    help="Path to project config/ directory")
    args = ap.parse_args()
    cfg  = load_config(Path(args.config))

    if args.stage == "day":
        out_dir = _out_dir(cfg)
        out_dir.mkdir(parents=True, exist_ok=True)
        _collocate_one_day(cfg, args.date, out_dir)

    elif args.stage == "merge":
        run_merge(cfg)


if __name__ == "__main__":
    main()
