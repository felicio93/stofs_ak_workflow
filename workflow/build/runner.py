"""
build/runner.py
===============
The `build` phase: submit every target of build.yaml (or one, with --only),
wait for the jobs and verify that each target is up to date.

Targets that are already built (sentinel holding the current recipe hash and
the installed files present) are skipped, so the phase is cheap when nothing
changed and is safe to run first in `--phase all`.
"""

import sys
from pathlib import Path

from workflow.build.builder import build_target, is_current, target_hash
from workflow.build.recipe import check_build


def run_build_phase(cfg: dict, config_dir: Path, only=None):
    """Build the targets of build.yaml; exit if any ends up not built."""
    if "build" not in cfg:
        print("[SKIP] build: no build.yaml in the config directory")
        return
    targets = cfg["build"]["targets"]
    if only is not None and only != "build_executables" and only not in targets:
        return                       # --only names a step of another phase
    if only is None and not cfg.get("build_executables", False):
        print("[SKIP] build_executables")
        return

    check_build(cfg)
    names = list(targets) if only in (None, "build_executables") else [only]
    print("[STEP] build_executables: " + ", ".join(names))

    jobs = []
    for name in names:
        jid = build_target(cfg, config_dir, name)
        if jid:
            jobs.append(jid)
    if jobs:
        from workflow.core.slurm import wait_for_slurm_jobs
        wait_for_slurm_jobs(jobs, poll_seconds=30, label="build jobs",
                            tag="[build]", next_step="the checks")

    stale = [n for n in names if not is_current(cfg, n)]
    if stale:
        print(f"\n  ERROR: not built / not up to date: {', '.join(stale)}")
        print("  See M*/logs/build_<target>.out and .err")
        sys.exit(1)
    print("\n  Build targets up to date: "
          + ", ".join(f"{n} ({target_hash(cfg, n)})" for n in names))
