"""
models/schism/postprocess
=========================
PLACEHOLDER — SCHISM post-processing (Phase 5). Not yet implemented.

Planned modules (siblings of this __init__.py)
----------------------------------------------
  plot_outputs.py        Animated maps of water level, currents, SSS/SST from
                         SCHISM output NetCDFs (schout*/out2d/etc.).
  station_validation.py  Extract modeled time series at NOAA CO-OPS tide gauge
                         and NDBC buoy locations; align with observations.
  skill_metrics.py       RMSE, bias, correlation, and skill scores (e.g.
                         Murphy) of model vs observations.
  compare_sst.py         Compare modeled SST against satellite composites
                         (GOES / VIIRS / OI-SST).

Wire-up: SchismDriver.postprocess() calls postprocess_phase() below. Add flags
to steps.yaml under the "Phase 5" block and dispatch them here.
"""


def postprocess_phase(cfg: dict, config_dir, only: str = None):
    raise NotImplementedError(
        "SCHISM post-processing (Phase 5) is not implemented yet. "
        "See workflow/models/schism/postprocess/__init__.py for the plan."
    )
