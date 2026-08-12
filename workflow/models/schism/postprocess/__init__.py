"""
models/schism/postprocess
=========================
Phase 5 — SCHISM post-processing.

Implemented steps
-----------------
  plot_outputs   Full-run animated GIFs of SCHISM field outputs (any variable
                 from the New I/O files: elevation, temperature, salinity,
                 velocity, ...) at a user-chosen layer and every-X-hours
                 cadence. Two-stage SLURM (parallel frames -> serial GIF).
  download_sst   DTN download + domain subset of LEO L3S-DY satellite SST
                 into M{ID}/obs/sst_leo/ (one file per day).
  compare_sst    Model (daily-mean SST) vs. satellite two-panel GIF.
  diag_run_plots Per-output-stack diagnostic frames written DURING the run.
                 NOT dispatched here — it is baked into auto_hotstart.py by
                 setup_run and fires from the Phase-4 monitoring loop. Its
                 flag/config still live with Phase 5.

Placeholder steps (not yet implemented)
----------------------------------------
  station_extract  Extract modeled time series at CO-OPS / NDBC stations.
  skill_metrics    RMSE, bias, correlation, skill scores vs observations.

Config for all of the above lives in postprocess.yaml (loaded by
core.config.load_config).
"""

from pathlib import Path


def postprocess_phase(cfg: dict, config_dir, only: str = None):
    """Dispatch Phase 5 post-processing steps."""

    config_dir = Path(config_dir)

    def enabled(step: str) -> bool:
        if only is not None:
            return step == only
        return bool(cfg.get(step, False))

    # --- download_sst (DTN) ---
    if enabled("download_sst"):
        print("[STEP] download_sst")
        from workflow.models.schism.postprocess.downloaders.sst_leo import (
            run_download_sst,
        )
        run_download_sst(cfg)
    else:
        print("[SKIP] download_sst")

    # --- plot_outputs (full-run field GIFs) ---
    if enabled("plot_outputs"):
        print("[STEP] plot_outputs")
        from workflow.models.schism.postprocess.submit_plot_outputs import (
            submit_plot_outputs,
        )
        submit_plot_outputs(cfg, config_dir)
    else:
        print("[SKIP] plot_outputs")

    # --- compare_sst (model vs satellite) ---
    if enabled("compare_sst"):
        print("[STEP] compare_sst")
        from workflow.models.schism.postprocess.submit_compare_sst import (
            submit_compare_sst,
        )
        submit_compare_sst(cfg, config_dir)
    else:
        print("[SKIP] compare_sst")

    # --- diag_run_plots: runs during Phase 4 (auto_hotstart), not here ---
    if enabled("diag_run_plots"):
        print("[NOTE] diag_run_plots runs DURING the run (Phase 4), dispatched")
        print("       by auto_hotstart.py — not in the postprocess phase.")
        print("       Enable it before setup_run so it is baked into the run dirs.")

    # --- placeholder steps ---
    for step in ("station_extract", "skill_metrics"):
        if enabled(step):
            print(f"[STEP] {step}")
            print(f"  NOTE: '{step}' is not yet implemented.")
        else:
            print(f"[SKIP] {step}")
