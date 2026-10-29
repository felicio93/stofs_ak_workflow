"""
models/schism/preprocess/gen_param.py
=====================================
Phase 3 (interactive) — Copy fix/param.nml into each I{ID}_{group_id}/
directory with the date and timing parameters updated for that specific group.

Parameters updated per group
-----------------------------
    start_year   <- year of the group start date
    start_month  <- month of the group start date
    start_day    <- day-of-month of the group start date (all groups)
    start_hour   <- 0 for all groups except the first (which keeps the
                    template value)
    rnday        <- number of simulation days in the group
    nhot_write   <- int(rnday * 86400 / dt)
    ihot         <- 0 for the first group (cold start, keeps template value)
                    1 for all subsequent groups (hotstart)

Parameters read from the template (not modified)
-------------------------------------------------
    dt           <- read to compute nhot_write
    all others   <- preserved exactly as in the template

Grouping awareness
------------------
    monthly  : rnday = calendar days in the month (28-31)
               start_day always 1 for months after the first
    ndays    : rnday = cfg['group_ndays'] (or fewer for the last group if
               it is truncated by end_date)
               start_day derived from the group start date

Resume-safe: groups whose param.nml already exists are skipped.
"""

import re
import sys
from pathlib import Path

from workflow.core.config import (
    model_dir,
    list_groups,
    ProgressTracker,
    group_date_range,
    get_group_ndays,
)


# =============================================================================
# Namelist helpers
# =============================================================================

def _read_nml_value(text: str, param: str):
    """Return the value string for `param` from namelist text, or None."""
    pattern = re.compile(
        r'^\s*' + re.escape(param) + r'\s*=\s*([^\s!]+)',
        re.IGNORECASE | re.MULTILINE
    )
    m = pattern.search(text)
    return m.group(1) if m else None


def _set_nml_value(text: str, param: str, value) -> str:
    """Replace the value of `param` in namelist text, preserving the rest
    of the line (including any trailing ! comment).
    """
    pattern = re.compile(
        r'(^\s*' + re.escape(param) + r'\s*=\s*)([^\s!]+)',
        re.IGNORECASE | re.MULTILINE
    )
    new_text, n = pattern.subn(
        lambda m: m.group(1) + str(value), text)
    if n == 0:
        print(f"  WARNING: parameter '{param}' not found in param.nml — "
              f"could not set to {value}.")
    return new_text


# =============================================================================
# Per-group processor
# =============================================================================

