"""
models/schism/driver.py
=======================
SchismDriver — orchestrates all phases for the standalone SCHISM model.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

Resilience against all-flags-on steps.yaml
-------------------------------------------
* UFS-SCHISM-only steps (gen_datm, copy_fd_ufs, etc.) are silently
  skipped with a [N/A] tag — they are irrelevant for model_type=schism.
* SCHISM+WWM-only steps (gen_wwmbnd, gen_wwminput) are similarly
  skipped with [N/A].
* DTN-only Phase 1 download steps (download_hycom, download_era5,
  download_glofas) catch DtnRequiredError and print [N/A] instead of
  crashing, allowing --phase all to continue on a login node.
"""

from pathlib import Path

from workflow.models.base import ModelDriver
from workflow.core.environment import DtnRequiredError

# Steps that belong to UFS-SCHISM only.
# Silently skipped when model_type is schism or schism_wwm.
_UFS_ONLY_STEPS = {
    "gen_datm", "plot_datm", "gen_esmf_mesh",
    "gen_datm_in", "gen_datm_streams",
    "copy_fd_ufs", "copy_noahmptable",
    "gen_model_configure", "copy_modulefiles",
    "gen_ufs_configure",
}

# Steps that belong to SCHISM+WWM only.
# Silently skipped when model_type is schism (standalone).
_WWM_ONLY_STEPS = {
    "gen_wwmbnd", "gen_wwminput",
}


def _na(step: str, reason: str):
    """Print a standardised [N/A] line for a skipped step."""
    print(f"[N/A]  {step}  ({reason})")


