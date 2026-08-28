from workflow.models.base import ModelDriver

class UfsSchismDriver(ModelDriver):
    name = "UFS_SCHISM"

    def preprocess(self, only: str = None):
        from workflow.core.config import model_dir, list_months
        cfg, config_dir = self.cfg, self.config_dir
        en = lambda step: self.enabled(step, only)
        slurm_jobs = []

        # Reuse existing downloads
        if en("download_era5"):
            print("[STEP] download_era5")
            from workflow.core.config import model_dir, list_months
            run_download_era5(cfg)
        else:
            print("[SKIP] download_era5")

        # Reuse existing SCHISM gen_sflux
        sflux_jid = None
        if en("gen_sflux"):
            print("[STEP] gen_sflux (reuse SCHISM gen_sflux)")
            from workflow.models.schism.preprocess.submit_era5 import submit_gen_sflux
            sflux_jid = submit_gen_sflux(cfg, config_dir)
            if sflux_jid: slurm_jobs.append(sflux_jid)
        else:
            print("[SKIP] gen_sflux")

        # New: convert sflux -> DATM
        datm_jid = None
        if en("gen_datm"):
            print("[STEP] gen_datm (sflux -> DATM)")
            from workflow.models.ufs_schism.preprocess.submit_datm import submit_gen_datm
            datm_jid = submit_gen_datm(cfg, config_dir, after_jobid=sflux_jid)
            if datm_jid: slurm_jobs.append(datm_jid)
        else:
            print("[SKIP] gen_datm")

        # New: plot DATM
        if en("plot_datm"):
            print("[STEP] plot_datm (DATM debug plots)")
            from workflow.models.ufs_schism.preprocess.submit_plot_datm import submit_plot_datm
            jid = submit_plot_datm(cfg, config_dir, after_jobid=datm_jid)
            if jid: slurm_jobs.append(jid)
        else:
            print("[SKIP] plot_datm")

        # New: generate ESMF mesh
        if en("gen_esmf_mesh"):
            print("[STEP] gen_esmf_mesh (Generate ESMF mesh file)")
            from workflow.models.ufs_schism.preprocess.submit_esmf_mesh import submit_gen_esmf_mesh
            jid = submit_gen_esmf_mesh(cfg, config_dir, after_jobid=datm_jid)
            if jid: slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_esmf_mesh")

        # New: generate datm_in
        if en("gen_datm_in"):
            print("[STEP] gen_datm_in (Generate datm_in file)")
            from workflow.core.config import list_months
            from workflow.models.ufs_schism.preprocess.gen_datm_in import gen_datm_in_month
            for ym in list_months(cfg):
                if gen_datm_in_month(cfg, ym):
                    sentinel = model_dir(cfg) / f"I{cfg['project_id']}" / f"I{cfg['project_id']}_{ym}" / "gen_datm_in.done"
                    sentinel.touch()
        else:
            print("[SKIP] gen_datm_in")

        # New: generate datm.streams
        if en("gen_datm_streams"):
            print("[STEP] gen_datm_streams (Generate datm.streams file)")
            from workflow.core.config import list_months
            from workflow.models.ufs_schism.preprocess.gen_datm_streams import gen_datm_streams_month
            for ym in list_months(cfg):
                if gen_datm_streams_month(cfg, ym):
                    sentinel = model_dir(cfg) / f"I{cfg['project_id']}" / f"I{cfg['project_id']}_{ym}" / "gen_datm_streams.done"
                    sentinel.touch()
        else:
            print("[SKIP] gen_datm_streams")

        # New: copy fd_ufs.yaml
        if en("copy_fd_ufs"):
            print("[STEP] copy_fd_ufs (Copy fd_ufs.yaml)")
            from workflow.models.ufs_schism.preprocess.copy_fd_ufs import copy_fd_ufs_to_months
            copy_fd_ufs_to_months(cfg)
        else:
            print("[SKIP] copy_fd_ufs")

        # New: copy noahmptable.tbl
        if en("copy_noahmptable"):
            print("[STEP] copy_noahmptable (Copy noahmptable.tbl)")
            from workflow.models.ufs_schism.preprocess.copy_noahmptable import copy_noahmptable_to_months
            copy_noahmptable_to_months(cfg)
        else:
            print("[SKIP] copy_noahmptable")

        # New: generate model_configure
        if en("gen_model_configure"):
            print("[STEP] gen_model_configure (Generate model_configure file)")
            from workflow.core.config import list_months
            from workflow.models.ufs_schism.preprocess.gen_model_configure import gen_model_configure_month
            for ym in list_months(cfg):
                if gen_model_configure_month(cfg, ym):
                    sentinel = model_dir(cfg) / f"I{cfg['project_id']}" / f"I{cfg['project_id']}_{ym}" / "gen_model_configure.done"
                    sentinel.touch()
        else:
            print("[SKIP] gen_model_configure")

        # New: copy modulefiles
        if en("copy_modulefiles"):
            print("[STEP] copy_modulefiles (Copy .lua modulefiles)")
            from workflow.models.ufs_schism.preprocess.copy_modulefiles import copy_modulefiles_to_months
            copy_modulefiles_to_months(cfg)
        else:
            print("[SKIP] copy_modulefiles")

        # New: generate ufs.configure
        if en("gen_ufs_configure"):
            print("[STEP] gen_ufs_configure (Generate ufs.configure file)")
            from workflow.core.config import list_months
            from workflow.models.ufs_schism.preprocess.gen_ufs_configure import gen_ufs_configure_month
            for ym in list_months(cfg):
                if gen_ufs_configure_month(cfg, ym):
                    sentinel = model_dir(cfg) / f"I{cfg['project_id']}" / f"I{cfg['project_id']}_{ym}" / "gen_ufs_configure.done"
                    sentinel.touch()
        else:
            print("[SKIP] gen_ufs_configure")

        # --- Phase 3: SCHISM preprocessing ----------------------------------
        if en("gen_estuary"):
            print("[STEP] gen_estuary")
            from workflow.models.schism.preprocess.gen_estuary import run_gen_estuary
            run_gen_estuary(cfg)
        else:
            print("[SKIP] gen_estuary")

        if en("gen_bctides"):
            print("[STEP] gen_bctides")
            from workflow.models.schism.preprocess.gen_bctides import run_gen_bctides
            run_gen_bctides(cfg)
        else:
            print("[SKIP] gen_bctides")

        if en("gen_source"):
            print("[STEP] gen_source")
            from workflow.models.schism.preprocess.gen_source import run_gen_source
            run_gen_source(cfg)
        else:
            print("[SKIP] gen_source")

        if en("gen_param"):
            print("[STEP] gen_param")
            from workflow.models.schism.preprocess.gen_param import run_gen_param
            run_gen_param(cfg)
        else:
            print("[SKIP] gen_param")

        if en("gen_hotstart"):
            print("[STEP] gen_hotstart")
            from workflow.models.schism.preprocess.gen_hycom_utils import submit_gen_hotstart
            jid = submit_gen_hotstart(cfg, config_dir)
            if jid: slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_hotstart")

        if en("gen_3Dth"):
            print("[STEP] gen_3Dth")
            from workflow.models.schism.preprocess.gen_hycom_utils import submit_gen_3Dth
            jid = submit_gen_3Dth(cfg, config_dir)
            if jid: slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_3Dth")

        if en("gen_nudge"):
            print("[STEP] gen_nudge")
            from workflow.models.schism.preprocess.gen_hycom_utils import submit_gen_nudge
            jid = submit_gen_nudge(cfg, config_dir)
            if jid: slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_nudge")

        return slurm_jobs

    def run(self, only: str = None):
        if not self.enabled("setup_run") and not self.enabled("submit_run"):
            print("All run phase steps are disabled. Skipping run phase.")
            return

        raise NotImplementedError("Slice A only: run phase not implemented for ufs_schism yet.")

    def postprocess(self, only: str = None):
        postprocess_steps = {
            "plot_outputs", "station_skill", "download_coops", "download_ndbc",
            "compare_sst", "download_sst", "diag_run_plots", "download_argo",
            "collocate_argo", "plot_argo"
        }
        any_enabled = any(self.enabled(step) for step in postprocess_steps)

        if not any_enabled and only is None:
            print("All postprocessing phase steps are disabled. Skipping postprocessing phase.")
            return

        raise NotImplementedError("Slice A only: postprocess not implemented for ufs_schism yet.")