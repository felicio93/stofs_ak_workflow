"""
models/schism/driver.py
=======================
SchismDriver — orchestrates all phases for the standalone SCHISM model.

This absorbs the step-dispatch logic that previously lived directly in
orchestrator.run_workflow(), and it owns the (formerly standalone)
submit_*.py launchers. The orchestrator now simply builds the driver via
workflow.models.base.make_driver and calls preprocess()/run()/postprocess().

Phase map (steps.yaml flags):
  Phase 0  inspect_mesh                              -> diagnostics.submit_inspect_mesh
  Phase 1  download_{hycom,era5,glofas}              -> workflow.downloaders.*  (DTN)
  Phase 2  aggregate_hycom, plotting_debug,
           gen_sflux, plot_sflux
  Phase 3  gen_estuary, gen_bctides, gen_source,
           gen_param, gen_hotstart, gen_3Dth, gen_nudge
  Phase 4  run management                            -> run.run_manager  (stub)
  Phase 5  post-processing                           -> postprocess.*     (stub)
"""

from pathlib import Path

from workflow.models.base import ModelDriver


class SchismDriver(ModelDriver):
    name = "SCHISM"

    # -------------------------------------------------------------------------
    # Phase 1-3 — Pre-processing
    # -------------------------------------------------------------------------
    def preprocess(self, only: str = None):
        cfg, config_dir = self.cfg, self.config_dir
        en = lambda step: self.enabled(step, only)

        self._preprocess_compat_warning(only)

        # --- Phase 0: mesh diagnostics --------------------------------------
        if en("inspect_mesh"):
            print("[STEP] inspect_mesh")
            from workflow.diagnostics.submit_inspect_mesh import submit_inspect_mesh
            submit_inspect_mesh(cfg, config_dir)
        else:
            print("[SKIP] inspect_mesh")

        # --- Phase 1: downloads (DTN) ---------------------------------------
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

        # --- Phase 2: processing --------------------------------------------
        if en("aggregate_hycom"):
            print("[STEP] aggregate_hycom")
            from workflow.models.schism.preprocess.aggregate_hycom import run_aggregate
            run_aggregate(cfg)
        else:
            print("[SKIP] aggregate_hycom")

        if en("plotting_debug"):
            print("[STEP] plotting_debug")
            from workflow.diagnostics.submit_plots import submit_plotting_jobs
            submit_plotting_jobs(cfg, config_dir)
        else:
            print("[SKIP] plotting_debug")

        if en("gen_sflux"):
            print("[STEP] gen_sflux")
            from workflow.models.schism.preprocess.submit_era5 import submit_gen_sflux
            _gen_sflux_jobid = submit_gen_sflux(cfg, config_dir)
        else:
            print("[SKIP] gen_sflux")
            _gen_sflux_jobid = ""

        if en("plot_sflux"):
            print("[STEP] plot_sflux")
            from workflow.models.schism.preprocess.submit_era5 import submit_plot_sflux
            submit_plot_sflux(cfg, config_dir, after_jobid=_gen_sflux_jobid)
        else:
            print("[SKIP] plot_sflux")

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
            submit_gen_hotstart(cfg, config_dir)
        else:
            print("[SKIP] gen_hotstart")

        if en("gen_3Dth"):
            print("[STEP] gen_3Dth")
            from workflow.models.schism.preprocess.gen_hycom_utils import submit_gen_3Dth
            submit_gen_3Dth(cfg, config_dir)
        else:
            print("[SKIP] gen_3Dth")

        if en("gen_nudge"):
            print("[STEP] gen_nudge")
            from workflow.models.schism.preprocess.gen_hycom_utils import submit_gen_nudge
            submit_gen_nudge(cfg, config_dir)
        else:
            print("[SKIP] gen_nudge")

    # -------------------------------------------------------------------------
    # Phase 4 — Run management  (placeholder; see run/run_manager.py)
    # -------------------------------------------------------------------------
    def run(self, only: str = None):
        from workflow.models.schism.run.run_manager import run_phase
        run_phase(self.cfg, self.config_dir, only=only)

    # -------------------------------------------------------------------------
    # Phase 5 — Post-processing  (placeholder; see postprocess/)
    # -------------------------------------------------------------------------
    def postprocess(self, only: str = None):
        from workflow.models.schism.postprocess import postprocess_phase
        postprocess_phase(self.cfg, self.config_dir, only=only)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------
    def _preprocess_compat_warning(self, only):
        """download_hycom (DTN, no sbatch) and plotting_debug (needs sbatch)
        generally cannot succeed in one invocation on the same node."""
        if only is None and self.enabled("download_hycom") and self.enabled("plotting_debug"):
            print(f"\n  {'!'*58}")
            print("  WARNING: download_hycom and plotting_debug are both enabled.")
            print("  These run in different contexts and usually cannot succeed in")
            print("  one invocation on a single node:")
            print("    - download_hycom needs the DTN (internet, no sbatch)")
            print("    - plotting_debug needs a node with sbatch (login node)")
            print("  Run them separately. Continuing with enabled steps in order...")
            print(f"  {'!'*58}")
