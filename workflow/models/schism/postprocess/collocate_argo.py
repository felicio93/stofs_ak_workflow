"""
models/schism/postprocess/collocate_argo.py
===========================================
Phase 5 step "collocate_argo".

Two-stage SLURM design (parallel per-day array -> serial merge/clean).

Works for all grouping modes (monthly, ndays/weekly/daily).
_group_for_day() maps each calendar day to the correct run directory
regardless of whether group IDs are YYYYMM or YYYYMMDD — fixes the
mismatch that caused all daily tasks to find no SCHISM outputs for
ndays grouping.
"""

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import (
    load_config,
    list_groups,
    list_months,
    group_date_range,
    model_dir,
)


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
# Helpers
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
    return (model_dir(cfg) / "obs" / "argo"
            / region / "processed")


def _window(cfg):
    start = cfg.get("collocate_argo_start") or cfg["start_date"]
    end   = cfg.get("collocate_argo_end")   or cfg["end_date"]
    return str(start), str(end)


def _build_day_list(cfg) -> list:
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


def _group_for_day(cfg, day_str: str):
    """Return the group_id whose date range contains day_str.

    Works for both monthly (YYYYMM) and ndays (YYYYMMDD) grouping.
    day_str is 'YYYYMMDD'. Returns None if no group covers the day.
    """
    d = date(int(day_str[:4]),
             int(day_str[4:6]),
             int(day_str[6:8]))
    for gid in list_groups(cfg):
        gstart, gend = group_date_range(cfg, gid)
        if gstart <= d <= gend:
            return gid
    return None


def _clip(a_start, a_end, b_start, b_end):
    s = max(a_start, b_start)
    e = min(a_end, b_end)
    return (s, e) if s < e else None


# ---------------------------------------------------------------------------
# Stage 1 worker: collocate one day, all variables
# ---------------------------------------------------------------------------

