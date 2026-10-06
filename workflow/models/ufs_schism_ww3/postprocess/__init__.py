"""
models/ufs_schism_ww3/postprocess
==================================
Phase 5 — UFS-SCHISM+WW3 post-processing.
"""

import os
import socket
from pathlib import Path

from workflow.models.schism.postprocess import (
    postprocess_phase as _schism_postprocess_phase,
)

from workflow.models.ufs_schism_ww3.postprocess.collocate_altimetry import (
    run_collocate_altimetry,
)


def _is_dtn() -> bool:
    hostname = socket.gethostname().lower()
    return ("dtn" in hostname
            or os.environ.get("ALLOW_NON_DTN") == "1")


def _dtn_skip(step: str):
    print(
        f"[N/A]  {step}  "
        f"(DTN required — run separately on the DTN with:\n"
        f"         stofs-ak --run --phase postprocess "
        f"--only {step} --config <cfg>)"
    )


def postprocess_phase(cfg: dict, config_dir,
                      only: str = None):
    config_dir = Path(config_dir)
    on_dtn     = _is_dtn()

    def enabled(step: str) -> bool:
        if only is not None:
            return step == only
        return bool(cfg.get(step, False))

    # Run all inherited SCHISM Phase 5 steps
    _schism_postprocess_phase(cfg, config_dir, only=only)

    # ----------------------------------------------------------------
    # plot_outputs_ww3 — WW3 field GIFs from *.out_grd.ww3.nc
    # ----------------------------------------------------------------
    if enabled("plot_outputs_ww3"):
        print("[STEP] plot_outputs_ww3")
        from workflow.models.ufs_schism_ww3.postprocess.submit_plot_outputs_ww3 import (
            submit_plot_outputs_ww3,
        )
        submit_plot_outputs_ww3(cfg, config_dir)
    else:
        print("[SKIP] plot_outputs_ww3")

    # ----------------------------------------------------------------
    # wave_skill
    # ----------------------------------------------------------------
    if enabled("wave_skill"):
        print("[STEP] wave_skill")
        from workflow.models.ufs_schism_ww3.postprocess.wave_skill import (
            run_wave_skill,
        )
        run_wave_skill(cfg, config_dir)
    else:
        print("[SKIP] wave_skill")

    # ----------------------------------------------------------------
    # download_altimetry (DTN)
    # ----------------------------------------------------------------
    if enabled("download_altimetry"):
        if not on_dtn:
            _dtn_skip("download_altimetry")
        else:
            print("[STEP] download_altimetry")
            from workflow.models.schism_wwm.postprocess.downloaders.altimetry import (
                run_download_altimetry,
            )
            run_download_altimetry(cfg)
    else:
        print("[SKIP] download_altimetry")

    # ----------------------------------------------------------------
    # collocate_altimetry
    # ----------------------------------------------------------------
    if enabled("collocate_altimetry"):
        print("[STEP] collocate_altimetry")
        run_collocate_altimetry(cfg, config_dir)
    else:
        print("[SKIP] collocate_altimetry")

    # ----------------------------------------------------------------
    # plot_altimetry
    # ----------------------------------------------------------------
    if enabled("plot_altimetry"):
        from workflow.core.config import model_dir as _model_dir
        _pid      = cfg["project_id"]
        _done_col = (
            _model_dir(cfg) / f"P{_pid}"
            / f"P{_pid}_collocate_altimetry"
            / "collocate_altimetry.done"
        )
        if _done_col.exists():
            print("[STEP] plot_altimetry")
            from workflow.models.schism_wwm.postprocess.altimetry_plots import (
                run_plot_altimetry,
            )
            run_plot_altimetry(cfg, config_dir)
        else:
            print(
                "[NOTE] plot_altimetry: "
                "collocate_altimetry.done not found. "
                "Run collocate_altimetry first.")
    else:
        print("[SKIP] plot_altimetry")
