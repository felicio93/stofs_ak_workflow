import shutil
from pathlib import Path

from workflow.core.config import load_config, model_dir, list_months

def copy_modulefiles_to_months(cfg: dict):
    pid = cfg["project_id"]
    mdir = model_dir(cfg)

    source_dir = mdir / "bin"
    if not source_dir.is_dir():
        print(f"ERROR: Source directory not found: {source_dir}")
        return

    lua_files = list(source_dir.glob("*.lua"))
    if not lua_files:
        print("No .lua files found in bin/. Skipping copy_modulefiles.")
        return

    print("
--- copy_modulefiles ---")
    for ym in list_months(cfg):
        dest_dir = mdir / f"I{pid}" / f"I{pid}_{ym}" / "modulefiles"
        dest_dir.mkdir(parents=True, exist_ok=True)

        print(f"  Copying {len(lua_files)} .lua files to {dest_dir}")
        for file_path in lua_files:
            shutil.copy(file_path, dest_dir)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Copy .lua modulefiles to all monthly input directories.")
    parser.add_argument("--config", required=True, help="Path to the config directory.")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    copy_modulefiles_to_months(config)