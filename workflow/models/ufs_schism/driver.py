"""
models/ufs_schism/driver.py
===========================
UfsSchismDriver — orchestrates all phases for the UFS-SCHISM model.

The orchestration follows the standalone SCHISM driver as closely as
possible, with the additional UFS-specific atmospheric/DATM preprocessing
steps inserted into Phase 2.

Phase map (steps.yaml flags):

  Phase 0  inspect_mesh
  Phase 1  download_{hycom,era5,glofas}
  Phase 2  aggregate_hycom, plot_hycom,
           gen_sflux, plot_sflux,
           gen_datm, plot_datm, gen_esmf_mesh,
           gen_datm_in, gen_datm_streams,
           copy_fd_ufs, copy_noahmptable,
           gen_model_configure, copy_modulefiles,
           gen_ufs_configure

  Phase 3  gen_estuary, gen_bctides, gen_source,
           gen_param, gen_hotstart, gen_3Dth, gen_nudge

  Phase 4  run management
  Phase 5  post-processing
"""

from workflow.models.base import ModelDriver


class UfsSchismDriver(ModelDriver):
    name = "UFS_SCHISM"

    # -------------------------------------------------------------------------
    # Phase 0-3 — Pre-processing
    #
    # Return a list of async SLURM job IDs that must complete before Phase 4
    # can safely start. This follows the standalone SCHISM driver.
    # -------------------------------------------------------------------------
    def preprocess(self, only: str = None):
        from workflow.core.config import model_dir, list_months

        cfg, config_dir = self.cfg, self.config_dir
        en = lambda step: self.enabled(step, only)

        # Collect asynchronous SLURM jobs so --phase all can wait for them.
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
        # Phase 1: downloads
        # =====================================================================

        if en("download_hycom"):
            print("[STEP] download_hycom")
            from workflow.downloaders.hycom import run_download

            run_download(cfg)
        else:
            print("[SKIP] download_hycom")

        if en("download_era5"):
            print("[STEP] download_era5")
            from workflow.downloaders.era5 import run_download_era5

            run_download_era5(cfg)
        else:
            print("[SKIP] download_era5")

        if en("download_glofas"):
            print("[STEP] download_glofas")
            from workflow.downloaders.glofas import run_download_glofas

            run_download_glofas(cfg)
        else:
            print("[SKIP] download_glofas")

        # =====================================================================
        # Phase 2: SCHISM forcing
        # =====================================================================

        # ---------------------------------------------------------------------
        # HYCOM aggregation
        # ---------------------------------------------------------------------

        if en("aggregate_hycom"):
            print("[STEP] aggregate_hycom")
            from workflow.models.schism.preprocess.aggregate_hycom import (
                run_aggregate,
            )

            run_aggregate(cfg)
        else:
            print("[SKIP] aggregate_hycom")

        # ---------------------------------------------------------------------
        # HYCOM diagnostic plots
        # ---------------------------------------------------------------------

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

        # ---------------------------------------------------------------------
        # SCHISM sflux
        # ---------------------------------------------------------------------

        if en("gen_sflux"):
            print("[STEP] gen_sflux")
            from workflow.models.schism.preprocess.submit_era5 import (
                submit_gen_sflux,
            )

            _gen_sflux_jobid = submit_gen_sflux(cfg, config_dir)

            if _gen_sflux_jobid:
                _slurm_jobs.append(_gen_sflux_jobid)
        else:
            print("[SKIP] gen_sflux")
            _gen_sflux_jobid = ""

        # ---------------------------------------------------------------------
        # SCHISM sflux plots
        # ---------------------------------------------------------------------

        if en("plot_sflux"):
            print("[STEP] plot_sflux")
            from workflow.models.schism.preprocess.submit_era5 import (
                submit_plot_sflux,
            )

            jid = submit_plot_sflux(
                cfg,
                config_dir,
                after_jobid=_gen_sflux_jobid,
            )

            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] plot_sflux")

        # =====================================================================
        # Phase 2: UFS-SCHISM / DATM forcing
        # =====================================================================

        # ---------------------------------------------------------------------
        # Convert SCHISM sflux -> UFS DATM forcing
        # ---------------------------------------------------------------------

        if en("gen_datm"):
            print("[STEP] gen_datm")
            from workflow.models.ufs_schism.preprocess.submit_datm import (
                submit_gen_datm,
            )

            _gen_datm_jobid = submit_gen_datm(
                cfg,
                config_dir,
                after_jobid=_gen_sflux_jobid,
            )

            if _gen_datm_jobid:
                _slurm_jobs.append(_gen_datm_jobid)
        else:
            print("[SKIP] gen_datm")
            _gen_datm_jobid = ""

        # ---------------------------------------------------------------------
        # DATM diagnostic plots
        # ---------------------------------------------------------------------

        if en("plot_datm"):
            print("[STEP] plot_datm")
            from workflow.models.ufs_schism.preprocess.submit_plot_datm import (
                submit_plot_datm,
            )

            jid = submit_plot_datm(
                cfg,
                config_dir,
                after_jobid=_gen_datm_jobid,
            )

            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] plot_datm")

        # ---------------------------------------------------------------------
        # Generate ESMF mesh
        # ---------------------------------------------------------------------

        if en("gen_esmf_mesh"):
            print("[STEP] gen_esmf_mesh")
            from workflow.models.ufs_schism.preprocess.submit_esmf_mesh import (
                submit_gen_esmf_mesh,
            )

            jid = submit_gen_esmf_mesh(
                cfg,
                config_dir,
                after_jobid=_gen_datm_jobid,
            )

            if jid:
                _slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_esmf_mesh")

        # =====================================================================
        # Phase 2: UFS configuration files
        #
        # These are interactive/local operations, matching the SCHISM driver's
        # treatment of other local preprocessing steps.
        # =====================================================================

        if en("gen_datm_in"):
            print("[STEP] gen_datm_in")

            from workflow.models.ufs_schism.preprocess.gen_datm_in import (
                gen_datm_in_month,
            )

            for ym in list_months(cfg):
                if gen_datm_in_month(cfg, ym):
                    sentinel = (
                        model_dir(cfg)
                        / f"I{cfg['project_id']}"
                        / f"I{cfg['project_id']}_{ym}"
                        / "gen_datm_in.done"
                    )
                    sentinel.touch()
        else:
            print("[SKIP] gen_datm_in")

        if en("gen_datm_streams"):
            print("[STEP] gen_datm_streams")

            from workflow.models.ufs_schism.preprocess.gen_datm_streams import (
                gen_datm_streams_month,
            )

            for ym in list_months(cfg):
                if gen_datm_streams_month(cfg, ym):
                    sentinel = (
                        model_dir(cfg)
                        / f"I{cfg['project_id']}"
                        / f"I{cfg['project_id']}_{ym}"
                        / "gen_datm_streams.done"
                    )
                    sentinel.touch()
        else:
            print("[SKIP] gen_datm_streams")

        if en("copy_fd_ufs"):
            print("[STEP] copy_fd_ufs")

            from workflow.models.ufs_schism.preprocess.copy_fd_ufs import (
                copy_fd_ufs_to_months,
            )

            copy_fd_ufs_to_months(cfg)
        else:
            print("[SKIP] copy_fd_ufs")

        if en("copy_noahmptable"):
            print("[STEP] copy_noahmptable")

            from workflow.models.ufs_schism.preprocess.copy_noahmptable import (
                copy_noahmptable_to_months,
            )

            copy_noahmptable_to_months(cfg)
        else:
            print("[SKIP] copy_noahmptable")

        if en("gen_model_configure"):
            print("[STEP] gen_model_configure")

            from workflow.models.ufs_schism.preprocess.gen_model_configure import (
                gen_model_configure_month,
            )

            for ym in list_months(cfg):
                if gen_model_configure_month(cfg, ym):
                    sentinel = (
                        model_dir(cfg)
                        / f"I{cfg['project_id']}"
                        / f"I{cfg['project_id']}_{ym}"
                        / "gen_model_configure.done"
                    )
                    sentinel.touch()
        else:
            print("[SKIP] gen_model_configure")

        if en("copy_modulefiles"):
            print("[STEP] copy_modulefiles")

            from workflow.models.ufs_schism.preprocess.copy_modulefiles import (
                copy_modulefiles_to_months,
            )

            copy_modulefiles_to_months(cfg)
        else:
            print("[SKIP] copy_modulefiles")

        if en("gen_ufs_configure"):
            print("[STEP] gen_ufs_configure")

            from workflow.models.ufs_schism.preprocess.gen_ufs_configure import (
                gen_ufs_configure_month,
            )

            for ym in list_months(cfg):
                if gen_ufs_configure_month(cfg, ym):
                    sentinel = (
                        model_dir(cfg)
                        / f"I{cfg['project_id']}"
                        / f"I{cfg['project_id']}_{ym}"
                        / "gen_ufs_configure.done"
                    )
                    sentinel.touch()
        else:
            print("[SKIP] gen_ufs_configure")

        # =====================================================================
        # Phase 3: SCHISM preprocessing
        #
        # Keep this section intentionally parallel to SchismDriver.
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

        # ---------------------------------------------------------------------
        # gen_hotstart
        # ---------------------------------------------------------------------

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

        # ---------------------------------------------------------------------
        # gen_3Dth
        # ---------------------------------------------------------------------

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

        # ---------------------------------------------------------------------
        # gen_nudge
        # ---------------------------------------------------------------------

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

        # Return collected job IDs so --phase all can wait for them.
        return _slurm_jobs

    # -------------------------------------------------------------------------
    # Phase 4 — Run management
    # -------------------------------------------------------------------------
    def run(self, only: str = None):
        from workflow.models.ufs_schism.run.run_manager import run_phase

        run_phase(
            self.cfg,
            self.config_dir,
            only=only,
        )

    # -------------------------------------------------------------------------
    # Phase 5 — Post-processing
    # -------------------------------------------------------------------------
    def postprocess(self, only: str = None):
        postprocess_steps = {
            "plot_outputs",
            "station_skill",
            "download_coops",
            "download_ndbc",
            "compare_sst",
            "download_sst",
            "diag_run_plots",
            "download_argo",
            "collocate_argo",
            "plot_argo",
        }

        any_enabled = any(
            self.enabled(step) for step in postprocess_steps
        )

        if not any_enabled and only is None:
            print(
                "All postprocessing phase steps are disabled. "
                "Skipping postprocessing phase."
            )
            return

        raise NotImplementedError(
            "Slice A only: postprocess not implemented "
            "for ufs_schism yet."
        )
