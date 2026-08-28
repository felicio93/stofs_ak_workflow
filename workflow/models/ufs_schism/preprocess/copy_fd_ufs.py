import shutil
from pathlib import Path

from workflow.core.config import load_config, model_dir, list_months

def copy_fd_ufs_to_months(cfg: dict):
    pid = cfg["project_id"]
    mdir = model_dir(cfg)

    source_path = mdir / "fix" / "fd_ufs.yaml"
    if not source_path.exists():
        print(f"ERROR: Source file not found: {source_path}")
        return

    print("--- copy_fd_ufs ---")
    for ym in list_months(cfg):
        dest_dir = mdir / f"I{pid}" / f"I{pid}_{ym}"
        dest_path = dest_dir / "fd_ufs.yaml"

        if dest_path.exists():
            print(f"  {ym}: fd_ufs.yaml already exists. Skipping.")
            continue

        print(f"  Copying to {dest_dir}")
        shutil.copy(source_path, dest_path)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Copy fd_ufs.yaml to all monthly input directories.")
    parser.add_argument("--config", required=True, help="Path to the config directory.")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    copy_fd_ufs_to_months(config)
