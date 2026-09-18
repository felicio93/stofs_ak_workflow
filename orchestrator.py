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
import time
from pathlib import Path
from datetime import date

from dateutil.relativedelta import relativedelta

from workflow.core.config import (
    load_config,
    KNOWN_MODEL_TYPES,
    KNOWN_GROUPINGS,
    list_groups,
    model_dir,
)
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

    # grouping is already normalised by load_config; just check the result
    grouping = cfg.get("grouping")
    if grouping not in ("monthly", "ndays"):
        print(f"ERROR: grouping resolved to unexpected value '{grouping}'. "
              f"Valid project.yaml options: {', '.join(sorted(KNOWN_GROUPINGS))}")
        sys.exit(1)

    if grouping == "ndays":
        n = cfg.get("group_ndays")
        if not isinstance(n, int) or n < 1:
            print(f"ERROR: group_ndays must be a positive integer, got: {n!r}")
            sys.exit(1)

    try:
        start = date.fromisoformat(cfg["start_date"])
        end   = date.fromisoformat(cfg["end_date"])
    except (KeyError, ValueError) as exc:
        print(f"ERROR: invalid start_date/end_date in project.yaml: {exc}")
        sys.exit(1)

    if start > end:
        print(f"ERROR: start_date ({start}) must be before or equal to "
              f"end_date ({end})")
        sys.exit(1)

    lon_ref = str(cfg.get("lon_reference", ""))
    if lon_ref not in ("180", "360"):
        print(f"ERROR: lon_reference must be '180' or '360', got: {lon_ref!r}")
        sys.exit(1)

    model_type = str(cfg.get("model_type", "schism")).lower()
    if model_type not in KNOWN_MODEL_TYPES:
        print(f"ERROR: model_type must be one of {KNOWN_MODEL_TYPES}, "
              f"got: {model_type!r}")
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
        print("ERROR: wrapped longitude domains are not supported yet; "
              "lon_min must be less than lon_max")
        sys.exit(1)


# Phase 4 steps — if enabled under --phase preprocess, warn the user.
RUN_PHASE_STEPS   = {"setup_run", "submit_run"}
POSTPROCESS_STEPS = {
    "plot_outputs", "station_skill",
    "download_coops", "download_ndbc", "compare_sst",
    "download_sst", "diag_run_plots",
    "download_argo", "collocate_argo", "plot_argo",
}


def _phase_mismatch_warnings(cfg: dict, phase: str, only: str):
    """Warn when steps belonging to a different phase are enabled."""
    if only is not None:
        return

    if phase == "preprocess":
        run_on  = [s for s in RUN_PHASE_STEPS   if cfg.get(s, False)]
        post_on = [s for s in POSTPROCESS_STEPS if cfg.get(s, False)]
        if run_on or post_on:
            print(f"\n  {'!'*58}")
            print("  WARNING: some steps in your steps.yaml belong to a")
            print("  different phase and will NOT run with the current command.")
            if run_on:
                print(f"    Phase 4 steps enabled but ignored: "
                      f"{', '.join(sorted(run_on))}")
                print("    Run them with:  "
                      "stofs-ak --run --phase run --config <cfg>")
            if post_on:
                print(f"    Phase 5 steps enabled but ignored: "
                      f"{', '.join(sorted(post_on))}")
                print("    Run them with:  "
                      "stofs-ak --run --phase postprocess --config <cfg>")
            print(f"  {'!'*58}\n")

    elif phase == "run":
        pre_on = [s for s in cfg
                  if s not in RUN_PHASE_STEPS | POSTPROCESS_STEPS
                  and s not in ("project_id", "project_dir", "start_date",
                                "end_date", "grouping", "group_ndays",
                                "slurm", "executables", "model_type",
                                "conda_base", "conda_envs",
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


# =============================================================================
# --init: build project directory structure
# =============================================================================

def init_project(cfg: dict):
    """Create the full project directory tree."""
    pid         = cfg["project_id"]
    mdir        = model_dir(cfg)
    groups      = list_groups(cfg)
    grouping    = cfg.get("grouping", "monthly")
    group_ndays = cfg.get("group_ndays", "")

    print(f"\n{'='*60}")
    print(f"  Initializing project M{pid}")
    print(f"  Root:     {mdir}")
    print(f"  Grouping: {grouping}"
          + (f"  (group_ndays={group_ndays})" if grouping == "ndays" else ""))
    print(f"{'='*60}\n")

    # --- Top-level fixed directories ---
    for subdir in ["fix", "bin", "logs"]:
        d = mdir / subdir
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d}")

    # --- Raw data directories ---
    for subdir in ["raw/hycom/ssh", "raw/hycom/ts", "raw/hycom/uv",
                   "raw/era5", "raw/glofas"]:
        d = mdir / subdir
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d}")

    # Pre-create yearly ERA5 raw subdirectories for the date range.
    start_year = date.fromisoformat(cfg["start_date"]).year
    end_year   = date.fromisoformat(cfg["end_date"]).year
    for yr in range(start_year, end_year + 1):
        d = mdir / "raw" / "era5" / str(yr)
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d}")

    # Pre-create yearly GloFAS raw subdirectories for the date range.
    for yr in range(start_year, end_year + 1):
        d = mdir / "raw" / "glofas" / str(yr)
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d}")

    print(f"\n  Generating {len(groups)} group(s) "
          f"({groups[0]} -> {groups[-1]})\n")

    # --- I, R, D directories with per-group subdirectories ---
    for prefix, label in [("I", "Inputs"), ("R", "Run"), ("D", "Debug plots")]:
        parent = mdir / f"{prefix}{pid}"
        parent.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {parent}  ({label})")
        for gid in groups:
            sub = parent / f"{prefix}{pid}_{gid}"
            sub.mkdir(parents=True, exist_ok=True)
            print(f"    {sub.name}/")

    # P{ID} top-level directory only (subdirs created on-demand by postprocess).
    (mdir / f"P{pid}").mkdir(parents=True, exist_ok=True)
    print(f"  Created: {mdir / f'P{pid}'}  (Postprocessing)")

    # --- SLURM log directory for debug plotting jobs ---
    (mdir / f"D{pid}" / "logs").mkdir(parents=True, exist_ok=True)

    # --- Fixed-file diagnostics directory ---
    (mdir / f"D{pid}" / f"D{pid}_fix").mkdir(parents=True, exist_ok=True)

    print(f"\n  Init complete. Next steps:")
    print(f"    1. Copy your mesh and fixed files into:  {mdir}/fix/")
    print(f"    2. Copy compiled Fortran executables into: {mdir}/bin/")
    print(f"    3. Run:  stofs-ak --run --config <config_dir>")
    print()


