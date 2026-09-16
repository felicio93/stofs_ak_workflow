"""
models/ufs_schism/preprocess/gen_model_configure.py
====================================================
Generate model_configure file for one group.

Reads fix/model_configure as a template and substitutes start date,
forecast length, and atmospheric time step.

Supports two template placeholder styles:

  Style A — @[KEY] tokens:
      start_year:   @[YYYY]
      nhours_fcst:  @[NHOURS]
      dt_atmos:     @[DT_ATMOS]

  Style B — keyword: value lines:
      start_year:              2025
      nhours_fcst:             48
      dt_atmos:                720

Works for all grouping modes (monthly, ndays/weekly/daily).
Sentinel: I{ID}_{group_id}/gen_model_configure.done
"""

import argparse
import re
import sys
from pathlib import Path

from workflow.core.config import (
    load_config,
    model_dir,
    group_date_range,
    get_group_ndays,
    list_groups,
)


# =============================================================================
# Generic substitution helper
# =============================================================================

def _substitute(text: str,
                at_key: str,
                kv_key: str,
                new_value: str) -> str:
    """Replace a placeholder regardless of template style."""
    # Style A
    text = text.replace(f"@[{at_key}]", new_value)

    # Style B =
    text = re.sub(
        r'(^\s*' + re.escape(kv_key) + r'\s*=\s*)([^\n]*)',
        lambda m: m.group(1) + new_value,
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    # Style B :
    text = re.sub(
        r'(^\s*' + re.escape(kv_key) + r'\s*:\s*)([^\n]*)',
        lambda m: m.group(1) + new_value,
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    return text


# =============================================================================
# Per-group entry point
# =============================================================================

def gen_model_configure_group(cfg: dict,
                               group_id: str) -> bool:
    """Generate model_configure for one group.
    Returns True on success, False on failure.
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "model_configure"
    if not template_path.exists():
        print(f"ERROR: Template file not found: "
              f"{template_path}")
        return False

    gstart, _ = group_date_range(cfg, group_id)
    ndays     = get_group_ndays(cfg, group_id)
    nhours    = ndays * 24
    dt_atmos  = int(cfg.get("dt_atmos", 720))

    out_path = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                / "model_configure")
    sentinel = out_path.parent / "gen_model_configure.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_model_configure: {group_id} already "
              f"complete. Skipping.")
        return True

    print(f"--- gen_model_configure {group_id} "
          f"({gstart}, {ndays} days) -> {out_path} ---")

    text = template_path.read_text()

    text = _substitute(text, "YYYY",     "start_year",
                        str(gstart.year))
    text = _substitute(text, "MM",       "start_month",
                        f"{gstart.month:02d}")
    text = _substitute(text, "DD",       "start_day",
                        f"{gstart.day:02d}")
    text = _substitute(text, "HH",       "start_hour",
                        "0")
    text = _substitute(text, "NHOURS",   "nhours_fcst",
                        str(nhours))
    text = _substitute(text, "DT_ATMOS", "dt_atmos",
                        str(dt_atmos))

    out_path.write_text(text)
    sentinel.touch()
    print(f"  Wrote {out_path}")
    print(f"  start={gstart}  nhours={nhours}  "
          f"dt_atmos={dt_atmos}")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point
# =============================================================================

def run_gen_model_configure(cfg: dict):
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_model_configure: {groups[0]} -> "
          f"{groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"{'='*60}\n")

    for group_id in groups:
        ok = gen_model_configure_group(cfg, group_id)
        if not ok:
            failed.append(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_model_configure complete. No failures.")
    else:
        print(f"  gen_model_configure complete with "
              f"{len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
    print(f"{'='*60}\n")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate model_configure file for a "
                    "given group.")
    parser.add_argument("--config", required=True)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--group", dest="group_id",
                     help="Group ID (YYYYMM or YYYYMMDD)")
    grp.add_argument("--month", dest="group_id",
                     help="Group ID — legacy alias for --group")
    args = parser.parse_args()
    cfg  = load_config(Path(args.config))
    if not gen_model_configure_group(cfg, args.group_id):
        sys.exit(1)
