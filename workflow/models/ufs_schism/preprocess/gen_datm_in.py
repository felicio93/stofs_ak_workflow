import sys
from pathlib import Path

import netCDF4 as nc4

from workflow.core.config import load_config, model_dir

def gen_datm_in_month(cfg: dict, ym: str):
    pid = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "datm_in"
    if not template_path.exists():
        print(f"ERROR: Template file not found: {template_path}")
        sys.exit(1)

    datm_subdir = cfg.get("datm_subdir", "forcing")
    datm_dir = mdir / f"I{pid}" / f"I{pid}_{ym}" / datm_subdir
    datm_file = datm_dir / f"datm_{ym}.nc"

    if not datm_file.exists():
        print(f"ERROR: DATM forcing file not found: {datm_file}")
        sys.exit(1)

    with nc4.Dataset(datm_file) as ds:
        nx = ds.dimensions["x"].size
        ny = ds.dimensions["y"].size

    out_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "datm_in"
    sentinel = out_path.parent / "gen_datm_in.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_datm_in: {ym} already complete. Skipping.")
        return

    print(f"--- gen_datm_in {ym} -> {out_path} ---")

    lines = template_path.read_text().splitlines()
    new_lines = []
    for line in lines:
        if "model_maskfile" in line:
            new_lines.append(f"  model_maskfile = '{datm_subdir}/datm_esmf_mesh.nc'")
        elif "model_meshfile" in line:
            new_lines.append(f"  model_meshfile = '{datm_subdir}/datm_esmf_mesh.nc'")
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

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate datm_in file for a given month.")
    parser.add_argument("--config", required=True, help="Path to the config directory.")
    parser.add_argument("--month", required=True, help="Month in YYYYMM format.")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    gen_datm_in_month(config, args.month)
