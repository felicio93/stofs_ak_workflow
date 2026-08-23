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
                 water temperature, air pressure, wind) into M{ID}/obs/coops/
                 (one CSV per station / product / month), based on fix/station.in.
  download_ndbc  DTN download of NOAA NDBC buoy observations (WTMP, wind, PRES,
                 ...) into M{ID}/obs/ndbc/ (one CSV per station / year:
                 historical annual file for past years; monthly + realtime for
                 the current year), based on fix/station.in.
  download_argo  DTN download of Argo float profiles from the IFREMER GDAC
                 (via OCSTrack get_argo), cropped to the domain bounding box,
                 into M{ID}/obs/argo/{region}/processed/.
  compare_sst    Model (daily-mean SST) vs. satellite two-panel GIF.
  station_skill  Interactive obs-vs-model comparison at CO-OPS and NDBC stations:
                 time-series plots (bias/RMSE/R^2 in the legend) + a
                 skill_metrics.csv under P{ID}/P{ID}_station_skill/. Re-runnable
                 for any sub-period via station_skill_start/end in
                 postprocess.yaml.
  collocate_argo Two-stage SLURM: parallel per-day array (Stage 1) + serial
                 merge/clean (Stage 2). Each array task collocates all
                 configured variables for one calendar day, reusing the
                 2.6 M-node KDTree. Daily NetCDFs are merged into
                 collocated_{var}.nc + collocated_{var}_clean.nc under
                 P{ID}/P{ID}_collocate_argo/. Falls back to serial
                 month-by-month loop when sbatch is unavailable or
                 ALLOW_NON_SLURM=1. Needs download_argo first.
  plot_argo      Three Argo diagnostic plots using the collocated NetCDFs from
                 collocate_argo: (1) profile location map coloured by date,
                 (2) per-variable skill histograms (R², Bias, RMSE), and
                 (3) per-variable profile matrix (obs | model | bias ± 1σ | RMSE).
                 Output in P{ID}/P{ID}_collocate_argo/. Needs collocate_argo first.
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

    # --- download_coops (DTN): station observations -> obs/coops/ ---
    if enabled("download_coops"):
        print("[STEP] download_coops")
        from workflow.models.schism.postprocess.downloaders.coops import (
            run_download_coops,
        )
        run_download_coops(cfg)
    else:
        print("[SKIP] download_coops")

    # --- download_ndbc (DTN): NDBC buoy observations -> obs/ndbc/ ---
    if enabled("download_ndbc"):
        print("[STEP] download_ndbc")
        from workflow.models.schism.postprocess.downloaders.ndbc import (
            run_download_ndbc,
        )
        run_download_ndbc(cfg)
    else:
        print("[SKIP] download_ndbc")

    # --- download_argo (DTN): Argo float profiles -> obs/argo/ ---
    if enabled("download_argo"):
        print("[STEP] download_argo")
        from workflow.models.schism.postprocess.downloaders.argo import (
            run_download_argo,
        )
        run_download_argo(cfg)
    else:
        print("[SKIP] download_argo")

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

    # --- collocate_argo (interactive): SCHISM 3-D T/S vs Argo floats ---
    if enabled("collocate_argo"):
        print("[STEP] collocate_argo")
        from workflow.models.schism.postprocess.collocate_argo import (
            run_collocate_argo,
        )
        run_collocate_argo(cfg, config_dir)
    else:
        print("[SKIP] collocate_argo")

    # --- plot_argo: location map + skill histograms + profile matrix ---
    # When collocate_argo was submitted via SLURM (the default), plot_argo
    # is auto-chained as Stage 3 (afterok on the merge job) and will run
    # automatically — no action needed here.
    # When collocate_argo ran in serial mode (ALLOW_NON_SLURM=1) or
    # collocate_argo.done already exists from a previous run, this step
    # runs interactively.
    if enabled("plot_argo"):
        from pathlib import Path as _Path
        from workflow.core.config import model_dir as _model_dir
        _pid      = cfg["project_id"]
        _done_col = (_model_dir(cfg) / f"P{_pid}" /
                     f"P{_pid}_collocate_argo" / "collocate_argo.done")
        if _done_col.exists():
            # Collocation finished (serial mode or previous run) → run now.
            print("[STEP] plot_argo")
            from workflow.models.schism.postprocess.argo_plots import run_plot_argo
            run_plot_argo(cfg, config_dir)
        else:
            # Collocation was submitted as a SLURM job; plot_argo is queued
            # as Stage 3 (afterok on the merge) and will run automatically.
            print("[NOTE] plot_argo: SLURM Stage 3 job queued "
                  "(afterok on merge job) — will run automatically.")
    else:
        print("[SKIP] plot_argo")

    # --- diag_run_plots: runs during Phase 4 (auto_hotstart), not here ---
    if enabled("diag_run_plots"):
        print("[NOTE] diag_run_plots runs DURING the run (Phase 4), dispatched")
        print("       by auto_hotstart.py — not in the postprocess phase.")
        print("       Enable it before setup_run so it is baked into the run dirs.")
