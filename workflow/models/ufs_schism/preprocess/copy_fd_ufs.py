"""
models/ufs_schism/preprocess/copy_fd_ufs.py
============================================
Copy fix/fd_ufs.yaml into each monthly I{ID}_YYYYMM/ directory.

Sentinel: I{ID}_YYYYMM/copy_fd_ufs.done
"""

import argparse
import shutil
from pathlib import Path

from workflow.core.config import load_config, model_dir, list_months


def copy_fd_ufs_to_months(cfg: dict):
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    source_path = mdir / "fix" / "fd_ufs.yaml"
    if not source_path.exists():
        print(f"ERROR: Source file not found: {source_path}")
        return

    print("--- copy_fd_ufs ---")
    for ym in list_months(cfg):
        dest_dir  = mdir / f"I{pid}" / f"I{pid}_{ym}"
        dest_path = dest_dir / "fd_ufs.yaml"
        sentinel  = dest_dir / "copy_fd_ufs.done"

        if sentinel.exists() and dest_path.exists():
            print(f"  {ym}: fd_ufs.yaml already present. Skipping.")
            continue

        print(f"  Copying to {dest_dir}")
        shutil.copy(source_path, dest_path)
        sentinel.touch()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Copy fd_ufs.yaml to all monthly input directories.")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    copy_fd_ufs_to_months(load_config(Path(args.config)))
