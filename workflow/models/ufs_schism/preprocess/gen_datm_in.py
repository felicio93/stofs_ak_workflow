"""
models/ufs_schism/preprocess/gen_datm_in.py
===========================================
Generate datm_in namelist file for one group.

Reads fix/datm_in as a template, substitutes the ESMF mesh file path
and the grid dimensions (nx, ny) read from the DATM forcing NetCDF,
and writes I{ID}/I{ID}_{group_id}/datm_in.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

Sentinel: I{ID}_{group_id}/gen_datm_in.done
"""

import argparse
import sys
from pathlib import Path

import netCDF4 as nc4

from workflow.core.config import (
    load_config,
    model_dir,
    list_groups,
)


def _datm_nc_path(cfg: dict, group_id: str) -> Path:
    """Return the path to the DATM forcing NetCDF for this group."""
    pid      = cfg["project_id"]
    mdir     = model_dir(cfg)
    subdir   = str(cfg.get("datm_subdir", "forcing"))
    tmpl     = str(cfg.get("datm_filename_template",
                            "datm_{YYYYMM}.nc"))
    name     = tmpl.replace("{YYYYMM}", group_id)
    return (mdir / f"I{pid}" / f"I{pid}_{group_id}"
            / subdir / name)


def gen_datm_in_group(cfg: dict, group_id: str) -> bool:
    """Generate datm_in for one group.

    Returns True on success, False on failure.
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "datm_in"
    if not template_path.exists():
        print(f"ERROR: Template file not found: "
              f"{template_path}")
        return False

    datm_nc = _datm_nc_path(cfg, group_id)
    if not datm_nc.exists():
        print(f"ERROR: DATM forcing file not found: {datm_nc}")
        print(f"  Run gen_datm for group {group_id} first.")
        return False

    # Read grid dimensions from the DATM NetCDF
    with nc4.Dataset(str(datm_nc)) as ds:
        nx = ds.dimensions["x"].size
        ny = ds.dimensions["y"].size

    out_path = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                / "datm_in")
    sentinel = out_path.parent / "gen_datm_in.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_datm_in: {group_id} already complete. "
              f"Skipping.")
        return True

    print(f"--- gen_datm_in {group_id} -> {out_path} ---")

    subdir    = str(cfg.get("datm_subdir", "forcing"))
    mesh_file = f"{subdir}/datm_esmf_mesh.nc"

    lines     = template_path.read_text().splitlines()
    new_lines = []
    for line in lines:
        if "model_maskfile" in line:
            new_lines.append(
                f"  model_maskfile = '{mesh_file}'")
        elif "model_meshfile" in line:
            new_lines.append(
                f"  model_meshfile = '{mesh_file}'")
        elif "nx_global" in line:
            new_lines.append(f"  nx_global = {nx}")
        elif "ny_global" in line:
            new_lines.append(f"  ny_global = {ny}")
        else:
            new_lines.append(line)

    out_path.write_text("\n".join(new_lines))
    sentinel.touch()
    print(f"  Wrote {out_path}  (nx={nx}, ny={ny})")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point (all groups)
# =============================================================================

def run_gen_datm_in(cfg: dict):
    """Generate datm_in for every group."""
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_datm_in: {groups[0]} -> {groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"{'='*60}\n")

    for group_id in groups:
        ok = gen_datm_in_group(cfg, group_id)
        if not ok:
            failed.append(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_datm_in complete. No failures.")
    else:
        print(f"  gen_datm_in complete with "
              f"{len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
    print(f"{'='*60}\n")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate datm_in file for a given group.")
    parser.add_argument("--config", required=True)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--group", dest="group_id",
                     help="Group ID (YYYYMM or YYYYMMDD)")
    grp.add_argument("--month", dest="group_id",
                     help="Group ID — legacy alias for --group")
    args = parser.parse_args()
    cfg  = load_config(Path(args.config))
    if not gen_datm_in_group(cfg, args.group_id):
        sys.exit(1)
