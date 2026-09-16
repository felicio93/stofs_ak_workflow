"""
models/ufs_schism/preprocess/copy_fd_ufs.py
============================================
Copy fix/fd_ufs.yaml into each group I{ID}_{group_id}/ directory.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

Sentinel: I{ID}_{group_id}/copy_fd_ufs.done
"""

import argparse
import shutil
from pathlib import Path

from workflow.core.config import (
    load_config,
    model_dir,
    list_groups,
)


def copy_fd_ufs_to_groups(cfg: dict):
    """Copy fix/fd_ufs.yaml into every group input directory."""
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    source_path = mdir / "fix" / "fd_ufs.yaml"
    if not source_path.exists():
        print(f"ERROR: Source file not found: {source_path}")
        return

    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")

    print(f"\n--- copy_fd_ufs ({grouping}, "
          f"{len(groups)} group(s)) ---")

    for group_id in groups:
        dest_dir  = (mdir / f"I{pid}" / f"I{pid}_{group_id}")
        dest_path = dest_dir / "fd_ufs.yaml"
        sentinel  = dest_dir / "copy_fd_ufs.done"

        if sentinel.exists() and dest_path.exists():
            print(f"  {group_id}: fd_ufs.yaml already present. "
                  f"Skipping.")
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        print(f"  {group_id}: copying fd_ufs.yaml -> "
              f"{dest_dir}")
        shutil.copy(source_path, dest_path)
        sentinel.touch()

    print("  copy_fd_ufs complete.")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Copy fd_ufs.yaml to all group input "
                    "directories.")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    copy_fd_ufs_to_groups(load_config(Path(args.config)))
