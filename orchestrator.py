"""
orchestrator.py
===============
Main entry point for the STOFS-AK SCHISM preprocessing workflow.

Usage
-----
  # Initialize project directory structure
  python orchestrator.py --init --config /path/to/M01/config

  # Run enabled workflow steps
  python orchestrator.py --run --config /path/to/M01/config

Modes
-----
  --init   Creates the full project directory tree (M{id}/, fix/, raw/,
           bin/, I{id}/, R{id}/, P{id}/ and monthly subdirectories).
           Safe to re-run: existing directories are never deleted.

  --run    Reads steps.yaml and executes each enabled step in order.
           Currently supported steps:
             download_hycom  ->  workflow/download_hycom.py
"""

import argparse
import re
import sys
from pathlib import Path
from datetime import date

# Ensure the repo root is importable regardless of the current working
# directory, so `from workflow.download_hycom import ...` works when the
# orchestrator is invoked by absolute path (e.g. python ~/stofs_ak_workflow/orchestrator.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dateutil.relativedelta import relativedelta

from workflow.config import load_config


# =============================================================================
# Config validation
# =============================================================================

def validate_config(cfg: dict):
    """Validate config values that affect directory layout and execution."""
    pid = str(cfg.get("project_id", ""))
    if not re.fullmatch(r"\d{2}", pid):
        print(f"ERROR: project_id must be a two-digit string, got: {pid!r}")
        sys.exit(1)

    if cfg.get("grouping") != "monthly":
        print("ERROR: only grouping: monthly is currently supported")
        sys.exit(1)

    try:
        start = date.fromisoformat(cfg["start_date"])
        end = date.fromisoformat(cfg["end_date"])
    except (KeyError, ValueError) as exc:
        print(f"ERROR: invalid start_date/end_date in project.yaml: {exc}")
        sys.exit(1)

    if start > end:
        print(f"ERROR: start_date ({start}) must be before or equal to end_date ({end})")
        sys.exit(1)

    lon_ref = str(cfg.get("lon_reference", ""))
    if lon_ref not in ("180", "360"):
        print(f"ERROR: lon_reference must be '180' or '360', got: {lon_ref!r}")
        sys.exit(1)

    for key in ("lon_min", "lon_max", "lat_min", "lat_max"):
        try:
            float(cfg[key])
        except (KeyError, TypeError, ValueError):
            print(f"ERROR: {key} must be defined as a number in domain.yaml")
            sys.exit(1)

    lon_min = float(cfg["lon_min"])
    lon_max = float(cfg["lon_max"])
    if lon_min >= lon_max:
        print("ERROR: wrapped longitude domains are not supported yet; lon_min must be less than lon_max")
        sys.exit(1)


# =============================================================================
# --init: build project directory structure
# =============================================================================

def init_project(cfg: dict):
    """Create the full project directory tree."""

    pid         = cfg["project_id"]
    project_dir = Path(cfg["project_dir"])
    start       = date.fromisoformat(cfg["start_date"])
    end         = date.fromisoformat(cfg["end_date"])

    model_dir = project_dir / f"M{pid}"

    print(f"\n{'='*60}")
    print(f"  Initializing project M{pid}")
    print(f"  Root: {model_dir}")
    print(f"{'='*60}\n")

    # --- Top-level fixed directories ---
    for subdir in ["fix", "bin", "logs"]:
        d = model_dir / subdir
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d}")

    # --- Raw data directories ---
    for subdir in ["raw/hycom/ssh", "raw/hycom/ts", "raw/hycom/uv",
                   "raw/era5"]:
        d = model_dir / subdir
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d}")

    # --- Generate monthly time groups ---
    months = []
    current = date(start.year, start.month, 1)
    while current <= date(end.year, end.month, 1):
        months.append(current.strftime("%Y%m"))
        current += relativedelta(months=1)

    print(f"\n  Generating {len(months)} monthly time groups "
          f"({months[0]} -> {months[-1]})\n")

    # --- I, R, P, D directories with monthly subdirectories ---
    for prefix, label in [("I", "Inputs"), ("R", "Run"),
                          ("P", "Postprocessing"), ("D", "Debug plots")]:
        parent = model_dir / f"{prefix}{pid}"
        parent.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {parent}  ({label})")
        for ym in months:
            sub = parent / f"{prefix}{pid}_{ym}"
            sub.mkdir(parents=True, exist_ok=True)
            print(f"    {sub.name}/")

    # --- SLURM log directory for debug plotting jobs ---
    (model_dir / f"D{pid}" / "logs").mkdir(parents=True, exist_ok=True)

    print(f"\n  Init complete. Next steps:")
    print(f"    1. Copy your mesh and fixed files into:  {model_dir}/fix/")
    print(f"    2. Copy compiled Fortran executables into: {model_dir}/bin/")
    print(f"    3. Run:  python orchestrator.py --run --config <config_dir>")
    print()


