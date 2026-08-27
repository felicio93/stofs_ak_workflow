import shutil
from pathlib import Path

from workflow.core.config import load_config, model_dir, list_months

def copy_noahmptable_to_months(cfg: dict):
    pid = cfg["project_id"]
    mdir = model_dir(cfg)

    source_path = mdir / "fix" / "noahmptable.tbl"
    if not source_path.exists():
        print(f"ERROR: Source file not found: {source_path}")
        return

    print("
--- copy_noahmptable ---")
    for ym in list_months(cfg):
        dest_dir = mdir / f"I{pid}" / f"I{pid}_{ym}"
        dest_path = dest_dir / "noahmptable.tbl"

        if dest_path.exists():
            print(f"  {ym}: noahmptable.tbl already exists. Skipping.")
            continue

        print(f"  Copying to {dest_dir}")
        shutil.copy(source_path, dest_path)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Copy noahmptable.tbl to all monthly input directories.")
    parser.add_argument("--config", required=True, help="Path to the config directory.")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    copy_noahmptable_to_months(config)