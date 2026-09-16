"""
models/ufs_schism/preprocess/gen_datm_streams.py
================================================
Generate datm.streams YAML file for one group.

Reads fix/datm.streams as a template, substitutes the year, ESMF mesh
file path, and DATM forcing file path, and writes
I{ID}/I{ID}_{group_id}/datm.streams.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

For ndays groups that span a year boundary (e.g. group starting
20251228 with group_ndays=7 ending 20260103), yearFirst and yearLast
are set from the group start and end years respectively so the DATM
stream covers the full group date range.

Sentinel: I{ID}_{group_id}/gen_datm_streams.done
"""

import argparse
import sys
from pathlib import Path

from workflow.core.config import (
    load_config,
    model_dir,
    group_date_range,
    list_groups,
)


def gen_datm_streams_group(cfg: dict, group_id: str) -> bool:
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

    # yearFirst / yearLast / yearAlign from group date range.
    # For groups that span a year boundary, yearFirst != yearLast.
    year_first = gstart.year
    year_last  = gend.year
    # yearAlign is the year the stream data alignment starts —
    # always the group start year.
    year_align = gstart.year

    subdir   = str(cfg.get("datm_subdir", "forcing"))
    tmpl     = str(cfg.get("datm_filename_template",
                            "datm_{YYYYMM}.nc"))
    name     = tmpl.replace("{YYYYMM}", group_id)

    mesh_file    = f"{subdir}/datm_esmf_mesh.nc"
    forcing_file = f"{subdir}/{name}"

    out_path = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                / "datm.streams")
    sentinel = out_path.parent / "gen_datm_streams.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_datm_streams: {group_id} already "
              f"complete. Skipping.")
        return True

    print(f"--- gen_datm_streams {group_id} "
          f"({gstart} -> {gend}) -> {out_path} ---")

    lines     = template_path.read_text().splitlines()
    new_lines = []
    for line in lines:
        if "yearFirst01:" in line:
            new_lines.append(
                f"yearFirst01:               {year_first}")
        elif "yearLast01:" in line:
            new_lines.append(
                f"yearLast01:                {year_last}")
        elif "yearAlign01:" in line:
            new_lines.append(
                f"yearAlign01:               {year_align}")
        elif "stream_mesh_file01:" in line:
            new_lines.append(
                f'stream_mesh_file01:        "{mesh_file}"')
        elif "stream_data_files01:" in line:
            new_lines.append(
                f'stream_data_files01:       "{forcing_file}"')
        else:
            new_lines.append(line)

    out_path.write_text("\n".join(new_lines))
    sentinel.touch()
    print(f"  Wrote {out_path}")
    print(f"  yearFirst={year_first}  yearLast={year_last}  "
          f"yearAlign={year_align}")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point (all groups)
# =============================================================================

def run_gen_datm_streams(cfg: dict):
    """Generate datm.streams for every group."""
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
