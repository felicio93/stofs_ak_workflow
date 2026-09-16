"""
models/schism/run/run_manager.py
================================
Phase 4 dispatcher — SCHISM run management.

Called by SchismDriver.run(). Dispatches the enabled Phase 4 steps:

  setup_run   Populate R{ID}_{group_id}/ run directories (interactive, fast).
  submit_run  Launch auto_hotstart.py group-by-group, chaining end-of-group
              hotstarts into the next group. BLOCKING — run inside
              screen/tmux.

Works for all grouping modes (monthly, ndays/weekly/daily).
"""


def run_phase(cfg: dict, config_dir, only: str = None):
    def enabled(step: str) -> bool:
        if only is not None:
            return step == only
        return bool(cfg.get(step, False))

    if enabled("setup_run"):
        print("[STEP] setup_run")
        from workflow.models.schism.run.setup_run import (
            run_setup_run,
        )
        run_setup_run(cfg, config_dir)
    else:
        print("[SKIP] setup_run")

    if enabled("submit_run"):
        print("[STEP] submit_run")
        from workflow.models.schism.run.submit_run import (
            run_submit_run,
        )
        run_submit_run(cfg)
    else:
        print("[SKIP] submit_run")
