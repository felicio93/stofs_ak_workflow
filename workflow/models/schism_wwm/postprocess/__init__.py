"""
models/schism_wwm/postprocess
==============================
Phase 5 — SCHISM+WWM post-processing.

Inherits all standard SCHISM postprocessing steps and adds
wave-specific validation (wave_skill).
"""

from pathlib import Path
from workflow.models.schism.postprocess import (
    postprocess_phase as _schism_postprocess_phase,
)


def postprocess_phase(cfg: dict, config_dir,
                      only: str = None):
    """Dispatch Phase 5 for SCHISM+WWM.

    Runs all standard SCHISM postprocessing steps first,
    then adds the wave_skill step.
    """
    config_dir = Path(config_dir)

    # Run all inherited SCHISM Phase 5 steps
    _schism_postprocess_phase(cfg, config_dir, only=only)

    # ----------------------------------------------------------------
    # wave_skill — WWM station output vs NDBC wave observations
    # ----------------------------------------------------------------
    def enabled(step: str) -> bool:
        if only is not None:
            return step == only
        return bool(cfg.get(step, False))

    if enabled("wave_skill"):
        print("[STEP] wave_skill")
        from workflow.models.schism_wwm.postprocess.wave_skill import (
            run_wave_skill,
        )
        run_wave_skill(cfg, config_dir)
    else:
        print("[SKIP] wave_skill")
