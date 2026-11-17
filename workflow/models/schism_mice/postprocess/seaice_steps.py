"""
models/schism_mice/postprocess/seaice_steps.py
==============================================
The two sea-ice postprocessing steps, shared by the ice models
(schism_mice, ufs_schism_cice):

  download_seaice : NOAA/NSIDC sea-ice concentration CDR, daily (DTN)
  compare_seaice  : model vs NSIDC on the 25 km cells (SLURM)

The ice comparison is what these models are run for: a steps.yaml made
before the steps existed (no line for them) runs them; an explicit false
turns them off.
"""

from typing import Optional

ICE_STEPS = ("download_seaice", "compare_seaice")


def run_seaice_steps(cfg: dict, config_dir, only: Optional[str] = None):
    model = cfg.get("model_type", "")

    def en(step):
        if only is not None:
            return step == only
        if step not in cfg:
            print(f"[NOTE] {step}: not in steps.yaml, runs by default for "
                  f"model_type={model} (add '{step}: false' to skip)")
            return True
        return bool(cfg[step])

    if en("download_seaice"):
        from workflow.core.environment import dtn_skip, is_dtn
        if not is_dtn():
            dtn_skip("download_seaice")
        else:
            print("[STEP] download_seaice")
            from workflow.models.schism_mice.postprocess.download_seaice \
                import run_download_seaice
            run_download_seaice(cfg)
    else:
        print("[SKIP] download_seaice")

    if en("compare_seaice"):
        print("[STEP] compare_seaice")
        from workflow.models.schism_mice.postprocess.submit_compare_seaice \
            import submit_compare_seaice
        submit_compare_seaice(cfg, config_dir)
    else:
        print("[SKIP] compare_seaice")