# =============================================================================
# --run: dispatch a phase (or all phases) to the model driver
# =============================================================================

def run_workflow(cfg: dict, config_dir: Path, phase: str = "preprocess",
                 only: str = None):
    """Build the model driver from cfg['model_type'] and run one or all phases.

    phase is one of:
      preprocess  (default) — data downloads + model input generation
      run                   — populate run dirs + launch monthly runs
      postprocess           — output plots, validation, skill metrics
      all                   — preprocess -> run -> postprocess in sequence

    When phase='all', the orchestrator inserts a wait barrier between the
    preprocess and run phases.
    """
    _phase_mismatch_warnings(cfg, phase, only)

    driver = make_driver(cfg, config_dir)
    phases = (["preprocess", "run", "postprocess"]
              if phase == "all" else [phase])

    _preprocess_slurm_jobs = []

    grouping = cfg.get("grouping", "monthly")
    group_info = (f"grouping={grouping}"
                  + (f", group_ndays={cfg['group_ndays']}"
                     if grouping == "ndays" else ""))

    for ph in phases:
        print(f"\n{'='*60}")
        print(f"  {driver.name} workflow -- "
              f"project M{cfg['project_id']} -- phase: {ph}")
        print(f"  {group_info}")
        if only:
            print(f"  (restricted to step: {only})")
        print(f"{'='*60}\n")

        if ph == "preprocess":
            result = driver.preprocess(only=only)
            if isinstance(result, list):
                _preprocess_slurm_jobs = [j for j in result if j]

        elif ph == "run":
            if phase == "all" and _preprocess_slurm_jobs:
                from workflow.core.slurm import wait_for_slurm_jobs
                wait_for_slurm_jobs(
                    _preprocess_slurm_jobs,
                    poll_seconds=30,
                    label="Phase 3 SLURM jobs "
                          "(gen_hotstart/gen_3Dth/gen_nudge/gen_sflux/...)"
                )
                print("  Waiting 15 s for filesystem metadata propagation ...")
                time.sleep(15)
            driver.run(only=only)

        elif ph == "postprocess":
            driver.postprocess(only=only)

    print(f"\n{'='*60}")
    print("  Workflow complete.")
    print(f"{'='*60}\n")


# =============================================================================
# --refresh: delete all sentinel files so the workflow re-runs from scratch
# =============================================================================

_SENTINELS = {
    # Preprocessing sentinels (in I{pid}/{prefix}_{gid}/)
    "idir": [
        "gen_hotstart.done",
        "gen_3Dth.done",
        "gen_nudge.done",
        "bctides.done",
        "sflux/gen_sflux.done",
        # UFS-SCHISM specific:
        "forcing/gen_datm.done",
        "forcing/gen_esmf_mesh.done",
        "gen_datm_in.done",
        "gen_datm_streams.done",
        "gen_model_configure.done",
        "gen_ufs_configure.done",
        "copy_fd_ufs.done",
        "copy_noahmptable.done",
        "modulefiles/copy_modulefiles.done",
    ],
    # Run sentinels (in R{pid}/{prefix}_{gid}/)
    "rdir": [
        "setup_run.done",
        "run.done",
    ],
    # Diagnostics sentinels (in D{pid}/{prefix}_{gid}/)
    "ddir": [
        "plot_hycom.done",
        "plot_sflux.done",
        "plot_datm.done",
        "plot_outputs.done",
        "compare_sst.done",
    ],
}

