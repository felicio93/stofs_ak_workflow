import re
import sys
from pathlib import Path

import netCDF4 as nc4

from workflow.core.config import load_config, model_dir

def gen_datm_in_month(cfg: dict, ym: str):
    pid = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "datm_in.template"
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

    print(f"
--- gen_datm_in {ym} -> {out_path} ---")

    content = template_path.read_text()
    content = re.sub(r"^(model_maskfile\s*=\s*).+$", f"\\1\'{datm_subdir}/datm_esmf_mesh.nc\'", content, flags=re.MULTILINE)
    content = re.sub(r"^(model_meshfile\s*=\s*).+$", f"\\1\'{datm_subdir}/datm_esmf_mesh.nc\'", content, flags=re.MULTILINE)
    content = re.sub(r"^(nx_global\s*=\s*).+$", f"\\1{nx}", content, flags=re.MULTILINE)
    content = re.sub(r"^(ny_global\s*=\s*).+$", f"\\1{ny}", content, flags=re.MULTILINE)

    out_path.write_text(content)
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