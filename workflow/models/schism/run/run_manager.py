"""
models/schism/run/run_manager.py
================================
PLACEHOLDER — SCHISM run management (Phase 4). Not yet implemented.

Planned responsibilities
------------------------
  submit_run      Render a run_schism.sbatch template and submit the SCHISM
                  run for each month's R{ID}_YYYYMM/ directory.
  monitor_run     Poll squeue/sacct, detect completion or failure, and write a
                  run.done sentinel per month.
  chain_hotstart  When month N finishes, promote its outputs/hotstart*.nc into
                  month N+1's run directory so the next month can start. Can be
                  driven either by SLURM --dependency=afterok chaining or by a
                  polling loop here.

Wire-up: SchismDriver.run() calls run_phase() below. Add steps to steps.yaml
under the "Phase 4" block and dispatch them here, mirroring preprocess().
"""


def run_phase(cfg: dict, config_dir, only: str = None):
    raise NotImplementedError(
        "SCHISM run management (Phase 4) is not implemented yet. "
        "See workflow/models/schism/run/run_manager.py for the plan."
    )