def _collocate_one_day(cfg, day_str: str,
                        out_dir: Path) -> list:
    """Collocate all configured variables for one calendar day."""
    import numpy as np
    from ocstrack.Model.model import SCHISM
    from ocstrack.Observation.argofloat import ArgoData
    from ocstrack.Collocation.collocate import Collocate
    from ocstrack.utils import convert_longitude

    variables = cfg.get("collocate_argo_vars") or [
        "temperature", "salinity"]
    variables = [v for v in variables if v in VAR_PLAN]

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    d        = date(int(day_str[:4]),
                    int(day_str[4:6]),
                    int(day_str[6:8]))
    d_next   = d + timedelta(days=1)
    day_iso  = d.isoformat()
    dnxt_iso = d_next.isoformat()

    daily_dir = _daily_dir(out_dir)
    written   = []

    # Skip if all output files already exist
    all_done = all(
        (daily_dir / f"collocated_{v}_{day_str}.nc").exists()
        for v in variables
    )
    if all_done:
        print(f"  [{day_str}] all variables already done, "
              f"skipping.")
        return [daily_dir / f"collocated_{v}_{day_str}.nc"
                for v in variables]

    # ---- Load Argo profiles for this day ----
    argo_dir    = _argo_processed_dir(cfg)
    argo_day_nc = argo_dir / f"cropped_{day_str}_prof.nc"

    if (argo_day_nc.exists()
            and argo_day_nc.stat().st_size > 0):
        import tempfile
        import shutil
        tmp_dir = Path(tempfile.mkdtemp(
            prefix="argo_day_"))
        try:
            (tmp_dir / argo_day_nc.name).symlink_to(
                argo_day_nc)
            try:
                argo_full = ArgoData(str(tmp_dir))
            except ValueError as exc:
                print(f"  [{day_str}] Argo load failed "
                      f"({argo_day_nc.name}): {exc}")
                return []
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        print(f"  [{day_str}] no daily file "
              f"{argo_day_nc.name} — "
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

    lon_ref = str(cfg.get("lon_reference", "360"))
    if lon_ref == "360":
        argo_full.lon = convert_longitude(
            argo_full.lon, mode=1)

    n_nearest = int(cfg.get("collocate_argo_n_nearest", 3))
    temporal  = bool(cfg.get(
        "collocate_argo_temporal_interp", True))

    # ---- Find the group run directory for this day ----
    # Uses _group_for_day so it works for both YYYYMM and
    # YYYYMMDD group IDs (ndays grouping).
    gid = _group_for_day(cfg, day_str)
    if gid is None:
        print(f"  [{day_str}] no group covers this day, "
              f"skipping.")
        return []

    rundir  = mdir / f"R{pid}" / f"R{pid}_{gid}"
    outputs = rundir / "outputs"

    if not outputs.is_dir():
        print(f"  [{day_str}] no outputs/ dir for "
              f"group {gid}, skipping.")
        return []

    # ---- Run OCSTrack for each variable ----
    for var in variables:
        out_nc = daily_dir / f"collocated_{var}_{day_str}.nc"
        if out_nc.exists() and out_nc.stat().st_size > 0:
            print(f"  [{day_str} {var}] already exists, "
                  f"skipping.")
            written.append(out_nc)
            continue

        startswith = VAR_PLAN[var]["startswith"]
        if not list(outputs.glob(f"{startswith}*.nc")):
            print(f"  [{day_str} {var}] no "
                  f"{startswith}*.nc stacks, skipping.")
            continue
        if not list(
                outputs.glob(f"{ZCOR_STARTSWITH}*.nc")):
            print(f"  [{day_str} {var}] no "
                  f"{ZCOR_STARTSWITH}*.nc stacks, "
                  f"skipping.")
            continue
        if not (rundir / "hgrid.gr3").exists():
            print(f"  [{day_str} {var}] no hgrid.gr3, "
                  f"skipping.")
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
        except Exception as exc:
            print(f"  [{day_str} {var}] SCHISM init "
                  f"failed: {exc}")
            continue

        if not model.files:
            print(f"  [{day_str} {var}] no model files "
                  f"for this day, skipping.")
            continue

        try:
            print(f"  [{day_str} {var}] collocating "
                  f"{argo_full.ds.sizes['JULD']} "
                  f"profile(s) against "
                  f"{len(model.files)} model file(s) "
                  f"-> {out_nc.name}")
            coll = Collocate(
                model_run=model,
                observation=argo_full,
                n_nearest=n_nearest,
                temporal_interp=temporal,
            )
            ds = coll.run(output_path=str(out_nc))
        except Exception as exc:
            print(f"  [{day_str} {var}] collocation "
                  f"failed: {type(exc).__name__}: {exc}")
            out_nc.unlink(missing_ok=True)
            continue

        if (ds is None
                or (hasattr(ds, "sizes")
                    and ds.sizes.get("time", 0) == 0)):
            print(f"  [{day_str} {var}] no matches.")
            out_nc.unlink(missing_ok=True)
            continue

        written.append(out_nc)

    return written


# ---------------------------------------------------------------------------
# Stage 2: merge daily files, distance-filter, write sentinels
# ---------------------------------------------------------------------------

def _concat_daily(var: str, daily_dir: Path,
                   out_dir: Path):
    import xarray as xr

    files = sorted(
        daily_dir.glob(f"collocated_{var}_????????.nc"))
    if not files:
        print(f"  [merge {var}] no daily files found "
              f"in {daily_dir}")
        return None

    combined = out_dir / f"collocated_{var}.nc"
    dsets    = []
    for f in files:
        try:
            dsets.append(xr.open_dataset(str(f)))
        except (OSError, ValueError) as exc:
            print(f"  [merge {var}] could not open "
                  f"{f.name}: {exc}")
    if not dsets:
        return None

    try:
        merged = xr.concat(dsets, dim="time",
                            join="outer")
        merged = merged.sortby("time")
    except (ValueError, KeyError) as exc:
        print(f"  [merge {var}] concat failed: {exc}")
        for d in dsets:
            d.close()
        return None

    merged.to_netcdf(str(combined))
    for d in dsets:
        d.close()
    print(f"  [merge {var}] {len(files)} daily file(s) "
          f"-> {combined.name}")
    return combined


def _write_clean(combined_path: Path,
                  dist_threshold_m: float) -> Path:
    import xarray as xr

    clean_path = combined_path.parent / (
        combined_path.stem + "_clean"
        + combined_path.suffix)
    ds           = xr.open_dataset(str(combined_path),
                                    engine="netcdf4")
    nearest_dist = ds["dist_deltas"].min(
        dim="nearest_nodes")
    mask    = nearest_dist.values < dist_threshold_m
    n_total = int(ds.sizes["time"])
    n_keep  = int(mask.sum())
    n_drop  = n_total - n_keep

    if n_keep == 0:
        print(f"  [clean {combined_path.name}] WARNING: "
              f"threshold {dist_threshold_m/1000:.1f} km "
              f"removes ALL {n_total} profiles. "
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
    print(f"  [clean {combined_path.name}] kept "
          f"{n_keep}/{n_total} profiles "
          f"(dropped {n_drop} > "
          f"{dist_threshold_m/1000:.1f} km) "
          f"-> {clean_path.name}")
    return clean_path


def run_merge(cfg: dict):
    """Stage 2: merge daily outputs and write clean files."""
    variables = cfg.get("collocate_argo_vars") or [
        "temperature", "salinity"]
    variables   = [v for v in variables if v in VAR_PLAN]
    out_dir     = _out_dir(cfg)
    daily_dir   = out_dir / "daily"
    dist_thresh = float(cfg.get(
        "collocate_argo_dist_threshold_km", 5)) * 1000.0

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
            _write_clean(combined,
                         dist_threshold_m=dist_thresh)
            any_output = True

    if any_output:
        (out_dir / "collocate_argo.done").touch()
        print(f"\n{'='*60}")
        print(f"  Argo collocation merge complete. "
              f"NetCDFs in {out_dir}")
        print(f"{'='*60}\n")
    else:
        print("\n  Merge found no daily files. "
              "Check that Stage 1 array ran.")


# ---------------------------------------------------------------------------
# Serial fallback
# ---------------------------------------------------------------------------

def _month_bounds(ym: str):
    from dateutil.relativedelta import relativedelta
    y, m  = int(ym[:4]), int(ym[4:])
    first = date(y, m, 1)
    nxt   = first + relativedelta(months=1)
    return first.isoformat(), nxt.isoformat()


def _collocate_one_month(cfg, ym, var,
                          win_start, win_end, out_dir):
    """Serial fallback: collocate one variable for one group."""
    import numpy as np
    from ocstrack.Model.model import SCHISM
    from ocstrack.Observation.argofloat import ArgoData
    from ocstrack.Collocation.collocate import Collocate
    from ocstrack.utils import convert_longitude

    pid     = cfg["project_id"]
    mdir    = model_dir(cfg)
    # ym here is a group_id (YYYYMM or YYYYMMDD)
    rundir  = mdir / f"R{pid}" / f"R{pid}_{ym}"
    outputs = rundir / "outputs"

    if not outputs.is_dir():
        print(f"  [{ym} {var}] no run outputs/ dir, "
              f"skipping.")
        return None

    startswith = VAR_PLAN[var]["startswith"]
    if not list(outputs.glob(f"{startswith}*.nc")):
        print(f"  [{ym} {var}] no {startswith}*.nc "
              f"stacks, skipping.")
        return None
    if not list(
            outputs.glob(f"{ZCOR_STARTSWITH}*.nc")):
        print(f"  [{ym} {var}] no "
              f"{ZCOR_STARTSWITH}*.nc stacks, skipping.")
        return None
    if not (rundir / "hgrid.gr3").exists():
        print(f"  [{ym} {var}] no hgrid.gr3, skipping.")
        return None

    # Get date range for this group
    gstart, gend = group_date_range(cfg, ym)
    m_start      = gstart.isoformat()
    m_end        = (gend + timedelta(days=1)).isoformat()

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

    n_nearest = int(cfg.get(
        "collocate_argo_n_nearest", 3))
    temporal  = bool(cfg.get(
        "collocate_argo_temporal_interp", True))
    out_nc    = out_dir / f"collocated_{var}_{ym}.nc"
    print(f"  [{ym} {var}] collocating "
          f"{argo.ds.sizes['JULD']} profile(s) "
          f"against {len(model.files)} model file(s) "
          f"-> {out_nc.name}")

    coll = Collocate(
        model_run=model, observation=argo,
        n_nearest=n_nearest, temporal_interp=temporal,
    )
    ds = coll.run(output_path=str(out_nc))
    if (ds is None
            or (hasattr(ds, "sizes")
                and ds.sizes.get("time", 0) == 0)):
        out_nc.unlink(missing_ok=True)
        return None
    return out_nc


def _concat_months(var, per_month_files, out_dir):
    import xarray as xr
    if not per_month_files:
        return None
    combined = out_dir / f"collocated_{var}.nc"
    dsets    = []
    for f in per_month_files:
        try:
            dsets.append(xr.open_dataset(str(f)))
        except (OSError, ValueError) as exc:
            print(f"  [concat {var}] could not open "
                  f"{f.name}: {exc}")
    if not dsets:
        return None
    try:
        merged = xr.concat(dsets, dim="time",
                            join="outer")
        merged = merged.sortby("time")
    except (ValueError, KeyError) as exc:
        print(f"  [concat {var}] concat failed: {exc}")
        for d in dsets:
            d.close()
        return None
    merged.to_netcdf(str(combined))
    for d in dsets:
        d.close()
    print(f"  [concat {var}] "
          f"{len(per_month_files)} group(s) "
          f"-> {combined.name}")
    return combined


def run_collocate_argo_serial(cfg: dict):
    """Serial group-by-group collocation fallback."""
    argo_dir = _argo_processed_dir(cfg)
    if not argo_dir.is_dir() or not list(
            argo_dir.glob("*.nc")):
        print(f"ERROR: no processed Argo files in "
              f"{argo_dir}")
        return

    variables = cfg.get("collocate_argo_vars") or [
        "temperature", "salinity"]
    variables = [v for v in variables if v in VAR_PLAN]
    if not variables:
        print("ERROR: collocate_argo_vars has no known "
              "variables.")
        return

    win_start, win_end = _window(cfg)
    out_dir     = _out_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Use list_groups so group IDs match run dir names
    groups      = list_groups(cfg)
    dist_thresh = float(cfg.get(
        "collocate_argo_dist_threshold_km", 5)) * 1000.0

    print(f"\n{'='*60}")
    print(f"  Argo collocation (serial mode)")
    print(f"  Variables: {variables}")
    print(f"  Window: {win_start} -> {win_end}   "
          f"{len(groups)} group(s)")
    print(f"{'='*60}\n")

    any_output = False
    for var in variables:
        per_group = []
        for gid in groups:
            try:
                nc = _collocate_one_month(
                    cfg, gid, var,
                    win_start, win_end, out_dir)
            except Exception as exc:
                print(f"  [{gid} {var}] ERROR: "
                      f"{type(exc).__name__}: {exc}")
                nc = None
            if nc is not None:
                per_group.append(nc)
        combined = _concat_months(
            var, per_group, out_dir)
        if combined is not None:
            any_output = True
            _write_clean(combined,
                         dist_threshold_m=dist_thresh)

    if any_output:
        (out_dir / "collocate_argo.done").touch()
        print(f"\n  Argo collocation (serial) complete. "
              f"NetCDFs in {out_dir}")
    else:
        print("\n  Argo collocation (serial) produced "
              "no matches.")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def run_collocate_argo(cfg: dict, config_dir=None):
    import shutil
    allow_serial = os.environ.get("ALLOW_NON_SLURM") == "1"

    if allow_serial or shutil.which("sbatch") is None:
        if shutil.which("sbatch") is None:
            print("  [collocate_argo] sbatch not found "
                  "— running serial fallback.")
        run_collocate_argo_serial(cfg)
        return

    from workflow.models.schism.postprocess.submit_collocate_argo import (
        submit_collocate_argo,
    )
    submit_collocate_argo(
        cfg, Path(config_dir) if config_dir else None)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap  = argparse.ArgumentParser(
        description="Argo collocation CLI (SLURM tasks)")
    sub = ap.add_subparsers(dest="stage", required=True)

    pd_p = sub.add_parser(
        "day",
        help="Collocate all variables for one day")
    pd_p.add_argument("--date", required=True,
                      help="Date to collocate (YYYYMMDD)")

    sub.add_parser(
        "merge",
        help="Merge daily outputs + write clean file")

    ap.add_argument("--config", required=True)
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
