"""
models/ufs_schism/run/run_manager.py
================================
Phase 4 dispatcher — UFS-SCHISM run management.

Called by UfsSchismDriver.run(). Dispatches the enabled Phase 4 steps in order:

  setup_run   Populate R{ID}_YYYYMM/ run directories (symlinks, executables,
              job cards, auto_hotstart.py). Interactive and fast.
  submit_run  Launch auto_hotstart.py month-by-month, chaining end-of-month
              hotstarts into the next month. BLOCKING — run inside screen/tmux.

Hotstart chaining (chain_hotstart in schism.yaml) is handled entirely inside
each run directory's auto_hotstart.py, so there is no separate chain step here.
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
        from workflow.models.ufs_schism.run.submit_run import run_submit_run
        run_submit_run(cfg)
    else:
        print("[SKIP] submit_run")
