"""
models/schism_mice/driver.py
============================
SchismMiceDriver — SCHISM with its multi-class sea-ice module (USE_MICE:
Icepack column physics, mEVP/EVP dynamics), internally coupled.

Inherits all SCHISM steps and adds:
  - download_hycom_ice : HYCOM sic/sih/siu/siv at the start date (DTN)
  - gen_mice_nml       : mice.nml per group (ihot_mice: first group from
                         fix/mice.nml, later groups 1)
  - gen_ice_hotstart   : HYCOM ice -> sic2/sih2/siu2/siv2 in the first
                         group's hotstart.nc (SLURM, after gen_hotstart);
                         only when the first group starts the ice from HYCOM
                         (ihot_mice = 2)
and runs with the MICE run profile (executables.schism_mice, mice.nml and
namelist.icepack linked into every run directory).

The ERA5 download and gen_sflux add the snowfall rate (srate in sflux_prc)
for this model (downloaders/era5.SNOWFALL_MODELS).
"""

from typing import Optional

from workflow.core.environment import DtnRequiredError
from workflow.core.config import model_dir
from workflow.models.schism.driver import SchismDriver, _na
from workflow.models.schism_mice import mice_nml


class SchismMiceDriver(SchismDriver):
    name = "SCHISM+MICE"

    def _first_ihot_mice(self):
        p = model_dir(self.cfg) / "fix" / "mice.nml"
        return mice_nml.ihot_mice(p.read_text()) if p.exists() else None

    def preprocess(self, only: Optional[str] = None):
        cfg = self.cfg
        en  = lambda step: self.enabled(step, only)

        if en("download_hycom_ice"):
            if self._first_ihot_mice() == 2:
                try:
                    print("[STEP] download_hycom_ice")
                    from workflow.downloaders.hycom import run_download_ice
                    if run_download_ice(cfg) is False:
                        raise SystemExit("ERROR: download_hycom_ice failed; "
                                         "re-run it before gen_ice_hotstart.")
                except DtnRequiredError as exc:
                    _na("download_hycom_ice",
                        f"DTN required — run separately on the DTN.\n"
                        f"         {exc}")
            else:
                _na("download_hycom_ice", "fix/mice.nml ihot_mice is not 2 "
                    "(the ice does not start from HYCOM)")
        else:
            self._skip("download_hycom_ice")

        slurm_jobs = super().preprocess(only=only)

        if en("gen_mice_nml"):
            print("[STEP] gen_mice_nml")
            from workflow.models.schism_mice.preprocess.gen_mice_nml import (
                run_gen_mice_nml,
            )
            if not run_gen_mice_nml(cfg):
                raise SystemExit(1)
        else:
            self._skip("gen_mice_nml")

        if en("gen_ice_hotstart"):
            if self._first_ihot_mice() == 2:
                print("[STEP] gen_ice_hotstart")
                from workflow.models.schism_mice.preprocess.gen_ice_hotstart \
                    import submit_gen_ice_hotstart
                jid = submit_gen_ice_hotstart(
                    cfg, self.config_dir,
                    after_jobid=self.submitted.get("gen_hotstart", ""))
                if jid:
                    slurm_jobs.append(jid)
            else:
                _na("gen_ice_hotstart", "fix/mice.nml ihot_mice is not 2 "
                    "(the ice does not start from HYCOM)")
        else:
            self._skip("gen_ice_hotstart")

        return slurm_jobs

    def run(self, only: Optional[str] = None):
        from workflow.models.schism.run.run_manager import run_phase
        from workflow.models.schism_mice.run.setup_run import profile
        run_phase(self.cfg, self.config_dir, only=only,
                  profile=profile(self.cfg))
