"""
models/schism/postprocess
=========================
Phase 5 — SCHISM post-processing.

DTN-only steps skip gracefully when not on a DTN node.
ocstrack is now in swf_main so download_argo and collocate_argo
no longer need an ImportError guard — they work from the same
swf_main session as all other steps.
"""

import os
import socket
from pathlib import Path


def _is_dtn() -> bool:
    """Return True if the current host is a DTN or ALLOW_NON_DTN=1."""
    hostname = socket.gethostname().lower()
    return ("dtn" in hostname
            or os.environ.get("ALLOW_NON_DTN") == "1")


def _dtn_skip(step: str):
    """Print a [N/A] message for a DTN-only step on a non-DTN node."""
    print(
        f"[N/A]  {step}  "
        f"(DTN required — run separately on the DTN with:\n"
        f"         stofs-ak --run --phase postprocess "
        f"--only {step} --config <cfg>)"
    )


def postprocess_phase(cfg: dict, config_dir,
                      only: str = None):
    """Dispatch Phase 5 post-processing steps.

    DTN-only steps skip gracefully when not on a DTN.
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
    # ocstrack is in swf_main so this works from the standard
    # DTN session without switching environments.
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
    # plot_outputs
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
    # compare_sst
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
    # station_skill
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
    # collocate_argo
    # ocstrack is in swf_main so this works without env switching.
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
    # auto-chained as Stage 3 and will run automatically.
    # When collocate_argo ran in serial mode or collocate_argo.done
    # already exists, this step runs interactively.
    # ----------------------------------------------------------------
    if enabled("plot_argo"):
        from workflow.core.config import (
            model_dir as _model_dir,
        )
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
            print("[NOTE] plot_argo: SLURM Stage 3 job "
                  "queued (afterok on merge job) — "
                  "will run automatically.")
    else:
        print("[SKIP] plot_argo")

    # ----------------------------------------------------------------
    # diag_run_plots (runs during Phase 4, not here)
    # ----------------------------------------------------------------
    if enabled("diag_run_plots"):
        print("[NOTE] diag_run_plots runs DURING the run "
              "(Phase 4), dispatched")
        print("       by auto_hotstart.py — not in the "
              "postprocess phase.")
        print("       Enable it before setup_run so it is "
              "baked into the run dirs.")
