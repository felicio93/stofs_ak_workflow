"""
models/ufs_schism_ww3/driver.py
================================
UfsSchismWw3Driver — UFS-SCHISM+WW3 externally coupled model.

Inherits all UFS-SCHISM preprocessing, run, and postprocessing steps
from UfsSchismDriver and adds WW3-specific preprocessing steps:

  gen_ww3_shel  : generate ww3_shel.nml per group (interactive)
  gen_ww3_ounp  : generate ww3_ounp.inp per group (interactive)

Uses executables.ufs_schism_ww3 (fv3_datm2sch2ww3) instead of
executables.ufs_schism.

Postprocessing inherits all SCHISM Phase 5 steps and adds:
  wave_skill          : WW3 station output vs NDBC wave observations
  download_altimetry  : satellite altimetry download (DTN)
  collocate_altimetry : SCHISM+WW3 Hs vs satellite altimetry
  plot_altimetry      : altimetry diagnostic plots

Same pattern as SchismWwmDriver inheriting SchismDriver.

Works for all grouping modes (monthly, ndays/weekly/daily).
"""

import time

from workflow.models.ufs_schism.driver import UfsSchismDriver


class UfsSchismWw3Driver(UfsSchismDriver):
    name = "UFS_SCHISM+WW3"

    # -------------------------------------------------------------------------
    # Phase 0-3 — Pre-processing
    # Inherits all UFS-SCHISM steps and adds gen_ww3_shel + gen_ww3_ounp.
    # The UFS barrier (wait for gen_datm + gen_esmf_mesh before interactive
    # config steps) is inherited unchanged from UfsSchismDriver.
    # -------------------------------------------------------------------------
    def preprocess(self, only: str = None):
        from workflow.core.config import list_groups
        from workflow.core.slurm import wait_for_slurm_jobs

        # Run all inherited UFS-SCHISM preprocessing steps.
        # This includes Phase 0 (inspect_mesh), Phase 1 (downloads),
        # Phase 2 (aggregate_hycom, gen_sflux, gen_datm, gen_esmf_mesh,
        # UFS config files), and Phase 3 (SCHISM preprocessing).
        # The UFS barrier between SLURM jobs and interactive steps is
        # handled inside UfsSchismDriver.preprocess().
        _slurm_jobs = super().preprocess(only=only)

        # ---- WW3-specific preprocessing steps ----
        # These run AFTER all UFS-SCHISM steps because they read
        # param.nml (for nhot_write/dt) which must already be in
        # I{ID}_{group_id}/ from gen_param.

        if self.enabled("gen_ww3_shel", only):
            print("[STEP] gen_ww3_shel")
            from workflow.models.ufs_schism_ww3.preprocess.gen_ww3_shel import (
                run_gen_ww3_shel,
            )
            run_gen_ww3_shel(self.cfg)
        else:
            print("[SKIP] gen_ww3_shel")

        if self.enabled("gen_ww3_ounp", only):
            print("[STEP] gen_ww3_ounp")
            from workflow.models.ufs_schism_ww3.preprocess.gen_ww3_ounp import (
                run_gen_ww3_ounp,
            )
            run_gen_ww3_ounp(self.cfg)
        else:
            print("[SKIP] gen_ww3_ounp")

        return _slurm_jobs

    # -------------------------------------------------------------------------
    # Phase 4 — Run management
    # Uses UFS-SCHISM+WW3 setup_run (WW3 fixed files + ww3_shel.nml/ounp.inp)
    # and the inherited submit_run (auto_hotstart.py is model-agnostic).
    # -------------------------------------------------------------------------
    def run(self, only: str = None):
        if self.enabled("setup_run", only):
            print("[STEP] setup_run")
            from workflow.models.ufs_schism_ww3.run.setup_run import (
                run_setup_run,
            )
            run_setup_run(self.cfg, self.config_dir)
        else:
            print("[SKIP] setup_run")

        if self.enabled("submit_run", only):
            print("[STEP] submit_run")
            from workflow.models.ufs_schism.run.submit_run import (
                run_submit_run,
            )
            run_submit_run(self.cfg)
        else:
            print("[SKIP] submit_run")

    # -------------------------------------------------------------------------
    # Phase 5 — Post-processing
    # Uses UFS-SCHISM+WW3 postprocess dispatcher which inherits all SCHISM
    # Phase 5 steps and adds wave_skill + altimetry collocation.
    # -------------------------------------------------------------------------
    def postprocess(self, only: str = None):
        from workflow.models.ufs_schism_ww3.postprocess import (
            postprocess_phase,
        )
        postprocess_phase(
            self.cfg, self.config_dir, only=only)
