"""
models/ufs_schism/preprocess/copy_modulefiles.py
================================================
Copy all *.lua modulefiles from bin/ into each group
I{ID}_{group_id}/modulefiles/ directory.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

Sentinel: I{ID}_{group_id}/modulefiles/copy_modulefiles.done
"""

import argparse
import shutil
from pathlib import Path

from workflow.core.config import (
    load_config,
    model_dir,
    list_groups,
)


def copy_modulefiles_to_groups(cfg: dict):
    """Copy all *.lua files from bin/ into every group's
    modulefiles/ subdirectory.
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    source_dir = mdir / "bin"
    if not source_dir.is_dir():
        print(f"ERROR: Source directory not found: "
              f"{source_dir}")
        return

    lua_files = list(source_dir.glob("*.lua"))
    if not lua_files:
        print("No .lua files found in bin/. "
              "Skipping copy_modulefiles.")
        return

    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")

    print(f"\n--- copy_modulefiles ({grouping}, "
          f"{len(groups)} group(s), "
          f"{len(lua_files)} .lua file(s)) ---")

    for group_id in groups:
        dest_dir = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                    / "modulefiles")
        sentinel = dest_dir / "copy_modulefiles.done"

        if sentinel.exists():
            print(f"  {group_id}: modulefiles already present. "
                  f"Skipping.")
            continue

        dest_dir.mkdir(parents=True, exist_ok=True)
        print(f"  {group_id}: copying {len(lua_files)} "
              f".lua file(s) -> {dest_dir}")
        for file_path in lua_files:
            shutil.copy(file_path, dest_dir)
        sentinel.touch()

    print("  copy_modulefiles complete.")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Copy .lua modulefiles to all group input "
                    "directories.")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    copy_modulefiles_to_groups(
        load_config(Path(args.config)))
