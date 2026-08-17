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
  download_coops DTN download of NOAA CO-OPS station observations (water level,
                 water temperature, air pressure, wind) into M{ID}/raw/coops/
                 (one CSV per station / product / month), based on fix/station.in.
  download_ndbc  DTN download of NOAA NDBC buoy observations (WTMP, wind, PRES,
                 ...) into M{ID}/raw/ndbc/ (one CSV per station / year:
                 historical annual file for past years; monthly + realtime for
                 the current year), based on fix/station.in.
  compare_sst    Model (daily-mean SST) vs. satellite two-panel GIF.
  station_skill  Interactive obs-vs-model comparison at CO-OPS and NDBC stations:
                 time-series plots (bias/RMSE/R^2 in the legend) + a
                 skill_metrics.csv under P{ID}/P{ID}_station_skill/. Re-runnable
                 for any sub-period via station_skill_start/end in
                 postprocess.yaml.
  diag_run_plots Per-output-stack diagnostic frames written DURING the run.
                 NOT dispatched here — it is baked into auto_hotstart.py by
                 setup_run and fires from the Phase-4 monitoring loop. Its
                 flag/config still live with Phase 5.

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

    # --- download_coops (DTN): station observations -> raw/coops/ ---
    if enabled("download_coops"):
        print("[STEP] download_coops")
        from workflow.models.schism.postprocess.downloaders.coops import (
            run_download_coops,
        )
        run_download_coops(cfg)
    else:
        print("[SKIP] download_coops")

    # --- download_ndbc (DTN): NDBC buoy observations -> raw/ndbc/ ---
    if enabled("download_ndbc"):
        print("[STEP] download_ndbc")
        from workflow.models.schism.postprocess.downloaders.ndbc import (
            run_download_ndbc,
        )
        run_download_ndbc(cfg)
    else:
        print("[SKIP] download_ndbc")

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

    # --- station_skill (interactive): CO-OPS obs vs model staout_* ---
    if enabled("station_skill"):
        print("[STEP] station_skill")
        from workflow.models.schism.postprocess.station_skill import (
            run_station_skill,
        )
        run_station_skill(cfg, config_dir)
    else:
        print("[SKIP] station_skill")

    # --- diag_run_plots: runs during Phase 4 (auto_hotstart), not here ---
    if enabled("diag_run_plots"):
        print("[NOTE] diag_run_plots runs DURING the run (Phase 4), dispatched")
        print("       by auto_hotstart.py — not in the postprocess phase.")
        print("       Enable it before setup_run so it is baked into the run dirs.")
