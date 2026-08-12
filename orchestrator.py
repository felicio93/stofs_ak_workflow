"""
orchestrator.py
===============
Main entry point / CLI for the STOFS-AK modeling workflow.

Selects a model driver from project.yaml `model_type` and dispatches one or
more phases (preprocess / run / postprocess / all). Model-specific step logic
lives in the driver (workflow/models/<model>/driver.py), not here.

Usage
-----
  # Initialize project directory structure
  stofs-ak --init --config /path/to/M01/config

  # Create/verify conda environments (run on the DTN)
  stofs-ak --setup-envs --config /path/to/M01/config

  # Run enabled preprocessing steps (default phase)
  stofs-ak --run --config /path/to/M01/config

  # Run a single step regardless of steps.yaml flags
  stofs-ak --run --only download_hycom --config /path/to/M01/config

  # Run Phase 4 (populate run dirs)
  stofs-ak --run --phase run --config /path/to/M01/config

  # Run ALL phases in sequence (preprocess -> run -> postprocess)
  stofs-ak --run --phase all --config /path/to/M01/config

If installed via `pip install -e .`, the `stofs-ak` console script is
available; otherwise invoke as `python orchestrator.py ...`.
"""

import argparse
import re
import sys
from pathlib import Path
from datetime import date

from dateutil.relativedelta import relativedelta

from workflow.core.config import load_config, KNOWN_MODEL_TYPES
from workflow.models.base import make_driver


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

    model_type = str(cfg.get("model_type", "schism")).lower()
    if model_type not in KNOWN_MODEL_TYPES:
        print(f"ERROR: model_type must be one of {KNOWN_MODEL_TYPES}, got: {model_type!r}")
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


# Phase 4 steps in steps.yaml — if any are enabled but --phase preprocess
# is active (the default), warn the user so they know to pass --phase run.
RUN_PHASE_STEPS      = {"setup_run", "submit_run"}
POSTPROCESS_STEPS    = {"plot_outputs", "station_extract",
                        "skill_metrics", "compare_sst"}


def _phase_mismatch_warnings(cfg: dict, phase: str, only: str):
    """Warn when steps belonging to a different phase are enabled."""
    if only is not None:
        return  # --only is explicit; no ambiguity

    if phase == "preprocess":
        run_on  = [s for s in RUN_PHASE_STEPS   if cfg.get(s, False)]
        post_on = [s for s in POSTPROCESS_STEPS if cfg.get(s, False)]
        if run_on or post_on:
            print(f"\n  {'!'*58}")
            print("  WARNING: some steps in your steps.yaml belong to a")
            print("  different phase and will NOT run with the current command.")
            if run_on:
                print(f"    Phase 4 steps enabled but ignored: {', '.join(sorted(run_on))}")
                print("    Run them with:  stofs-ak --run --phase run --config <cfg>")
            if post_on:
                print(f"    Phase 5 steps enabled but ignored: {', '.join(sorted(post_on))}")
                print("    Run them with:  stofs-ak --run --phase postprocess --config <cfg>")
            print(f"  {'!'*58}\n")

    elif phase == "run":
        pre_on = [s for s in cfg
                  if s not in RUN_PHASE_STEPS | POSTPROCESS_STEPS
                  and s not in ("project_id", "project_dir", "start_date",
                                "end_date", "grouping", "slurm", "executables",
                                "model_type", "conda_base", "conda_envs",
                                "lon_min", "lon_max", "lat_min", "lat_max",
                                "lon_reference", "estuary_depth_threshold",
                                "chain_hotstart")
                  and cfg.get(s) is True]
        if pre_on:
            print(f"\n  {'!'*58}")
            print("  NOTE: some preprocessing steps are enabled in steps.yaml")
            print("  but will not run under --phase run:")
            print(f"    {', '.join(sorted(pre_on))}")
            print("  Run them with:  stofs-ak --run --config <cfg>")
            print(f"  {'!'*58}\n")

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
                   "raw/era5", "raw/glofas"]:
        d = model_dir / subdir
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d}")

    # Pre-create yearly ERA5 raw subdirectories for the date range
    start_year = date.fromisoformat(cfg["start_date"]).year
    end_year   = date.fromisoformat(cfg["end_date"]).year
    for yr in range(start_year, end_year + 1):
        d = model_dir / "raw" / "era5" / str(yr)
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d}")

    # Pre-create yearly GloFAS raw subdirectories for the date range
    for yr in range(start_year, end_year + 1):
        d = model_dir / "raw" / "glofas" / str(yr)
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

    # --- Fixed-file diagnostics directory ---
    (model_dir / f"D{pid}" / f"D{pid}_fix").mkdir(parents=True, exist_ok=True)

    print(f"\n  Init complete. Next steps:")
    print(f"    1. Copy your mesh and fixed files into:  {model_dir}/fix/")
    print(f"    2. Copy compiled Fortran executables into: {model_dir}/bin/")
    print(f"    3. Run:  stofs-ak --run --config <config_dir>")
    print()


# =============================================================================
# --init: build project directory structure
# =============================================================================

# =============================================================================
# --run: dispatch a phase (or all phases) to the model driver
# =============================================================================

def run_workflow(cfg: dict, config_dir: Path, phase: str = "preprocess",
                 only: str = None):
    """Build the model driver from cfg['model_type'] and run one or all phases.

    phase is one of:
      preprocess  (default) — data downloads + SCHISM input generation
      run                   — populate run dirs + launch monthly runs
      postprocess           — output plots, validation, skill metrics
      all                   — preprocess -> run -> postprocess in sequence
    """
    _phase_mismatch_warnings(cfg, phase, only)

    driver = make_driver(cfg, config_dir)
    phases = ["preprocess", "run", "postprocess"] if phase == "all" else [phase]

    for ph in phases:
        print(f"\n{'='*60}")
        print(f"  {driver.name} workflow -- project M{cfg['project_id']} -- phase: {ph}")
        if only:
            print(f"  (restricted to step: {only})")
        print(f"{'='*60}\n")

        if ph == "preprocess":
            driver.preprocess(only=only)
        elif ph == "run":
            driver.run(only=only)
        elif ph == "postprocess":
            driver.postprocess(only=only)

    print(f"\n{'='*60}")
    print("  Workflow complete.")
    print(f"{'='*60}\n")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="STOFS-AK modeling workflow orchestrator "
                    "(preprocess / run / postprocess; model selected via "
                    "project.yaml model_type)"
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
        help="Run a workflow phase (see --phase)"
    )
    parser.add_argument(
        "--phase", default="preprocess",
        choices=("preprocess", "run", "postprocess", "all"),
        help=(
            "Which phase to run with --run (default: preprocess). "
            "preprocess = download + SCHISM inputs; "
            "run = populate run dirs + launch monthly runs; "
            "postprocess = output plots + validation (placeholder); "
            "all = preprocess -> run -> postprocess in sequence"
        )
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
        from workflow.core.environment import setup_envs
        setup_envs(cfg)
    elif args.run:
        run_workflow(cfg, config_dir, phase=args.phase, only=args.only)


if __name__ == "__main__":
    main()
