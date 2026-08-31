"""
models/ufs_schism/preprocess/copy_noahmptable.py
=================================================
Copy fix/noahmptable.tbl into each monthly I{ID}_YYYYMM/ directory.

Sentinel: I{ID}_YYYYMM/copy_noahmptable.done
"""

import argparse
import shutil
from pathlib import Path

from workflow.core.config import load_config, model_dir, list_months


def copy_noahmptable_to_months(cfg: dict):
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    source_path = mdir / "fix" / "noahmptable.tbl"
    if not source_path.exists():
        print(f"ERROR: Source file not found: {source_path}")
        return

    print("--- copy_noahmptable ---")
    for ym in list_months(cfg):
        dest_dir  = mdir / f"I{pid}" / f"I{pid}_{ym}"
        dest_path = dest_dir / "noahmptable.tbl"
        sentinel  = dest_dir / "copy_noahmptable.done"

        if sentinel.exists() and dest_path.exists():
            print(f"  {ym}: noahmptable.tbl already present. Skipping.")
            continue

        print(f"  Copying to {dest_dir}")
        shutil.copy(source_path, dest_path)
        sentinel.touch()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Copy noahmptable.tbl to all monthly input directories.")
    parser.add_argument("--config", required=True)
    copy_noahmptable_to_months(load_config(Path(parser.parse_args().config)))
