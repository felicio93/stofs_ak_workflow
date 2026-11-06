"""
models/schism_2d/driver.py
==========================
Schism2dDriver — standalone SCHISM in 2D barotropic mode.

SCHISM has no separate 2D code: a vgrid.in with one layer (SZ, nvrt = 2)
makes the model depth-averaged, and param.nml ibc = 1, ibtp = 0 makes it
barotropic (no T/S transport; SCHISM sets T = 10, S = 0 internally on a cold
start). The run starts from rest (ihot = 0) with the tides ramped in, forced
by tides at the open boundaries (bctides.in), sflux_air (nws = 2) and,
optionally, river volume sources (source.nc).

Inherits the SCHISM steps and adds:
  - gen_constant_gr3 : constant property files in fix/ (manning.gr3,
                       windrot_geo2proj.gr3) from modelsetup.yaml
                       (--phase setup)

Steps that need HYCOM or a 3D baroclinic model are reported as [N/A]:
  no hotstart, boundary T/S/velocity, nudging or estuary files are made,
  and the SST/Argo comparisons do not apply (no temperature is computed).
"""

from typing import Optional

from workflow.models.base import ModelDriver
from workflow.models.schism.driver import SchismDriver, _na

_NO_HYCOM = "2D barotropic: tides-only boundary, cold start, no HYCOM inputs"
_NO_TS    = "2D barotropic: no temperature/salinity computed"


class Schism2dDriver(SchismDriver):
    name = "SCHISM 2D"
    setup_steps = SchismDriver.setup_steps | {"gen_constant_gr3"}
    na_steps = {
        "gen_vgrid":          "2D uses the one-layer vgrid.in of the "
                              "template (ivcor=2)",
        "replicate_fix":      "copies 3D property files; 2D uses "
                              "gen_constant_gr3",
        "download_hycom":     _NO_HYCOM,
        "aggregate_hycom":    _NO_HYCOM,
        "plot_hycom":         _NO_HYCOM,
        "gen_estuary":        _NO_HYCOM,
        "gen_hotstart":       _NO_HYCOM,
        "gen_3Dth":           _NO_HYCOM,
        "gen_nudge":          _NO_HYCOM,
        "plot_hycom_outputs": _NO_HYCOM,
    }
    na_post_steps = {
        "download_sst":       _NO_TS,
        "compare_sst":        _NO_TS,
        "download_argo":      _NO_TS,
        "collocate_argo":     _NO_TS,
        "plot_argo":          _NO_TS,
    }

    def run(self, only: Optional[str] = None):
        from workflow.models.schism.run.run_manager import run_phase
        from workflow.models.schism_2d.run.setup_run import profile
        run_phase(self.cfg, self.config_dir, only=only,
                  profile=profile(self.cfg))

    def postprocess(self, only: Optional[str] = None):
        cfg = dict(self.cfg)
        for step, reason in self.na_post_steps.items():
            if ModelDriver.enabled(self, step, only):
                _na(step, reason)
            cfg[step] = False
        if only in self.na_post_steps:
            return
        from workflow.models.schism.postprocess import postprocess_phase
        postprocess_phase(cfg, self.config_dir, only=only)
