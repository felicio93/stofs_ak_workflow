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
        if en("gen_sflux"):
            print("[STEP] gen_sflux (reuse SCHISM gen_sflux)")
            from workflow.models.schism.preprocess.submit_era5 import submit_gen_sflux
            jid = submit_gen_sflux(cfg, config_dir)
            if jid: slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_sflux")

        # New: convert sflux -> DATM
        if en("gen_datm"):
            print("[STEP] gen_datm (sflux -> DATM)")
            from workflow.models.ufs_schism.preprocess.submit_datm import submit_gen_datm
            jid = submit_gen_datm(cfg, config_dir)
            if jid: slurm_jobs.append(jid)
        else:
            print("[SKIP] gen_datm")

        return slurm_jobs

    def run(self, only: str = None):
        raise NotImplementedError("Slice A only: run phase not implemented for ufs_schism yet.")

    def postprocess(self, only: str = None):
        raise NotImplementedError("Slice A only: postprocess not implemented for ufs_schism yet.")