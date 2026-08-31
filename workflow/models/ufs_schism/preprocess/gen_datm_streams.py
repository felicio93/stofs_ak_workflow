"""
models/ufs_schism/preprocess/gen_datm_streams.py
================================================
Generate datm.streams YAML file for one month.

Reads fix/datm.streams as a template, substitutes the year, ESMF mesh file
path, and DATM forcing file path, and writes I{ID}/I{ID}_YYYYMM/datm.streams.

Sentinel: I{ID}_YYYYMM/gen_datm_streams.done
"""

import argparse
import sys
from pathlib import Path

from workflow.core.config import load_config, model_dir


def gen_datm_streams_month(cfg: dict, ym: str) -> bool:
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    year = ym[:4]

    template_path = mdir / "fix" / "datm.streams"
    if not template_path.exists():
        print(f"ERROR: Template file not found: {template_path}")
        return False

    datm_subdir = str(cfg.get("datm_subdir", "forcing"))
    datm_tmpl   = str(cfg.get("datm_filename_template", "datm_{YYYYMM}.nc"))

    mesh_file    = f"{datm_subdir}/datm_esmf_mesh.nc"
    forcing_file = f"{datm_subdir}/{datm_tmpl.replace('{YYYYMM}', ym)}"

    out_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "datm.streams"
    sentinel = out_path.parent / "gen_datm_streams.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_datm_streams: {ym} already complete. Skipping.")
        return True

    print(f"--- gen_datm_streams {ym} -> {out_path} ---")

    lines = template_path.read_text().splitlines()
    new_lines = []
    for line in lines:
        if "yearFirst01:" in line:
            new_lines.append(f"yearFirst01:               {year}")
        elif "yearLast01:" in line:
            new_lines.append(f"yearLast01:                {year}")
        elif "yearAlign01:" in line:
            new_lines.append(f"yearAlign01:               {year}")
        elif "stream_mesh_file01:" in line:
            new_lines.append(f'stream_mesh_file01:        "{mesh_file}"')
        elif "stream_data_files01:" in line:
            new_lines.append(f'stream_data_files01:       "{forcing_file}"')
        else:
            new_lines.append(line)

    out_path.write_text("\n".join(new_lines))
    sentinel.touch()
    print(f"  Wrote {out_path}")
    print(f"  Sentinel: {sentinel}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate datm.streams file for a given month.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--month",  required=True, help="YYYYMM")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    if not gen_datm_streams_month(cfg, args.month):
        sys.exit(1)
