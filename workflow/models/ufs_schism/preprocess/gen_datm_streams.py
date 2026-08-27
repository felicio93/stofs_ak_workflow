import re
import sys
from pathlib import Path

from workflow.core.config import load_config, model_dir

def gen_datm_streams_month(cfg: dict, ym: str):
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    year = ym[:4]

    template_path = mdir / "fix" / "datm.streams.template"
    if not template_path.exists():
        print(f"ERROR: Template file not found: {template_path}")
        sys.exit(1)

    datm_subdir = cfg.get("datm_subdir", "forcing")
    datm_dir = mdir / f"I{pid}" / f"I{pid}_{ym}" / datm_subdir

    mesh_file = f'{datm_subdir}/datm_esmf_mesh.nc'
    forcing_file = f'{datm_subdir}/datm_{ym}.nc'

    out_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "datm.streams"
    sentinel = out_path.parent / "gen_datm_streams.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_datm_streams: {ym} already complete. Skipping.")
        return

    print(f"
--- gen_datm_streams {ym} -> {out_path} ---")

    content = template_path.read_text()
    content = re.sub(r"^(yearFirst01\s*=\s*).+$", f"\\1{year}", content, flags=re.MULTILINE)
    content = re.sub(r"^(yearLast01\s*=\s*).+$", f"\\1{year}", content, flags=re.MULTILINE)
    content = re.sub(r"^(yearAlign01\s*=\s*).+$", f"\\1{year}", content, flags=re.MULTILINE)
    content = re.sub(r"^(stream_mesh_file01\s*=\s*).+$", f"\\1\"{mesh_file}\"", content, flags=re.MULTILINE)
    content = re.sub(r"^(stream_data_files01\s*=\s*).+$", f"\\1\"{forcing_file}\"", content, flags=re.MULTILINE)

    out_path.write_text(content)
    sentinel.touch()

    print(f"  Wrote {out_path}")
    print(f"  Sentinel: {sentinel}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate datm.streams file for a given month.")
    parser.add_argument("--config", required=True, help="Path to the config directory.")
    parser.add_argument("--month", required=True, help="Month in YYYYMM format.")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    gen_datm_streams_month(config, args.month)