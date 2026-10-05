"""
models/ufs_schism/preprocess/gen_datm_in.py
===========================================
Generate datm_in namelist file for one group.

Reads fix/datm_in as a template and substitutes the ESMF mesh file
path and the grid dimensions (nx, ny) read from the DATM forcing NetCDF.

Supports two template placeholder styles — whichever is present in the
fix/ file will be substituted; the other is silently ignored:

  Style A — @[KEY] tokens (e.g. CMEPS/UFS convention):
      model_meshfile = "@[DATM_INPUT_DIR]/@[DATM_MESH_FILE]"
      nx_global = @[NX_GLOBAL]

  Style B — keyword = value lines (legacy workflow convention):
      model_meshfile = 'forcing/datm_esmf_mesh.nc'
      nx_global = 384

skip_restart_read
-----------------
For groups 2+ (start_type = continue), skip_restart_read = .true. is
added to bypass the DATM restart read. This is required because
datamode=ATMMESH has a CDEPS bug where ATMMESH is present in the
restart WRITE and ADVANCE case lists but missing from the restart READ
case list, causing a "datamode ATMMESH not recognized" error when
start_type=continue. Since DATM reads forcing from pre-computed NetCDF
files (not a prognostic state), skipping the restart read is safe —
DATM simply re-initializes its time interpolation from the correct
group start date.

Works for all grouping modes (monthly, ndays/weekly/daily).
Sentinel: I{ID}_{group_id}/gen_datm_in.done
"""

import argparse
import re
import sys
from pathlib import Path

import netCDF4 as nc4

from workflow.core.config import (
    load_config,
    model_dir,
    list_groups,
)


# =============================================================================
# Generic substitution helper
# =============================================================================

def _substitute(text: str,
                at_key: str,
                kv_key: str,
                new_value: str) -> str:
    """Replace a placeholder in text regardless of template style."""
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
# Path helper
# =============================================================================

def _datm_nc_path(cfg: dict, group_id: str) -> Path:
    """Return the path to the DATM forcing NetCDF for this group."""
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    subdir = str(cfg.get("datm_subdir", "forcing"))
    tmpl   = str(cfg.get("datm_filename_template",
                          "datm_{YYYYMM}.nc"))
    name   = tmpl.replace("{YYYYMM}", group_id)
    return (mdir / f"I{pid}" / f"I{pid}_{group_id}"
            / subdir / name)


# =============================================================================
# Per-group entry point
# =============================================================================

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
        print(f"ERROR: DATM forcing file not found: "
              f"{datm_nc}")
        print(f"  Run gen_datm for group {group_id} first.")
        return False

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

    # Determine if this is the first group
    groups   = list_groups(cfg)
    is_first = (group_id == groups[0])

    print(f"--- gen_datm_in {group_id} -> {out_path} ---")

    subdir    = str(cfg.get("datm_subdir", "forcing"))
    mesh_file = "datm_esmf_mesh.nc"
    mesh_path = f"{subdir}/{mesh_file}"

    text = template_path.read_text()

    # When @[DATM_INPUT_DIR]/@[DATM_MESH_FILE] appear together
    combined_at = (f"@[DATM_INPUT_DIR]"
                   f"/@[DATM_MESH_FILE]")
    text = text.replace(combined_at, f'"{mesh_path}"')

    text = _substitute(text,
                        "DATM_INPUT_DIR",
                        "datm_input_dir",
                        subdir)
    text = _substitute(text,
                        "DATM_MESH_FILE",
                        "datm_mesh_file",
                        mesh_file)
    text = _substitute(text,
                        "NX_GLOBAL",
                        "nx_global",
                        str(nx))
    text = _substitute(text,
                        "NY_GLOBAL",
                        "ny_global",
                        str(ny))
    text = _substitute(text,
                        "DATM_MESH_PATH",
                        "model_maskfile",
                        f"'{mesh_path}'")
    text = _substitute(text,
                        "DATM_MESH_PATH",
                        "model_meshfile",
                        f"'{mesh_path}'")

    # For groups 2+, add skip_restart_read = .true. to bypass the DATM
    # restart read (ATMMESH datamode has a CDEPS bug where it is missing
    # from the restart READ case list — see module docstring).
    if not is_first:
        text = text.replace(
            '  restfilm = "null"',
            '  restfilm = "null"\n  skip_restart_read = .true.'
        )
        print(f"  Added skip_restart_read = .true. "
              f"(group 2+: ATMMESH restart read bug workaround)")

    out_path.write_text(text)
    sentinel.touch()
    print(f"  Wrote {out_path}  (nx={nx}, ny={ny})")
    print(f"  mesh path: {mesh_path}")
    print(f"  is_first:  {is_first}")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point
# =============================================================================

def run_gen_datm_in(cfg: dict):
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_datm_in: {groups[0]} -> {groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"  Group 1: no skip_restart_read (cold start)")
    print(f"  Groups 2+: skip_restart_read=.true. "
          f"(ATMMESH restart read bug workaround)")
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
