"""
models/ufs_schism_ww3/preprocess/gen_ww3_shel.py
=================================================
Phase 3 (interactive) — Generate ww3_shel.nml for each group run
directory from fix/ww3_shel.nml template.

Parameters updated per group
-----------------------------
    domain%start   <- group start date  'YYYYMMDD 000000'
    domain%stop    <- group end date + 1 day  'YYYYMMDD 000000'
    date%field     <- same start/stop, cadence 3600s (wtiminc in param.nml)
    date%point     <- same start/stop, cadence ww3_point_output_dt (default 1800s)
    date%restart   <- same start/stop, cadence nhot_write*dt (end of group)
                      reads nhot_write and dt from per-group param.nml
                      (NOT from fix/param.nml which has the template value)

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

Sentinel: I{ID}_{group_id}/gen_ww3_shel.done
"""

import re
import sys
from datetime import timedelta
from pathlib import Path

from workflow.core.config import (
    model_dir,
    list_groups,
    ProgressTracker,
    group_date_range,
    get_group_ndays,
)


# =============================================================================
# Time helpers
# =============================================================================

def _ww3_time(d) -> str:
    """Format a date as WW3 time string 'YYYYMMDD 000000'."""
    return d.strftime("%Y%m%d 000000")


def _compute_times(cfg: dict, group_id: str) -> tuple:
    """Return (start_str, stop_str, restart_dt) for a group.

    stop  = group end date + 1 day (read-ahead bracket so WW3 never
            runs out of time window at the final timestep).
    restart_dt = nhot_write * dt  [seconds] — one restart written at
                 end of group, matching SCHISM hotstart cadence.

    Reads nhot_write and dt from the per-group param.nml
    (I{ID}_{group_id}/param.nml) which has the correct per-group
    nhot_write set by gen_param. Falls back to fix/param.nml only
    if the per-group file does not yet exist.
    """
    gstart, gend = group_date_range(cfg, group_id)

    # One day past the last simulation day
    stop_date = gend + timedelta(days=1)

    start_str = _ww3_time(gstart)
    stop_str  = _ww3_time(stop_date)

    # Read nhot_write and dt from per-group param.nml
    pid        = cfg["project_id"]
    mdir       = model_dir(cfg)

    # Prefer per-group param.nml (has correct nhot_write from gen_param)
    # Fall back to fix/param.nml if per-group not yet generated
    param_path = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                  / "param.nml")
    if not param_path.exists():
        param_path = mdir / "fix" / "param.nml"
        print(f"  WARNING: per-group param.nml not found for "
              f"{group_id}, falling back to fix/param.nml. "
              f"Run gen_param before gen_ww3_shel.")

    nhot_write = None
    dt         = None

    if param_path.exists():
        text = param_path.read_text()
        m = re.search(
            r'^\s*nhot_write\s*=\s*([^\s!]+)',
            text, re.IGNORECASE | re.MULTILINE)
        if m:
            nhot_write = int(float(m.group(1)))
        m = re.search(
            r'^\s*dt\s*=\s*([^\s!]+)',
            text, re.IGNORECASE | re.MULTILINE)
        if m:
            dt = float(m.group(1))

    if nhot_write is None or dt is None:
        print(f"  ERROR: could not read nhot_write/dt from "
              f"{param_path}")
        sys.exit(1)

    restart_dt = int(nhot_write * dt)
    print(f"  nhot_write={nhot_write}, dt={dt}s "
          f"-> restart_dt={restart_dt}s "
          f"({restart_dt/86400:.2f} days)")
    return start_str, stop_str, restart_dt


# =============================================================================
# Substitution helper
# =============================================================================

def _substitute(text: str, key: str,
                new_value: str) -> str:
    """Replace the value of key in a namelist-style or
    free-format text file.

    Handles both:
      key = 'old_value'   (namelist style)
      key = old_value     (no quotes)
    Replaces the entire RHS up to the end of the line,
    preserving any trailing comment.
    """
    pattern = re.compile(
        r"(^\s*" + re.escape(key) + r"\s*=\s*)([^\n]*)",
        re.IGNORECASE | re.MULTILINE,
    )

    def replacer(m):
        prefix = m.group(1)
        rest   = m.group(2)
        # Preserve trailing comment if present
        comment_match = re.search(r"(\s*!.*)$", rest)
        comment = comment_match.group(1) if comment_match else ""
        return prefix + new_value + comment

    return pattern.sub(replacer, text)


# =============================================================================
# Per-group entry point
# =============================================================================

def gen_ww3_shel_group(cfg: dict, group_id: str) -> bool:
    """Generate ww3_shel.nml for one group.
    Returns True on success, False on failure.
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "ww3_shel.nml"
    if not template_path.exists():
        print(f"ERROR: Template not found: {template_path}")
        return False

    out_path = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                / "ww3_shel.nml")
    sentinel = out_path.parent / "gen_ww3_shel.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_ww3_shel: {group_id} already complete. "
              f"Skipping.")
        return True

    gstart, gend  = group_date_range(cfg, group_id)
    ndays         = get_group_ndays(cfg, group_id)
    start_str, stop_str, restart_dt = _compute_times(
        cfg, group_id)

    # Point output cadence from config (default 1800s)
    point_dt = int(cfg.get("ww3_point_output_dt", 1800))

    # Field output cadence: wtiminc from param.nml (default 3600s)
    field_dt = int(cfg.get("ww3_field_output_dt", 3600))

    print(f"--- gen_ww3_shel {group_id} "
          f"({gstart} -> {gend}, {ndays} days) ---")
    print(f"  domain%start  = '{start_str}'")
    print(f"  domain%stop   = '{stop_str}'")
    print(f"  field cadence = {field_dt}s")
    print(f"  point cadence = {point_dt}s")
    print(f"  restart dt    = {restart_dt}s")

    text = template_path.read_text()

    # --- domain block ---
    text = _substitute(text, "domain%start",
                        f"'{start_str}'")
    text = _substitute(text, "domain%stop",
                        f"'{stop_str}'")

    # --- output_date_nml block ---
    def _date_line(cadence: int) -> str:
        return (f"'{start_str}' '{cadence}' '{stop_str}'")

    text = _substitute(text, "date%field",
                        _date_line(field_dt))
    text = _substitute(text, "date%point",
                        _date_line(point_dt))
    text = _substitute(text, "date%restart",
                        _date_line(restart_dt))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text)
    sentinel.touch()
    print(f"  Written: {out_path}")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point (all groups)
# =============================================================================

def run_gen_ww3_shel(cfg: dict):
    """Generate ww3_shel.nml for every group."""
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_ww3_shel: {groups[0]} -> {groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"  Template: fix/ww3_shel.nml")
    print(f"  NOTE: reads nhot_write from per-group param.nml")
    print(f"        (gen_param must run before gen_ww3_shel)")
    print(f"{'='*60}\n")

    prog = ProgressTracker(
        total=len(groups), label="gen_ww3_shel")

    for group_id in groups:
        ok = gen_ww3_shel_group(cfg, group_id)
        if not ok:
            failed.append(group_id)
        prog.update(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_ww3_shel complete. No failures.")
    else:
        print(f"  gen_ww3_shel complete with "
              f"{len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
    print(f"{'='*60}\n")
