"""
models/ufs_schism/run/run_manager.py
====================================
Phase 4 dispatcher — UFS-SCHISM run management.

Called by UfsSchismDriver.run(). Dispatches the enabled Phase 4 steps:

  setup_run   Populate R{ID}_YYYYMM/ run directories (interactive, fast).
  submit_run  Launch auto_hotstart.py month-by-month, chaining hotstarts.
              BLOCKING — launch inside screen/tmux.
"""


def run_phase(cfg: dict, config_dir, only: str = None):
    def enabled(step: str) -> bool:
        if only is not None:
            return step == only
        return bool(cfg.get(step, False))

    if enabled("setup_run"):
        print("[STEP] setup_run")
        from workflow.models.ufs_schism.run.setup_run import run_setup_run
        run_setup_run(cfg, config_dir)
    else:
        print("[SKIP] setup_run")

    if enabled("submit_run"):
        print("[STEP] submit_run")
        # submit_run behavior is identical to SCHISM — reuse it directly.
        from workflow.models.schism.run.submit_run import run_submit_run
        run_submit_run(cfg)
    else:
        print("[SKIP] submit_run")