def _process_group(group_id: str, cfg: dict, template_text: str,
                   dt: float, mdir: Path, is_first: bool) -> bool:
    """Write I{ID}/I{ID}_{group_id}/param.nml for one group.

    All groups: start_year, start_month, start_day, rnday and nhot_write.
    First group: start_hour and ihot are kept from the template (the
    user's cold-start values).
    All subsequent groups: start_hour=0 and ihot=1 are enforced.

    Returns True on success, False on failure.
    """
    pid          = cfg["project_id"]
    gstart, _    = group_date_range(cfg, group_id)
    ndays        = get_group_ndays(cfg, group_id)

    out_dir = mdir / f"I{pid}" / f"I{pid}_{group_id}"
    out_nml = out_dir / "param.nml"

    if out_nml.exists() and out_nml.stat().st_size > 0:
        print(f"  {group_id}: param.nml already exists, skipping.")
        return True

    if not out_dir.exists():
        print(f"  ERROR {group_id}: output directory {out_dir} does not "
              f"exist. Run --init first.")
        return False

    # nhot_write = total timesteps for this group
    total_steps = int(ndays * 86400 // dt)
    if (ndays * 86400) % dt != 0:
        print(f"  {group_id}: NOTE nhot_write = {total_steps} "
              f"(rnday*86400/dt = {ndays * 86400 / dt:.4f} — "
              f"not exact integer, using floor)")

    text = template_text

    # Always update these
    text = _set_nml_value(text, "start_year",  gstart.year)
    text = _set_nml_value(text, "start_month", gstart.month)
    text = _set_nml_value(text, "start_day",   gstart.day)
    text = _set_nml_value(text, "rnday",       ndays)
    text = _set_nml_value(text, "nhot_write",  total_steps)

    if is_first:
        # Keep template's start_hour and ihot (cold-start values)
        hour_str = _read_nml_value(text, "start_hour") or "?"
        ihot_str = _read_nml_value(text, "ihot")       or "?"
        print(f"  {group_id}: first group — keeping template values: "
              f"start_hour={hour_str}, ihot={ihot_str}")
    else:
        # Enforce hotstart values for all subsequent groups
        text = _set_nml_value(text, "start_hour", 0)
        text = _set_nml_value(text, "ihot",        1)

    out_nml.write_text(text)
    print(f"  {group_id}: param.nml written  "
          f"(start={gstart}, rnday={ndays}, "
          f"nhot_write={total_steps})")
    return True


# =============================================================================
# Main entry point
# =============================================================================

def run_gen_param(cfg: dict):
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    fix  = mdir / "fix"

    template = fix / "param.nml"
    if not template.exists():
        print("ERROR: fix/param.nml not found.")
        print(f"  Copy your calibrated param.nml to {template} "
              "before running gen_param.")
        sys.exit(1)

    template_text = template.read_text()

    # ------------------------------------------------------------------
    # Read dt from template
    # ------------------------------------------------------------------
    dt_str = _read_nml_value(template_text, "dt")
    if dt_str is None:
        print("ERROR: 'dt' not found in fix/param.nml. "
              "Cannot compute nhot_write.")
        sys.exit(1)
    try:
        dt = float(dt_str)
    except ValueError:
        print(f"ERROR: could not parse dt value '{dt_str}' as a number.")
        sys.exit(1)
    print(f"  Template dt = {dt} s")

    # ------------------------------------------------------------------
    # Check ihot
    # ------------------------------------------------------------------
    ihot_str = _read_nml_value(template_text, "ihot")
    if ihot_str is not None and ihot_str.strip() not in ("0", "1", "2"):
        print(f"  WARNING: ihot = {ihot_str} in fix/param.nml — "
              f"unrecognised value.")
    if ihot_str is not None and ihot_str.strip() == "2":
        # schism_init.F90: with ihot=2 the station output is appended to an
        # existing outputs/staout.nc (nf90_open, errors unchecked); the first
        # group runs in a fresh run directory, so it gets no station output.
        print("  WARNING: ihot = 2 in fix/param.nml: SCHISM (since 2026-10-19) "
              "only appends station output to an existing outputs/staout.nc, "
              "so the first group would have no station output. Use ihot = 1 "
              "for a hotstart made by gen_hotstart.")

    # ------------------------------------------------------------------
    # Check nhot
    # ------------------------------------------------------------------
    nhot_str = _read_nml_value(template_text, "nhot")
    if nhot_str is not None and nhot_str.strip() != "1":
        print(f"  WARNING: nhot = {nhot_str} in fix/param.nml. "
              f"Set nhot = 1 to enable hotstart output.")

    # ------------------------------------------------------------------
    # Process groups
    # ------------------------------------------------------------------
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    prog     = ProgressTracker(total=len(groups))
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_param: {groups[0]} -> {groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"  Template: fix/param.nml  |  dt = {dt} s")
    print(f"  start_year/month/day from each group's start date")
    print(f"  First group ({groups[0]}): start_hour/ihot kept from template")
    print(f"  All others: start_hour=0, ihot=1 enforced")
    print(f"{'='*60}\n")

    for i, group_id in enumerate(groups):
        ok = _process_group(group_id, cfg, template_text, dt, mdir,
                            is_first=(i == 0))
        if not ok:
            failed.append(group_id)
        prog.update(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_param complete. No failures.")
    else:
        print(f"  gen_param complete with {len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
        print("  Re-run to retry (existing files are skipped).")
    print(f"{'='*60}\n")
