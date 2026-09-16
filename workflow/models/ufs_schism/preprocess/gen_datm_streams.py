"""
models/ufs_schism/preprocess/gen_datm_streams.py
================================================
Generate datm.streams file for one group.

Reads fix/datm.streams as a template and substitutes year, mesh file,
and forcing file fields.

Supports two template placeholder styles — whichever is present in the
fix/ file will be substituted:

  Style A — @[KEY] tokens:
      yearFirst01:  @[YYYY_FIRST]
      stream_mesh_file01: "@[DATM_INPUT_DIR]/@[DATM_MESH_FILE]"
      stream_data_files01: "@[DATM_INPUT_DIR]/@[DATM_FORCING_FILE]"

  Style B — keyword: value lines:
      yearFirst01:               2025
      stream_mesh_file01:        "forcing/datm_esmf_mesh.nc"
      stream_data_files01:       "forcing/datm_20250901.nc"

Works for all grouping modes (monthly, ndays/weekly/daily).
Sentinel: I{ID}_{group_id}/gen_datm_streams.done
"""

import argparse
import re
import sys
from pathlib import Path

from workflow.core.config import (
    load_config,
    model_dir,
    group_date_range,
    list_groups,
)


# =============================================================================
# Generic substitution helper (shared pattern)
# =============================================================================

def _substitute(text: str,
                at_key: str,
                kv_key: str,
                new_value: str) -> str:
    """Replace a placeholder regardless of template style.

    Handles @[AT_KEY], key = value, and key: value patterns.
    """
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

def gen_datm_streams_group(cfg: dict,
                            group_id: str) -> bool:
    """Generate datm.streams for one group.
    Returns True on success, False on failure.
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "datm.streams"
    if not template_path.exists():
        print(f"ERROR: Template file not found: "
              f"{template_path}")
        return False

    gstart, gend = group_date_range(cfg, group_id)
    year_first   = gstart.year
    year_last    = gend.year

    subdir       = str(cfg.get("datm_subdir", "forcing"))
    mesh_file    = "datm_esmf_mesh.nc"
    mesh_path    = f"{subdir}/{mesh_file}"
    tmpl         = str(cfg.get("datm_filename_template",
                                "datm_{YYYYMM}.nc"))
    forcing_file = tmpl.replace("{YYYYMM}", group_id)
    forcing_path = f"{subdir}/{forcing_file}"

    out_path = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                / "datm.streams")
    sentinel = out_path.parent / "gen_datm_streams.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_datm_streams: {group_id} already "
              f"complete. Skipping.")
        return True

    print(f"--- gen_datm_streams {group_id} "
          f"({gstart} -> {gend}) -> {out_path} ---")

    text = template_path.read_text()

    # --- Year fields ---
    text = _substitute(text,
                        "YYYY_FIRST",
                        "yearFirst01",
                        str(year_first))
    text = _substitute(text,
                        "YYYY_LAST",
                        "yearLast01",
                        str(year_last))
    # yearAlign always follows yearFirst
    text = _substitute(text,
                        "YYYY_FIRST",
                        "yearAlign01",
                        str(year_first))

    # --- Combined path tokens (must come before individual ones) ---
    # Style A combined: @[DATM_INPUT_DIR]/@[DATM_MESH_FILE]
    text = text.replace(
        f"@[DATM_INPUT_DIR]/@[DATM_MESH_FILE]",
        f'"{mesh_path}"')
    # Style A combined: @[DATM_INPUT_DIR]/@[DATM_FORCING_FILE]
    text = text.replace(
        f"@[DATM_INPUT_DIR]/@[DATM_FORCING_FILE]",
        f'"{forcing_path}"')

    # --- Individual tokens / kv pairs ---
    text = _substitute(text,
                        "DATM_INPUT_DIR",
                        "datm_input_dir",
                        subdir)
    text = _substitute(text,
                        "DATM_MESH_FILE",
                        "stream_mesh_file01",
                        f'"{mesh_path}"')
    text = _substitute(text,
                        "DATM_FORCING_FILE",
                        "stream_data_files01",
                        f'"{forcing_path}"')

    out_path.write_text(text)
    sentinel.touch()
    print(f"  Wrote {out_path}")
    print(f"  yearFirst={year_first}  yearLast={year_last}")
    print(f"  mesh:    {mesh_path}")
    print(f"  forcing: {forcing_path}")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point
# =============================================================================

def run_gen_datm_streams(cfg: dict):
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_datm_streams: {groups[0]} -> {groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"{'='*60}\n")

    for group_id in groups:
        ok = gen_datm_streams_group(cfg, group_id)
        if not ok:
            failed.append(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_datm_streams complete. No failures.")
    else:
        print(f"  gen_datm_streams complete with "
              f"{len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
    print(f"{'='*60}\n")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate datm.streams file for a "
                    "given group.")
    parser.add_argument("--config", required=True)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--group", dest="group_id",
                     help="Group ID (YYYYMM or YYYYMMDD)")
    grp.add_argument("--month", dest="group_id",
                     help="Group ID — legacy alias for --group")
    args = parser.parse_args()
    cfg  = load_config(Path(args.config))
    if not gen_datm_streams_group(cfg, args.group_id):
        sys.exit(1)
