"""
models/schism/postprocess
=========================
Phase 5 — SCHISM post-processing.

Implemented modules
-------------------
  plot_outputs.py        Animated GIFs of SCHISM field outputs (any variable
                         from the New I/O output files: elevation, temperature,
                         salinity, velocity, etc.) at a user-specified layer
                         and temporal stride. Configured in postprocess.yaml.

Placeholder modules (not yet implemented)
------------------------------------------
  station_validation.py  Extract modeled time series at NOAA CO-OPS tide gauge
                         and NDBC buoy locations; align with observations.
  skill_metrics.py       RMSE, bias, correlation, and skill scores (e.g.
                         Murphy) of model vs observations.
  compare_sst.py         Compare modeled SST against satellite composites
                         (LEO L3S, GOES, VIIRS, OI-SST).

Wire-up: SchismDriver.postprocess() calls postprocess_phase() below.
"""


def postprocess_phase(cfg: dict, config_dir, only: str = None):
    """Dispatch Phase 5 post-processing steps."""

    def enabled(step: str) -> bool:
        if only is not None:
            return step == only
        return bool(cfg.get(step, False))

    # --- Implemented steps ---

    if enabled("plot_outputs"):
        print("[STEP] plot_outputs")
        from workflow.models.schism.postprocess.submit_plot_outputs import (
            submit_plot_outputs,
        )
        from pathlib import Path
        submit_plot_outputs(cfg, Path(config_dir))
    else:
        print("[SKIP] plot_outputs")

    # --- Placeholder steps (not yet implemented) ---

    for step in ("station_extract", "skill_metrics", "compare_sst"):
        if enabled(step):
            print(f"[STEP] {step}")
            print(f"  NOTE: '{step}' is not yet implemented.")
            print(f"  See workflow/models/schism/postprocess/__init__.py for the plan.")
        else:
            print(f"[SKIP] {step}")

