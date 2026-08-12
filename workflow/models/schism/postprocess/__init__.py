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
    """Dispatch Phase 5 post-processing steps (placeholder).

    All Phase 5 flags in steps.yaml default to false. When any are enabled
    this function will be expanded to dispatch plot_outputs, station_extract,
    skill_metrics, and compare_sst. Until then it silently skips everything
    so that --phase all and --phase postprocess do not crash.
    """
    postprocess_steps = ("plot_outputs", "station_extract",
                         "skill_metrics", "compare_sst")
    any_enabled = any(cfg.get(s, False) for s in postprocess_steps)
    if any_enabled or only in postprocess_steps:
        print("  NOTE: Phase 5 post-processing is not yet implemented.")
        print("  The following steps are planned but will be skipped:")
        for s in postprocess_steps:
            state = "[ENABLED]" if cfg.get(s, False) else "[disabled]"
            print(f"    {state} {s}")
        print("  See workflow/models/schism/postprocess/__init__.py for the plan.")
    else:
        for s in postprocess_steps:
            print(f"[SKIP] {s}")
