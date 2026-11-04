"""
models/schism_2d/run/setup_run.py
=================================
Phase 4, step "setup_run" for SCHISM 2D (barotropic, one layer).

Same as the SCHISM setup_run (models/schism/run/setup_run.py) with the 2D
run profile:

  * executables.schism_2d (a build without PREC_EVAP: PREC_EVAP forces the
    evaporation/precipitation model, which aborts without the heat budget)
  * fix/ links: the mesh, vgrid.in (one layer), partition.prop, the friction
    and wind-rotation files and station.in; none of the 3D property files
  * group inputs: bctides.in, param.nml and, when param.nml has
    if_source /= 0, source.nc; no HYCOM boundary or nudging files
  * the first group cold-starts (ihot = 0), so no hotstart.nc and no
    gen_hotstart sentinel
"""

import re

from workflow.core.config import model_dir
from workflow.models.schism.run.setup_run import RunProfile

FIX_LINKS = [
    "hgrid.gr3", "hgrid.ll", "vgrid.in", "partition.prop",
    "manning.gr3", "windrot_geo2proj.gr3", "station.in",
]


def _if_source(cfg: dict) -> int:
    """if_source of fix/param.nml (SCHISM default 0); -1 (source.nc
    required) when the file is missing, so setup_run reports it."""
    pn = model_dir(cfg) / "fix" / "param.nml"
    if not pn.exists():
        return -1
    m = re.search(r"(?mi)^\s*if_source\s*=\s*(-?\d+)", pn.read_text())
    return int(m.group(1)) if m else 0


def profile(cfg: dict) -> RunProfile:
    inputs = ["bctides.in", "param.nml"]
    if _if_source(cfg) != 0:
        inputs.append("source.nc")
    return RunProfile(
        exe_key        = "schism_2d",
        fix_links      = tuple(FIX_LINKS),
        input_links    = tuple(inputs),
        sentinels      = (),
        first_hotstart = False,
    )
