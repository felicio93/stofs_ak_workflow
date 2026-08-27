from workflow.models.base import ModelDriver

class UfsSchismDriver(ModelDriver):
    name = "UFS_SCHISM"

    def preprocess(self, only: str = None):
        cfg, config_dir = self.cfg, self.config_dir
        en = lambda step: self.enabled(step, only)
        slurm_jobs = []

        # Reuse existing downloads
        if en("download_era5"):
            print("[STEP] download_era5")
            from workflow.downloaders.era5 import run_download_era5
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
                gen_datm_in_month(cfg, ym)
        else:
            print("[SKIP] gen_datm_in")

        # New: generate datm.streams
        if en("gen_datm_streams"):
            print("[STEP] gen_datm_streams (Generate datm.streams file)")
            from workflow.core.config import list_months
            from workflow.models.ufs_schism.preprocess.gen_datm_streams import gen_datm_streams_month
            for ym in list_months(cfg):
                gen_datm_streams_month(cfg, ym)
        else:
            print("[SKIP] gen_datm_streams")

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