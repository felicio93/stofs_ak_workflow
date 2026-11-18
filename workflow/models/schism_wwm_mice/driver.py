"""
models/schism_wwm_mice/driver.py
================================
SchismWwmMiceDriver — SCHISM with both internal modules: WWM (waves) and
MICE (multi-class sea ice, Icepack), in one run.

WWM and MICE exchange nothing directly: each is coupled to SCHISM as in
schism_wwm and schism_mice, and they affect each other only through the
shared ocean (WWM wave forces and drag, MICE stress, heat and fresh water).
WWM does not see the ice (waves grow and propagate under it).

Steps = the SCHISM+MICE steps (download_hycom_ice, gen_mice_nml,
gen_ice_hotstart; download_seaice, compare_seaice) + the SCHISM+WWM steps
(gen_wwmbnd in --phase setup, gen_wwminput; wave_skill and the altimetry
steps in postprocessing).
"""

from typing import Optional

from workflow.models.schism_mice.driver import SchismMiceDriver


class SchismWwmMiceDriver(SchismMiceDriver):
    name = "SCHISM+WWM+MICE"
    supports_wwm_steps = True
    setup_steps = SchismMiceDriver.setup_steps | {"gen_wwmbnd"}

    def preprocess(self, only: Optional[str] = None):
        slurm_jobs = super().preprocess(only=only)
        if self.enabled("gen_wwminput", only):
            print("[STEP] gen_wwminput")
            from workflow.models.schism_wwm.preprocess.gen_wwminput import (
                run_gen_wwminput,
            )
            run_gen_wwminput(self.cfg)
        else:
            print("[SKIP] gen_wwminput")
        return slurm_jobs

    def run(self, only: Optional[str] = None):
        from workflow.models.schism.run.run_manager import run_phase
        from workflow.models.schism_wwm_mice.run.setup_run import profile
        run_phase(self.cfg, self.config_dir, only=only,
                  profile=profile(self.cfg))

    def postprocess(self, only: Optional[str] = None):
        from workflow.models.schism_mice.postprocess.seaice_steps import (
            ICE_STEPS, run_seaice_steps,
        )
        if only not in ICE_STEPS:
            # SCHISM steps + wave_skill and altimetry (SCHISM+WWM dispatcher)
            from workflow.models.schism_wwm.postprocess import (
                postprocess_phase,
            )
            postprocess_phase(self.cfg, self.config_dir, only=only)
        run_seaice_steps(self.cfg, self.config_dir, only=only)
