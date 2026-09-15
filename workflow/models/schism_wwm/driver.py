"""
models/schism_wwm/driver.py
===========================
SchismWwmDriver — SCHISM + WWM internally coupled model.
"""

from workflow.models.schism.driver import SchismDriver


class SchismWwmDriver(SchismDriver):
    name = "SCHISM+WWM"

    def preprocess(self, only: str = None):
        # Run all inherited SCHISM preprocessing steps first.
        slurm_jobs = super().preprocess(only=only)

        # --- WWM-specific steps ---

        # gen_wwmbnd: one-time, generates fix/wwmbnd.gr3 from fix/hgrid.gr3
        if self.enabled('gen_wwmbnd', only):
            print("[STEP] gen_wwmbnd")
            from workflow.models.schism_wwm.preprocess.gen_wwmbnd import (
                run_gen_wwmbnd,
            )
            run_gen_wwmbnd(self.cfg)
        else:
            print("[SKIP] gen_wwmbnd")

        # gen_wwminput: per-month, generates wwminput.nml from template
        if self.enabled('gen_wwminput', only):
            print("[STEP] gen_wwminput")
            from workflow.models.schism_wwm.preprocess.gen_wwminput import (
                run_gen_wwminput,
            )
            run_gen_wwminput(self.cfg)
        else:
            print("[SKIP] gen_wwminput")

        return slurm_jobs
