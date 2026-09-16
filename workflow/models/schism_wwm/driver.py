"""
models/schism_wwm/driver.py
===========================
SchismWwmDriver — SCHISM + WWM internally coupled model.

Inherits all SCHISM preprocessing and run steps from SchismDriver,
adding WWM-specific steps:
  - gen_wwmbnd  : generate fix/wwmbnd.gr3 (once)
  - gen_wwminput: generate wwminput.nml per group

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).
"""

from workflow.models.schism.driver import SchismDriver


class SchismWwmDriver(SchismDriver):
    name = "SCHISM+WWM"

    # -------------------------------------------------------------------------
    # Phase 0-3 — Pre-processing
    # Inherits all SCHISM steps; adds WWM-specific steps after.
    # -------------------------------------------------------------------------
    def preprocess(self, only: str = None):
        # Run all inherited SCHISM preprocessing steps first
        slurm_jobs = super().preprocess(only=only)

        # --- WWM-specific steps ---
        if self.enabled("gen_wwmbnd", only):
            print("[STEP] gen_wwmbnd")
            from workflow.models.schism_wwm.preprocess.gen_wwmbnd import (
                run_gen_wwmbnd,
            )
            run_gen_wwmbnd(self.cfg)
        else:
            print("[SKIP] gen_wwmbnd")

        if self.enabled("gen_wwminput", only):
            print("[STEP] gen_wwminput")
            from workflow.models.schism_wwm.preprocess.gen_wwminput import (
                run_gen_wwminput,
            )
            run_gen_wwminput(self.cfg)
        else:
            print("[SKIP] gen_wwminput")

        return slurm_jobs

    # -------------------------------------------------------------------------
    # Phase 4 — Run management
    # Uses the WWM-specific setup_run; submit_run is identical to SCHISM.
    # -------------------------------------------------------------------------
    def run(self, only: str = None):
        if self.enabled("setup_run", only):
            print("[STEP] setup_run")
            from workflow.models.schism_wwm.run.setup_run import (
                run_setup_run,
            )
            run_setup_run(self.cfg, self.config_dir)
        else:
            print("[SKIP] setup_run")

        if self.enabled("submit_run", only):
            print("[STEP] submit_run")
            from workflow.models.schism.run.submit_run import (
                run_submit_run,
            )
            run_submit_run(self.cfg)
        else:
            print("[SKIP] submit_run")

    # -------------------------------------------------------------------------
    # Phase 5 — Post-processing
    # Reuses SCHISM postprocessing pipeline unchanged.
    # -------------------------------------------------------------------------
    def postprocess(self, only: str = None):
        from workflow.models.schism.postprocess import (
            postprocess_phase,
        )
        postprocess_phase(
            self.cfg, self.config_dir, only=only)
