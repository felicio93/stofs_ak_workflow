"""
models/ufs_schism_cice/driver.py
================================
UfsSchismCiceDriver — UFS-Coastal DATM + CMEPS + SCHISM + CICE6 (app
CSTLSC), externally coupled through the mediator in coastal mode.

Inherits all UFS-SCHISM steps and adds:
  gen_cice_grid : CICE regional grid, land mask, bathymetry and ESMF mesh
                  from the SCHISM mesh into fix/cice/ (--phase setup,
                  SLURM job)
  gen_ice_in    : ice_in per group from fix/ice_in (dates, run type,
                  restart, decomposition, history, boundary restoring)

and runs with executables.ufs_schism_cice: the run directory also gets
cice_grid/ (-> fix/cice), ice_in and history/, and auto_hotstart.py chains
the CICE restart (RESTART/iced.<date>.nc + ice.restart_file) to the next
group.

DATM uses datamode ERA5 (fix/datm_in): CICE needs the lowest-level fields
and the shortwave bands that mode makes (see templates/ufs_schism_cice/fix).

Known limits of the public code this model is built from (Nov 2026):
  - CICE (ufs-coastal pin) takes snowfall only from negative Faxa_rain in
    coastal mode: with ERA5 all precipitation reaches the ice as rain.
  - Boundary restoring (ice_restore_bry) reads BRY_<year>.nc files that the
    workflow does not make yet; the default is open edges, no restoring.
  - The initial ice is none (ice free) or a CICE restart given by the user.

Postprocessing: the SCHISM steps plus download_seaice / compare_seaice
(CICE daily aice at the SCHISM nodes vs NOAA/NSIDC, as for schism_mice).
"""

from typing import Optional

from workflow.models.ufs_schism.driver import UfsSchismDriver


class UfsSchismCiceDriver(UfsSchismDriver):
    name = "UFS_SCHISM+CICE"
    setup_steps = UfsSchismDriver.setup_steps | {"gen_cice_grid"}

    def preprocess(self, only: Optional[str] = None):
        _slurm_jobs = super().preprocess(only=only)

        if self.enabled("gen_ice_in", only):
            print("[STEP] gen_ice_in")
            from workflow.models.ufs_schism_cice.preprocess.gen_ice_in import (
                run_gen_ice_in,
            )
            if not run_gen_ice_in(self.cfg):
                raise SystemExit(1)
        else:
            print("[SKIP] gen_ice_in")
        return _slurm_jobs

    def postprocess(self, only: Optional[str] = None):
        """The SCHISM postprocessing (old I/O outputs) plus the sea-ice
        comparison with NOAA/NSIDC, reading the CICE history."""
        from workflow.models.schism_mice.postprocess.seaice_steps import (
            ICE_STEPS, run_seaice_steps,
        )
        if only not in ICE_STEPS:
            super().postprocess(only=only)
        run_seaice_steps(self.cfg, self.config_dir, only=only)

    def run(self, only: Optional[str] = None):
        self._warn_no_run_diagnostics()
        if self.enabled("setup_run", only):
            print("[STEP] setup_run")
            from workflow.models.ufs_schism_cice.run.setup_run import (
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
