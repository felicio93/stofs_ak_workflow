"""
models/schism_mice/run/setup_run.py
===================================
Phase 4, step "setup_run" for SCHISM + MICE (multi-class sea ice).

Same as the SCHISM setup_run (models/schism/run/setup_run.py) with the MICE
run profile:

  * executables.schism_mice (a build with USE_MICE and PREC_EVAP)
  * fix/ links: the SCHISM files plus namelist.icepack (Icepack reads it
    from the run directory)
  * group inputs: the SCHISM ones plus the group's mice.nml (gen_mice_nml)
  * first group: when its mice.nml starts the ice from HYCOM
    (ihot_mice = 2), the gen_ice_hotstart sentinel, i.e. hotstart.nc holds
    sic2/sih2/siu2/siv2

The later groups restart the ice from the hotstart.nc that auto_hotstart
chains from the previous group (ihot_mice = 1): combine_hotstart7 merges
the Icepack variables like any other node variable.
"""

from workflow.core.config import list_groups, model_dir
from workflow.models.schism.run.setup_run import (
    FIX_LINKS as SCHISM_FIX_LINKS,
    INPUT_LINKS as SCHISM_INPUT_LINKS,
    SCHISM_PROFILE,
    RunProfile,
)
from workflow.models.schism_mice import mice_nml

FIX_LINKS   = list(SCHISM_FIX_LINKS) + ["namelist.icepack"]
INPUT_LINKS = list(SCHISM_INPUT_LINKS) + ["mice.nml"]


def _first_ihot_mice(cfg: dict):
    """ihot_mice of the first group's mice.nml (fix/mice.nml before
    gen_mice_nml has run)."""
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    first = list_groups(cfg)[0]
    for p in (mdir / f"I{pid}" / f"I{pid}_{first}" / "mice.nml",
              mdir / "fix" / "mice.nml"):
        if p.exists():
            return mice_nml.ihot_mice(p.read_text())
    return None


def profile(cfg: dict) -> RunProfile:
    first = ()
    if _first_ihot_mice(cfg) == 2:
        first = (("gen_ice_hotstart.done", "gen_ice_hotstart"),)
    return SCHISM_PROFILE._replace(
        exe_key         = "schism_mice",
        fix_links       = tuple(FIX_LINKS),
        input_links     = tuple(INPUT_LINKS),
        first_sentinels = first,
    )
