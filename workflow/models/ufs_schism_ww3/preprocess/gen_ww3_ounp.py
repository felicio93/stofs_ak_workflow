"""
models/ufs_schism_ww3/preprocess/gen_ww3_ounp.py
=================================================
Phase 3 (interactive) — Generate ww3_ounp.inp for each group run
directory from fix/ww3_ounp.inp template.

The ww3_ounp.inp file uses a fixed-column text format (not namelist).
The only line that changes per group is the first data line:

    YYYYMMDD HHMMSS  cadence.  n_records

Parameters updated per group
-----------------------------
    start time  <- group start date  'YYYYMMDD 000000'
    cadence     <- ww3_point_output_dt from ufs_schism_ww3.yaml
                   (default 1800s, matching DELTC in ww3_shel.nml)
    n_records   <- get_group_ndays(cfg, group_id) * 86400
                   / ww3_point_output_dt

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

Sentinel: I{ID}_{group_id}/gen_ww3_ounp.done
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
# Per-group entry point
# =============================================================================

def gen_ww3_ounp_group(cfg: dict, group_id: str) -> bool:
    """Generate ww3_ounp.inp for one group.
    Returns True on success, False on failure.
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "ww3_ounp.inp"
    if not template_path.exists():
        print(f"ERROR: Template not found: {template_path}")
        return False

    out_path = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                / "ww3_ounp.inp")
    sentinel = out_path.parent / "gen_ww3_ounp.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_ww3_ounp: {group_id} already complete. "
              f"Skipping.")
        return True

    gstart, gend = group_date_range(cfg, group_id)
    ndays        = get_group_ndays(cfg, group_id)

    # Point output cadence from config (default 1800s)
    point_dt = int(cfg.get("ww3_point_output_dt", 1800))

    # Number of output records for this group
    # Use ceiling so the last partial step is always included
    import math
    n_records = math.ceil(ndays * 86400 / point_dt)

    # WW3 time string: 'YYYYMMDD HHMMSS'
    start_str = gstart.strftime("%Y%m%d 000000")

    print(f"--- gen_ww3_ounp {group_id} "
          f"({gstart} -> {gend}, {ndays} days) ---")
    print(f"  start     = {start_str}")
    print(f"  cadence   = {point_dt}s")
    print(f"  n_records = {n_records}")

    # Read template
    text = template_path.read_text()
    lines = text.splitlines(keepends=True)

    # Locate and replace the time/cadence/n_records line.
    # The line matches the pattern:
    #   YYYYMMDD HHMMSS  cadence.  n_records
    # preceded only by comment lines starting with '$'.
    # We identify it as the first non-comment, non-blank line
    # after the file header comments.
    time_line_pattern = re.compile(
        r"^\s*\d{8}\s+\d{6}\s+[\d.]+\s+\d+")

    new_lines = []
    replaced  = False
    for line in lines:
        if not replaced and time_line_pattern.match(line):
            # Replace with updated values, preserving indentation
            indent = re.match(r"^(\s*)", line).group(1)
            new_line = (f"{indent}{start_str}  "
                        f"{float(point_dt):.1f}  "
                        f"{n_records}\n")
            new_lines.append(new_line)
            replaced = True
        else:
            new_lines.append(line)

    if not replaced:
        print(f"  ERROR {group_id}: could not find the "
              f"time/cadence/n_records line in "
              f"{template_path.name}.")
        print(f"  Expected a line matching: "
              f"YYYYMMDD HHMMSS  cadence.  n_records")
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(new_lines))
    sentinel.touch()
    print(f"  Written: {out_path}")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point (all groups)
# =============================================================================

def run_gen_ww3_ounp(cfg: dict):
    """Generate ww3_ounp.inp for every group."""
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_ww3_ounp: {groups[0]} -> {groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"  Template: fix/ww3_ounp.inp")
    print(f"{'='*60}\n")

    prog = ProgressTracker(
        total=len(groups), label="gen_ww3_ounp")

    for group_id in groups:
        ok = gen_ww3_ounp_group(cfg, group_id)
        if not ok:
            failed.append(group_id)
        prog.update(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_ww3_ounp complete. No failures.")
    else:
        print(f"  gen_ww3_ounp complete with "
              f"{len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
    print(f"{'='*60}\n")
