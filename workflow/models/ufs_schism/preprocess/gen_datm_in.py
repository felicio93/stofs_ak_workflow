"""
models/ufs_schism/preprocess/gen_datm_in.py
===========================================
Generate datm_in namelist file for one month.

Reads fix/datm_in as a template, substitutes the ESMF mesh file path and
the grid dimensions (nx, ny) read from the DATM forcing NetCDF, and writes
I{ID}/I{ID}_YYYYMM/datm_in.

Sentinel: I{ID}_YYYYMM/gen_datm_in.done
"""

import argparse
import sys
from pathlib import Path

import netCDF4 as nc4

from workflow.core.config import load_config, model_dir


def gen_datm_in_month(cfg: dict, ym: str) -> bool:
    pid = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "datm_in"
    if not template_path.exists():
        print(f"ERROR: Template file not found: {template_path}")
        return False

    datm_subdir = str(cfg.get("datm_subdir", "forcing"))
    datm_tmpl   = str(cfg.get("datm_filename_template", "datm_{YYYYMM}.nc"))
    datm_file   = (mdir / f"I{pid}" / f"I{pid}_{ym}" / datm_subdir
                   / datm_tmpl.replace("{YYYYMM}", ym))

    if not datm_file.exists():
        print(f"ERROR: DATM forcing file not found: {datm_file}")
        return False

    with nc4.Dataset(str(datm_file)) as ds:
        nx = ds.dimensions["x"].size
        ny = ds.dimensions["y"].size

    out_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "datm_in"
    sentinel = out_path.parent / "gen_datm_in.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_datm_in: {ym} already complete. Skipping.")
        return True

    print(f"--- gen_datm_in {ym} -> {out_path} ---")

    lines = template_path.read_text().splitlines()
    new_lines = []
    mesh_file = f"{datm_subdir}/datm_esmf_mesh.nc"
    for line in lines:
        if "model_maskfile" in line:
            new_lines.append(f"  model_maskfile = '{mesh_file}'")
        elif "model_meshfile" in line:
            new_lines.append(f"  model_meshfile = '{mesh_file}'")
        elif "nx_global" in line:
            new_lines.append(f"  nx_global = {nx}")
        elif "ny_global" in line:
            new_lines.append(f"  ny_global = {ny}")
        else:
            new_lines.append(line)

    out_path.write_text("\n".join(new_lines))
    sentinel.touch()
    print(f"  Wrote {out_path} (nx={nx}, ny={ny})")
    print(f"  Sentinel: {sentinel}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate datm_in file for a given month.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--month",  required=True, help="YYYYMM")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    if not gen_datm_in_month(cfg, args.month):
        sys.exit(1)