# =============================================================================
# --run: execute enabled workflow steps
# =============================================================================

def run_workflow(cfg: dict, config_dir: Path, only: str = None):
    """Execute each enabled step in order. If `only` is set, run just that step."""

    def enabled(step):
        if only is not None:
            return step == only
        return bool(cfg.get(step, False))

    # -------------------------------------------------------------------------
    # Phase-compatibility warning.
    # The steps run in different execution contexts and cannot all succeed in
    # one invocation on a single node:
    #   download_hycom  -> DTN (internet, usually NO sbatch)
    #   aggregate_hycom -> any node (no internet needed)
    #   plotting_debug  -> login node (needs sbatch; DTN usually lacks it)
    # In particular, download_hycom (DTN-only) and plotting_debug (needs sbatch)
    # generally cannot run on the same node. Warn rather than block.
    # -------------------------------------------------------------------------
    if only is None and enabled("download_hycom") and enabled("plotting_debug"):
        print(f"\n  {'!'*58}")
        print("  WARNING: download_hycom and plotting_debug are both enabled.")
        print("  These run in different contexts and usually cannot succeed in")
        print("  one invocation on a single node:")
        print("    - download_hycom needs the DTN (internet, no sbatch)")
        print("    - plotting_debug needs a node with sbatch (login node)")
        print("  Run them separately: download on the DTN, then plotting from a")
        print("  login node. Continuing with the enabled steps in order...")
        print(f"  {'!'*58}")

    print(f"\n{'='*60}")
    print(f"  Running workflow for project M{cfg['project_id']}")
    if only:
        print(f"  (restricted to step: {only})")
    print(f"{'='*60}\n")

    # -------------------------------------------------------------------------
    # Step: download_hycom
    # -------------------------------------------------------------------------
    if enabled("download_hycom"):
        print("[STEP] download_hycom")
        from workflow.download_hycom import run_download
        run_download(cfg)
    else:
        print("[SKIP] download_hycom")

    # -------------------------------------------------------------------------
    # Step: aggregate_hycom  (interactive: ncrcat daily -> monthly SCHISM stacks)
    # -------------------------------------------------------------------------
    if enabled("aggregate_hycom"):
        print("[STEP] aggregate_hycom")
        from workflow.aggregate_hycom import run_aggregate
        run_aggregate(cfg)
    else:
        print("[SKIP] aggregate_hycom")

    # -------------------------------------------------------------------------
    # Step: plotting_debug  (submits a SLURM job array, one task per month)
    # -------------------------------------------------------------------------
    if enabled("plotting_debug"):
        print("[STEP] plotting_debug")
        from workflow.submit_plots import submit_plotting_jobs
        submit_plotting_jobs(cfg, config_dir)
    else:
        print("[SKIP] plotting_debug")

    print(f"\n{'='*60}")
    print("  Workflow complete.")
    print(f"{'='*60}\n")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="STOFS-AK SCHISM preprocessing workflow orchestrator"
    )
    parser.add_argument(
        "--config", required=True,
        help="Path to the config/ directory containing project.yaml, domain.yaml, etc."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--init", action="store_true",
        help="Initialize the project directory structure"
    )
    mode.add_argument(
        "--setup-envs", action="store_true", dest="setup_envs",
        help="Create/verify the conda environments (run on the DTN; needs internet)"
    )
    mode.add_argument(
        "--run", action="store_true",
        help="Run enabled workflow steps"
    )
    parser.add_argument(
        "--only", default=None,
        help="Run only the named step (e.g. download_hycom), ignoring steps.yaml flags"
    )
    args = parser.parse_args()

    config_dir = Path(args.config).resolve()
    if not config_dir.is_dir():
        print(f"ERROR: Config directory does not exist: {config_dir}")
        sys.exit(1)

    cfg = load_config(config_dir)
    validate_config(cfg)

    if args.init:
        init_project(cfg)
    elif args.setup_envs:
        from workflow.setup_envs import setup_envs
        setup_envs(cfg)
    elif args.run:
        run_workflow(cfg, config_dir, only=args.only)


if __name__ == "__main__":
    main()
