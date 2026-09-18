"""
models/schism_wwm/postprocess
==============================
Phase 5 — SCHISM+WWM post-processing.

Inherits all standard SCHISM postprocessing steps and adds
wave-specific validation (wave_skill, download_altimetry,
collocate_altimetry).
"""

from pathlib import Path
from workflow.models.schism.postprocess import (
    postprocess_phase as _schism_postprocess_phase,
)


def postprocess_phase(cfg: dict, config_dir,
                      only: str = None):
    """Dispatch Phase 5 for SCHISM+WWM.

    Runs all standard SCHISM Phase 5 steps first, then adds
    WWM-specific steps.
    """
    config_dir = Path(config_dir)

    # Run all inherited SCHISM Phase 5 steps
    _schism_postprocess_phase(cfg, config_dir, only=only)

    def enabled(step: str) -> bool:
        if only is not None:
            return step == only
        return bool(cfg.get(step, False))

    # ----------------------------------------------------------------
    # wave_skill — WWM station output vs NDBC wave observations
    # ----------------------------------------------------------------
    if enabled("wave_skill"):
        print("[STEP] wave_skill")
        from workflow.models.schism_wwm.postprocess.wave_skill import (
            run_wave_skill,
        )
        run_wave_skill(cfg, config_dir)
    else:
        print("[SKIP] wave_skill")

    # ----------------------------------------------------------------
    # download_altimetry (DTN)
    # ----------------------------------------------------------------
    if enabled("download_altimetry"):
        import os
        import socket
        on_dtn = ("dtn" in socket.gethostname().lower()
                  or os.environ.get("ALLOW_NON_DTN") == "1")
        if not on_dtn:
            print(
                "[N/A]  download_altimetry  "
                "(DTN required — run separately on the DTN with:\n"
                "         stofs-ak --run --phase postprocess "
                "--only download_altimetry --config <cfg>)"
            )
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
        from workflow.models.schism_wwm.postprocess.collocate_altimetry import (
            run_collocate_altimetry,
        )
        run_collocate_altimetry(cfg, config_dir)
    else:
        print("[SKIP] collocate_altimetry")
