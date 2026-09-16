"""
models/schism_wwm/driver.py
===========================
SchismWwmDriver — SCHISM + WWM internally coupled model.
"""

from workflow.models.schism.driver import SchismDriver


class SchismWwmDriver(SchismDriver):
    name = "SCHISM+WWM"

    def preprocess(self, only: str = None):
        slurm_jobs = super().preprocess(only=only)

        if self.enabled('gen_wwmbnd', only):
            print("[STEP] gen_wwmbnd")
            from workflow.models.schism_wwm.preprocess.gen_wwmbnd import (
                run_gen_wwmbnd,
            )
            run_gen_wwmbnd(self.cfg)
        else:
            print("[SKIP] gen_wwmbnd")

        if self.enabled('gen_wwminput', only):
            print("[STEP] gen_wwminput")
            from workflow.models.schism_wwm.preprocess.gen_wwminput import (
                run_gen_wwminput,
            )
            run_gen_wwminput(self.cfg)
        else:
            print("[SKIP] gen_wwminput")

        return slurm_jobs

    def run(self, only: str = None):
        """Phase 4 — run management for SCHISM+WWM."""
        if self.enabled('setup_run', only):
            print("[STEP] setup_run")
            from workflow.models.schism_wwm.run.setup_run import run_setup_run
            run_setup_run(self.cfg, self.config_dir)
        else:
            print("[SKIP] setup_run")

        if self.enabled('submit_run', only):
            print("[STEP] submit_run")
            from workflow.models.schism.run.submit_run import run_submit_run
            run_submit_run(self.cfg)
        else:
            print("[SKIP] submit_run")

    def postprocess(self, only: str = None):
        from workflow.models.schism.postprocess import postprocess_phase
        postprocess_phase(self.cfg, self.config_dir, only=only)
