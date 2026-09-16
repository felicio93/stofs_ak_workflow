"""
models/ufs_schism/driver.py
===========================
UfsSchismDriver — orchestrates all phases for the UFS-SCHISM model.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

Resilience against all-flags-on steps.yaml
-------------------------------------------
DTN-only Phase 1 download steps (download_hycom, download_era5,
download_glofas) catch DtnRequiredError and print [N/A] instead of
crashing, allowing --phase all to continue on a login node.
"""

from workflow.models.base import ModelDriver
from workflow.core.environment import DtnRequiredError


def _na(step: str, reason: str):
    """Print a standardised [N/A] line for a skipped step."""
    print(f"[N/A]  {step}  ({reason})")


class UfsSchismDriver(ModelDriver):
    name = "UFS_SCHISM"

    # -------------------------------------------------------------------------
    # Phase 0-3 — Pre-processing
    # -------------------------------------------------------------------------
    def preprocess(self, only: str = None):
        from workflow.core.config import list_groups

        cfg, config_dir = self.cfg, self.config_dir
        en = lambda step: self.enabled(step, only)

        _slurm_jobs = []

        # =====================================================================
        # Phase 0: mesh diagnostics
        # =====================================================================
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

        # =====================================================================
        # Phase 1: downloads (DTN only)
        # Catch DtnRequiredError so --phase all on a login node
        # skips gracefully instead of crashing.
        # =====================================================================
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

        # =====================================================================
        # Phase 2: SCHISM forcing
        # =====================================================================
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

        # =====================================================================
        # Phase 2: UFS-SCHISM / DATM forcing
        # =====================================================================
        if en("gen_datm"):
            print("[STEP] gen_datm")
            from workflow.models.ufs_schism.preprocess.submit_datm import (
                submit_gen_datm,
            )
            _gen_datm_jobid = submit_gen_datm(
                cfg, config_dir,
                after_jobid=_gen_sflux_jobid)
            if _gen_datm_jobid:
                _slurm_jobs.append(_gen_datm_jobid)
        else:
            print("[SKIP] gen_datm")
            _gen_datm_jobid = ""

        if en("plot_datm"):
            print("[STEP] plot_datm")
            from workflow.models.ufs_schism.preprocess.submit_plot_datm import (
                submit_plot_datm,
            )
            jid = submit_plot_datm(
                cfg, config_dir,
                after_jobid=_gen_datm_jobid)
            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] plot_datm")

        if en("gen_esmf_mesh"):
            print("[STEP] gen_esmf_mesh")
            from workflow.models.ufs_schism.preprocess.submit_esmf_mesh import (
                submit_gen_esmf_mesh,
            )
            jid = submit_gen_esmf_mesh(
                cfg, config_dir,
                after_jobid=_gen_datm_jobid)
            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_esmf_mesh")

        # =====================================================================
        # Phase 2: UFS configuration files (interactive, local)
        # =====================================================================
        if en("gen_datm_in"):
            print("[STEP] gen_datm_in")
            from workflow.models.ufs_schism.preprocess.gen_datm_in import (
                gen_datm_in_group,
            )
            for group_id in list_groups(cfg):
                gen_datm_in_group(cfg, group_id)
        else:
            print("[SKIP] gen_datm_in")

        if en("gen_datm_streams"):
            print("[STEP] gen_datm_streams")
            from workflow.models.ufs_schism.preprocess.gen_datm_streams import (
                gen_datm_streams_group,
            )
            for group_id in list_groups(cfg):
                gen_datm_streams_group(cfg, group_id)
        else:
            print("[SKIP] gen_datm_streams")

        if en("copy_fd_ufs"):
            print("[STEP] copy_fd_ufs")
            from workflow.models.ufs_schism.preprocess.copy_fd_ufs import (
                copy_fd_ufs_to_groups,
            )
            copy_fd_ufs_to_groups(cfg)
        else:
            print("[SKIP] copy_fd_ufs")

        if en("copy_noahmptable"):
            print("[STEP] copy_noahmptable")
            from workflow.models.ufs_schism.preprocess.copy_noahmptable import (
                copy_noahmptable_to_groups,
            )
            copy_noahmptable_to_groups(cfg)
        else:
            print("[SKIP] copy_noahmptable")

        if en("gen_model_configure"):
            print("[STEP] gen_model_configure")
            from workflow.models.ufs_schism.preprocess.gen_model_configure import (
                gen_model_configure_group,
            )
            for group_id in list_groups(cfg):
                gen_model_configure_group(cfg, group_id)
        else:
            print("[SKIP] gen_model_configure")

        if en("copy_modulefiles"):
            print("[STEP] copy_modulefiles")
            from workflow.models.ufs_schism.preprocess.copy_modulefiles import (
                copy_modulefiles_to_groups,
            )
            copy_modulefiles_to_groups(cfg)
        else:
            print("[SKIP] copy_modulefiles")

        if en("gen_ufs_configure"):
            print("[STEP] gen_ufs_configure")
            from workflow.models.ufs_schism.preprocess.gen_ufs_configure import (
                gen_ufs_configure_group,
            )
            for group_id in list_groups(cfg):
                gen_ufs_configure_group(cfg, group_id)
        else:
            print("[SKIP] gen_ufs_configure")

        # =====================================================================
        # Phase 3: SCHISM preprocessing (identical to SchismDriver)
        # =====================================================================
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

        # =====================================================================
        # WWM-only steps: skip silently for UFS-SCHISM
        # =====================================================================
        for step in ("gen_wwmbnd", "gen_wwminput"):
            if en(step):
                _na(step,
                    "SCHISM+WWM only, skipped for "
                    "model_type=ufs_schism")

        return _slurm_jobs

    # -------------------------------------------------------------------------
    # Phase 4 — Run management
    # -------------------------------------------------------------------------
    def run(self, only: str = None):
        from workflow.models.ufs_schism.run.run_manager import (
            run_phase,
        )
        run_phase(self.cfg, self.config_dir, only=only)

    # -------------------------------------------------------------------------
    # Phase 5 — Post-processing
    # UFS-SCHISM produces identical SCHISM output so the SCHISM
    # postprocessing pipeline is reused without modification.
    # DTN-step resilience is handled inside postprocess_phase().
    # -------------------------------------------------------------------------
    def postprocess(self, only: str = None):
        from workflow.models.schism.postprocess import (
            postprocess_phase,
        )
        postprocess_phase(
            self.cfg, self.config_dir, only=only)
