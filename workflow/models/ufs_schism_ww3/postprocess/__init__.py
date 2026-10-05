"""
models/ufs_schism_ww3/postprocess
==================================
Phase 5 — UFS-SCHISM+WW3 post-processing.

Inherits all standard UFS-SCHISM postprocessing steps (which in turn
inherit all SCHISM steps) and adds WW3-specific validation:

  wave_skill          : WW3 station output vs NDBC wave observations
                        reads ww3.{YYYYMM}_tab.nc (converted from
                        out_pnt.ww3 by ww3_ounp via auto_hotstart.py)
  download_altimetry  : satellite altimetry Hs download (DTN)
  collocate_altimetry : WW3 Hs vs satellite altimetry collocation
                        uses *.out_grd.ww3.nc field output files
  plot_altimetry      : altimetry diagnostic plots

Note: wave_skill requires download_ndbc to have run first.
      collocate_altimetry requires download_altimetry first.
"""

import os
import socket
from pathlib import Path

from workflow.models.ufs_schism_ww3.postprocess.collocate_altimetry import (
        run_collocate_altimetry,
        )


def _is_dtn() -> bool:
    """Return True if the current host is a DTN or ALLOW_NON_DTN=1."""
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
    """Dispatch Phase 5 for UFS-SCHISM+WW3.

    Runs all standard SCHISM Phase 5 steps first, then adds
    WW3-specific steps.
    """
    config_dir = Path(config_dir)
    on_dtn     = _is_dtn()

    def enabled(step: str) -> bool:
        if only is not None:
            return step == only
        return bool(cfg.get(step, False))

    # Run all inherited SCHISM Phase 5 steps
    # (download_sst, compare_sst, download_coops, download_ndbc,
    #  station_skill, download_argo, collocate_argo, plot_argo,
    #  plot_outputs)
    _schism_postprocess_phase(cfg, config_dir, only=only)

    # ----------------------------------------------------------------
    # wave_skill — WW3 station output vs NDBC wave observations
    # Uses ww3.{YYYYMM}_tab.nc files written by ww3_ounp during run.
    # Variable mapping: hs->WVHT, tr->APD(TM01), th1m->MWD
    # Requires: download_ndbc first.
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
    # Uses *.out_grd.ww3.nc field output files for Hs collocation.
    # Reuses the SCHISM+WWM collocate_altimetry module since the
    # WW3 field NetCDF format is compatible (unstructured, HS variable).
    # ----------------------------------------------------------------
    if enabled("collocate_altimetry"):
        print("[STEP] collocate_altimetry")
        from workflow.models.schism_wwm.postprocess.collocate_altimetry import (
            run_collocate_altimetry,
        )
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
                "Run collocate_altimetry first, then:\n"
                "         stofs-ak --run "
                "--phase postprocess "
                "--only plot_altimetry "
                "--config <cfg>")
    else:
        print("[SKIP] plot_altimetry")