_TOP_LEVEL_SENTINELS = [
    "inspect_mesh.done",
]


def reset_sentinels(cfg: dict):
    """Delete every sentinel file in the project so the workflow re-runs
    from scratch on the next invocation.

    Raw downloaded data and generated NetCDF outputs are NOT deleted.
    """
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    groups = list_groups(cfg)

    deleted = []
    missing = []

    def _remove(path: Path):
        if path.exists():
            path.unlink(missing_ok=True)
            deleted.append(path)
        else:
            missing.append(path)

    # --- Per-group sentinels ---
    for gid in groups:
        dirs = {
            "idir": mdir / f"I{pid}" / f"I{pid}_{gid}",
            "rdir": mdir / f"R{pid}" / f"R{pid}_{gid}",
            "ddir": mdir / f"D{pid}" / f"D{pid}_{gid}",
        }
        for key, names in _SENTINELS.items():
            base = dirs[key]
            for name in names:
                _remove(base / name)

    # --- Top-level sentinels ---
    fix_ddir = mdir / f"D{pid}" / f"D{pid}_fix"
    for name in _TOP_LEVEL_SENTINELS:
        _remove(fix_ddir / name)

    # --- Postprocessing sentinels (in P{pid}/ topic subdirs) ---
    pdir = mdir / f"P{pid}"
    for subdir, sentinels in [
        (f"P{pid}_plot_outputs",   ["plot_outputs.done",   ".frames_done"]),
        (f"P{pid}_compare_sst",    ["compare_sst.done",    ".frames_done"]),
        (f"P{pid}_station_skill",  ["station_skill.done"]),
        (f"P{pid}_collocate_argo", ["collocate_argo.done", ".daily_done",
                                    "plot_argo.done"]),
        (f"P{pid}_collocate_altimetry", ["collocate_altimetry.done",
                                         ".daily_done", "plot_altimetry.done"]),
    ]:
        for name in sentinels:
            _remove(pdir / subdir / name)

    # --- Summary ---
    grouping = cfg.get("grouping", "monthly")
    group_info = (f"grouping={grouping}"
                  + (f", group_ndays={cfg['group_ndays']}"
                     if grouping == "ndays" else ""))

    print(f"\n{'='*60}")
    print(f"  --refresh: sentinel reset for M{pid}")
    print(f"  {len(groups)} group(s): {groups[0]} -> {groups[-1]}")
    print(f"  ({group_info})")
    print(f"{'='*60}")
    if deleted:
        print(f"\n  Deleted {len(deleted)} sentinel(s):")
        for p in deleted:
            print(f"    {p.relative_to(mdir)}")
    if missing:
        print(f"\n  Not found (already absent): {len(missing)} sentinel(s).")
    print(f"\n  Raw data and NetCDF outputs were NOT deleted.")
    print(f"  Re-run:  stofs-ak --run --phase all --config <cfg>")
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
        help="Path to the config/ directory containing project.yaml, "
             "domain.yaml, etc."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--init", action="store_true",
        help="Initialize the project directory structure"
    )
    mode.add_argument(
        "--setup-envs", action="store_true", dest="setup_envs",
        help="Create/verify the conda environments "
             "(run on the DTN; needs internet)"
    )
    mode.add_argument(
        "--run", action="store_true",
        help="Run a workflow phase (see --phase)"
    )
    mode.add_argument(
        "--refresh", action="store_true",
        help=(
            "Delete all *.done sentinel files so the workflow re-runs from "
            "scratch on the next --run invocation. Raw data and NetCDF outputs "
            "are NOT deleted — only the resume sentinels."
        )
    )
    parser.add_argument(
        "--phase", default="preprocess",
        choices=("preprocess", "run", "postprocess", "all"),
        help=(
            "Which phase to run with --run (default: preprocess). "
            "preprocess = download + model inputs; "
            "run = populate run dirs + launch monthly runs; "
            "postprocess = output plots + validation; "
            "all = preprocess -> run -> postprocess in sequence"
        )
    )
    parser.add_argument(
        "--only", default=None,
        help="Run only the named step (e.g. download_hycom), "
             "ignoring steps.yaml flags"
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
    elif args.refresh:
        reset_sentinels(cfg)
    elif args.run:
        run_workflow(cfg, config_dir, phase=args.phase, only=args.only)


if __name__ == "__main__":
    main()
