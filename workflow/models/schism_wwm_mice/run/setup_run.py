"""
models/schism_wwm_mice/run/setup_run.py
=======================================
Phase 4, step "setup_run" for SCHISM + WWM + MICE (waves and multi-class
sea ice, both internally coupled to SCHISM).

The SCHISM+MICE run profile (models/schism_mice/run/setup_run.py) with:
  * executables.schism_wwm_mice (USE_WWM + USE_MICE + PREC_EVAP)
  * fix/ links: also wwmbnd.gr3 and hgrid_WWM.gr3 (gen_wwmbnd)
  * group inputs: also wwminput.nml, with the gen_wwminput sentinel

The WWM hotfile (hotfile_out_WWM.nc -> next group's hotfile_in_WWM.nc) is
chained by auto_hotstart.py, as for SCHISM+WWM; the ice restarts through
the SCHISM hotstart, as for SCHISM+MICE.
"""

from workflow.models.schism_mice.run.setup_run import (
    FIX_LINKS as MICE_FIX_LINKS,
    INPUT_LINKS as MICE_INPUT_LINKS,
    profile as mice_profile,
)
from workflow.models.schism.run.setup_run import RunProfile

FIX_LINKS   = list(MICE_FIX_LINKS) + ["wwmbnd.gr3", "hgrid_WWM.gr3"]
INPUT_LINKS = list(MICE_INPUT_LINKS) + ["wwminput.nml"]


def profile(cfg: dict) -> RunProfile:
    p = mice_profile(cfg)
    return p._replace(
        exe_key     = "schism_wwm_mice",
        fix_links   = tuple(FIX_LINKS),
        input_links = tuple(INPUT_LINKS),
        sentinels   = tuple(p.sentinels)
                      + (("gen_wwminput.done", "gen_wwminput"),),
    )