class SchismDriver(ModelDriver):
    name = "SCHISM"

    # -------------------------------------------------------------------------
    # Phase 0-3 — Pre-processing
    # -------------------------------------------------------------------------
    def preprocess(self, only: str = None):
        cfg, config_dir = self.cfg, self.config_dir
        en = lambda step: self.enabled(step, only)

        self._preprocess_compat_warning(only)

        _slurm_jobs = []

        # ---- Phase 0: mesh diagnostics ----
        if en("inspect_mesh"):
            print("[STEP] inspect_mesh")
            from workflow.diagnostics.submit_inspect_mesh import (
                submit_inspect_mesh,
            )
            jid = submit_inspect_mesh(cfg, config_dir)
            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] inspect_mesh")

        # ---- Phase 1: downloads (DTN only) ----
        # Catch DtnRequiredError so --phase all on a login node
        # skips gracefully instead of crashing.

        if en("download_hycom"):
            try:
                print("[STEP] download_hycom")
                from workflow.downloaders.hycom import (
                    run_download,
                )
                run_download(cfg)
            except DtnRequiredError as exc:
                _na("download_hycom",
                    f"DTN required — run separately on the "
                    f"DTN.\n         {exc}")
        else:
            print("[SKIP] download_hycom")

        if en("download_era5"):
            try:
                print("[STEP] download_era5")
                from workflow.downloaders.era5 import (
                    run_download_era5,
                )
                run_download_era5(cfg)
            except DtnRequiredError as exc:
                _na("download_era5",
                    f"DTN required — run separately on the "
                    f"DTN.\n         {exc}")
        else:
            print("[SKIP] download_era5")

        if en("download_glofas"):
            try:
                print("[STEP] download_glofas")
                from workflow.downloaders.glofas import (
                    run_download_glofas,
                )
                run_download_glofas(cfg)
            except DtnRequiredError as exc:
                _na("download_glofas",
                    f"DTN required — run separately on the "
                    f"DTN.\n         {exc}")
        else:
            print("[SKIP] download_glofas")

        # ---- UFS-only steps: skip silently ----
        for step in sorted(_UFS_ONLY_STEPS):
            if en(step):
                _na(step,
                    "UFS-SCHISM only, skipped for "
                    "model_type=schism")

        # ---- WWM-only steps: skip silently ----
        for step in sorted(_WWM_ONLY_STEPS):
            if en(step):
                _na(step,
                    "SCHISM+WWM only, skipped for "
                    "model_type=schism")

        # ---- Phase 2: processing ----
        if en("aggregate_hycom"):
            print("[STEP] aggregate_hycom")
            from workflow.models.schism.preprocess.aggregate_hycom import (
                run_aggregate,
            )
            run_aggregate(cfg)
        else:
            print("[SKIP] aggregate_hycom")

        if en("plot_hycom"):
            print("[STEP] plot_hycom")
            from workflow.diagnostics.submit_plots import (
                submit_plotting_jobs,
            )
            jid = submit_plotting_jobs(cfg, config_dir)
            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] plot_hycom")

        if en("gen_sflux"):
            print("[STEP] gen_sflux")
            from workflow.models.schism.preprocess.submit_era5 import (
                submit_gen_sflux,
            )
            _gen_sflux_jobid = submit_gen_sflux(
                cfg, config_dir)
            if _gen_sflux_jobid:
                _slurm_jobs.append(_gen_sflux_jobid)
        else:
            print("[SKIP] gen_sflux")
            _gen_sflux_jobid = ""

        if en("plot_sflux"):
            print("[STEP] plot_sflux")
            from workflow.models.schism.preprocess.submit_era5 import (
                submit_plot_sflux,
            )
            jid = submit_plot_sflux(
                cfg, config_dir,
                after_jobid=_gen_sflux_jobid)
            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] plot_sflux")

        # ---- Phase 3: SCHISM preprocessing ----
        if en("gen_estuary"):
            print("[STEP] gen_estuary")
            from workflow.models.schism.preprocess.gen_estuary import (
                run_gen_estuary,
            )
            run_gen_estuary(cfg)
        else:
            print("[SKIP] gen_estuary")

        if en("gen_bctides"):
            print("[STEP] gen_bctides")
            from workflow.models.schism.preprocess.gen_bctides import (
                run_gen_bctides,
            )
            run_gen_bctides(cfg)
        else:
            print("[SKIP] gen_bctides")

        if en("gen_source"):
            print("[STEP] gen_source")
            from workflow.models.schism.preprocess.gen_source import (
                run_gen_source,
            )
            run_gen_source(cfg)
        else:
            print("[SKIP] gen_source")

        if en("gen_param"):
            print("[STEP] gen_param")
            from workflow.models.schism.preprocess.gen_param import (
                run_gen_param,
            )
            run_gen_param(cfg)
        else:
            print("[SKIP] gen_param")

        if en("gen_hotstart"):
            print("[STEP] gen_hotstart")
            from workflow.models.schism.preprocess.gen_hycom_utils import (
                submit_gen_hotstart,
            )
            jid = submit_gen_hotstart(cfg, config_dir)
            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_hotstart")

        if en("gen_3Dth"):
            print("[STEP] gen_3Dth")
            from workflow.models.schism.preprocess.gen_hycom_utils import (
                submit_gen_3Dth,
            )
            jid = submit_gen_3Dth(cfg, config_dir)
            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_3Dth")

        if en("gen_nudge"):
            print("[STEP] gen_nudge")
            from workflow.models.schism.preprocess.gen_hycom_utils import (
                submit_gen_nudge,
            )
            jid = submit_gen_nudge(cfg, config_dir)
            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_nudge")

        return _slurm_jobs

    # -------------------------------------------------------------------------
    # Phase 4 — Run management
    # -------------------------------------------------------------------------
    def run(self, only: str = None):
        from workflow.models.schism.run.run_manager import (
            run_phase,
        )
        run_phase(self.cfg, self.config_dir, only=only)

    # -------------------------------------------------------------------------
    # Phase 5 — Post-processing
    # -------------------------------------------------------------------------
    def postprocess(self, only: str = None):
        from workflow.models.schism.postprocess import (
            postprocess_phase,
        )
        postprocess_phase(
            self.cfg, self.config_dir, only=only)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------
    def _preprocess_compat_warning(self, only):
        """download_hycom (DTN, no sbatch) and plot_hycom
        (needs sbatch) generally cannot succeed in one
        invocation on the same node.
        """
        if (only is None
                and self.enabled("download_hycom")
                and self.enabled("plot_hycom")):
            print(f"\n  {'!'*58}")
            print("  WARNING: download_hycom and plot_hycom "
                  "are both enabled.")
            print("  These run in different contexts and "
                  "usually cannot succeed in")
            print("  one invocation on a single node:")
            print("    - download_hycom needs the DTN "
                  "(internet, no sbatch)")
            print("    - plot_hycom needs a node with sbatch "
                  "(login node)")
            print("  Run them separately. Continuing with "
                  "enabled steps in order...")
            print(f"  {'!'*58}")
