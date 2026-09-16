"""
models/schism/postprocess
=========================
Phase 5 — SCHISM post-processing.

Implemented steps
-----------------
  plot_outputs   Full-run animated GIFs of SCHISM field outputs.
  download_sst   DTN download + domain subset of LEO L3S-DY satellite SST.
  download_coops DTN download of NOAA CO-OPS station observations.
  download_ndbc  DTN download of NOAA NDBC buoy observations.
  download_argo  DTN download of Argo float profiles.
  compare_sst    Model (daily-mean SST) vs. satellite two-panel GIF.
  station_skill  Interactive obs-vs-model comparison + skill CSV.
  collocate_argo OCSTrack 3-D collocation of SCHISM T/S vs Argo floats.
  plot_argo      Argo diagnostic plots.
  diag_run_plots Per-output-stack diagnostic frames DURING the run.

Resilience against all-flags-on steps.yaml
-------------------------------------------
DTN-only steps (download_sst, download_coops, download_ndbc,
download_argo) check whether the current node is a DTN before
attempting the download. When running on a login node (e.g. via
--phase all) they print a [N/A] message and skip gracefully instead
of crashing. Run them separately on the DTN after the model completes.
"""

import os
import socket
from pathlib import Path


# =============================================================================
# DTN helpers
# =============================================================================

def _is_dtn() -> bool:
    """Return True if the current host is a DTN or ALLOW_NON_DTN=1."""
    hostname = socket.gethostname().lower()
    return ("dtn" in hostname
            or os.environ.get("ALLOW_NON_DTN") == "1")


def _dtn_skip(step: str):
    """Print a [N/A] message for a DTN-only step running on a
    non-DTN node.
    """
    print(
        f"[N/A]  {step}  "
        f"(DTN required — run separately on the DTN with:\n"
        f"         stofs-ak --run --phase postprocess "
        f"--only {step} --config <cfg>)"
    )


# =============================================================================
# Phase 5 dispatcher
# =============================================================================

def postprocess_phase(cfg: dict, config_dir,
                      only: str = None):
    """Dispatch Phase 5 post-processing steps.

    DTN-only steps skip gracefully when not on a DTN.
    All other steps run normally regardless of node type.
    """
    config_dir = Path(config_dir)
    on_dtn     = _is_dtn()

    def enabled(step: str) -> bool:
        if only is not None:
            return step == only
        return bool(cfg.get(step, False))

    # ----------------------------------------------------------------
    # download_sst (DTN)
    # ----------------------------------------------------------------
    if enabled("download_sst"):
        if not on_dtn:
            _dtn_skip("download_sst")
        else:
            print("[STEP] download_sst")
            from workflow.models.schism.postprocess.downloaders.sst_leo import (
                run_download_sst,
            )
            run_download_sst(cfg)
    else:
        print("[SKIP] download_sst")

    # ----------------------------------------------------------------
    # download_coops (DTN)
    # ----------------------------------------------------------------
    if enabled("download_coops"):
        if not on_dtn:
            _dtn_skip("download_coops")
        else:
            print("[STEP] download_coops")
            from workflow.models.schism.postprocess.downloaders.coops import (
                run_download_coops,
            )
            run_download_coops(cfg)
    else:
        print("[SKIP] download_coops")

    # ----------------------------------------------------------------
    # download_ndbc (DTN)
    # ----------------------------------------------------------------
    if enabled("download_ndbc"):
        if not on_dtn:
            _dtn_skip("download_ndbc")
        else:
            print("[STEP] download_ndbc")
            from workflow.models.schism.postprocess.downloaders.ndbc import (
                run_download_ndbc,
            )
            run_download_ndbc(cfg)
    else:
        print("[SKIP] download_ndbc")

    # ----------------------------------------------------------------
    # download_argo (DTN)
    # ----------------------------------------------------------------
    if enabled("download_argo"):
        if not on_dtn:
            _dtn_skip("download_argo")
        else:
            print("[STEP] download_argo")
            from workflow.models.schism.postprocess.downloaders.argo import (
                run_download_argo,
            )
            run_download_argo(cfg)
    else:
        print("[SKIP] download_argo")

    # ----------------------------------------------------------------
    # plot_outputs (full-run field GIFs)
    # ----------------------------------------------------------------
    if enabled("plot_outputs"):
        print("[STEP] plot_outputs")
        from workflow.models.schism.postprocess.submit_plot_outputs import (
            submit_plot_outputs,
        )
        submit_plot_outputs(cfg, config_dir)
    else:
        print("[SKIP] plot_outputs")

    # ----------------------------------------------------------------
    # compare_sst (model vs satellite)
    # ----------------------------------------------------------------
    if enabled("compare_sst"):
        print("[STEP] compare_sst")
        from workflow.models.schism.postprocess.submit_compare_sst import (
            submit_compare_sst,
        )
        submit_compare_sst(cfg, config_dir)
    else:
        print("[SKIP] compare_sst")

    # ----------------------------------------------------------------
    # station_skill (interactive)
    # ----------------------------------------------------------------
    if enabled("station_skill"):
        print("[STEP] station_skill")
        from workflow.models.schism.postprocess.station_skill import (
            run_station_skill,
        )
        run_station_skill(cfg, config_dir)
    else:
        print("[SKIP] station_skill")

    # ----------------------------------------------------------------
    # collocate_argo (interactive / SLURM)
    # ----------------------------------------------------------------
    if enabled("collocate_argo"):
        print("[STEP] collocate_argo")
        from workflow.models.schism.postprocess.collocate_argo import (
            run_collocate_argo,
        )
        run_collocate_argo(cfg, config_dir)
    else:
        print("[SKIP] collocate_argo")

    # ----------------------------------------------------------------
    # plot_argo
    # When collocate_argo was submitted via SLURM, plot_argo is
    # auto-chained as Stage 3 (afterok on the merge job) and will
    # run automatically — no action needed here in that case.
    # When collocate_argo ran in serial mode or collocate_argo.done
    # already exists, this step runs interactively.
    # ----------------------------------------------------------------
    if enabled("plot_argo"):
        from workflow.core.config import model_dir as _model_dir
        _pid      = cfg["project_id"]
        _done_col = (
            _model_dir(cfg) / f"P{_pid}"
            / f"P{_pid}_collocate_argo"
            / "collocate_argo.done"
        )
        if _done_col.exists():
            print("[STEP] plot_argo")
            from workflow.models.schism.postprocess.argo_plots import (
                run_plot_argo,
            )
            run_plot_argo(cfg, config_dir)
        else:
            print("[NOTE] plot_argo: SLURM Stage 3 job queued "
                  "(afterok on merge job) — will run "
                  "automatically.")
    else:
        print("[SKIP] plot_argo")

    # ----------------------------------------------------------------
    # diag_run_plots
    # This step runs DURING Phase 4 (dispatched by auto_hotstart.py),
    # not here. Print a reminder if it is enabled.
    # ----------------------------------------------------------------
    if enabled("diag_run_plots"):
        print("[NOTE] diag_run_plots runs DURING the run "
              "(Phase 4), dispatched")
        print("       by auto_hotstart.py — not in the "
              "postprocess phase.")
        print("       Enable it before setup_run so it is "
              "baked into the run dirs.")
