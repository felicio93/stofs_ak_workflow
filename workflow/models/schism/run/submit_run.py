"""
models/schism/run/submit_run.py
===============================
Phase 4, step "submit_run".

Launches auto_hotstart.py for the SCHISM monthly runs and blocks until they
complete. auto_hotstart.py is called as a subprocess with the run directory as
its working directory; it submits the SCHISM job, monitors it, combines the
end-of-month hotstart, and (when chain_hotstart is true) symlinks that hotstart
into the next month's run directory and launches the next month's
auto_hotstart.py itself.

Because the chaining is nested, with chain_hotstart=true a single call blocks
for the entire multi-month chain; the loop below then finds run.done for every
remaining month and skips it. With chain_hotstart=false, each month is launched
explicitly here, one after another.

This step is BLOCKING and can run for a long time (hours per month). Launch it
inside a screen/tmux session on the login node:

    screen -S stofs_run
    stofs-ak --run --phase run --only submit_run --config <cfg>
    # detach: Ctrl-A D    reattach: screen -r stofs_run

Resume-safe: months with a run.done sentinel are skipped, so re-running after
an interrupted session picks up where it left off.
"""

import subprocess
import sys
from pathlib import Path

from workflow.core.config import list_months, model_dir


def run_submit_run(cfg: dict):
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)

    print(f"\n{'='*60}")
    print(f"  submit_run for M{pid}")
    print(f"  {len(months)} month(s): {months[0]} -> {months[-1]}")
    print(f"  chain_hotstart: {bool(cfg.get('chain_hotstart', True))}")
    print(f"  NOTE: this is BLOCKING. Run inside screen/tmux.")
    print(f"{'='*60}")

    launched_any = False
    for ym in months:
        rdir = mdir / f"R{pid}" / f"R{pid}_{ym}"

        if (rdir / "run.done").exists():
            print(f"  {ym}: run.done present, skipping.")
            continue

        if not (rdir / "setup_run.done").exists():
            print(f"  ERROR {ym}: setup_run not done for {rdir}.")
            print("    Run:  stofs-ak --run --phase run --only setup_run --config <cfg>")
            sys.exit(1)

        auto = rdir / "auto_hotstart.py"
        if not auto.exists():
            print(f"  ERROR {ym}: {auto} not found (re-run setup_run).")
            sys.exit(1)

        print(f"\n{'='*60}")
        print(f"  Launching auto_hotstart.py for {ym}")
        print(f"  Run dir: {rdir}")
        print(f"  Screen log: {rdir}/scrn.out (auto_hotstart prints to stdout too)")
        print(f"{'='*60}\n")

        launched_any = True
        # Blocking. With chain_hotstart=true this returns only after the whole
        # downstream chain has finished; with chain_hotstart=false it returns
        # after just this month.
        result = subprocess.run([sys.executable, str(auto)], cwd=str(rdir))
        if result.returncode != 0:
            print(f"\n  ERROR: auto_hotstart.py failed for {ym} "
                  f"(exit {result.returncode}).")
            print(f"  Inspect {rdir}/outputs/mirror.out and re-run submit_run "
                  f"to resume.")
            sys.exit(result.returncode)

    print(f"\n{'='*60}")
    if launched_any:
        print("  submit_run complete. All launched months finished.")
    else:
        print("  All months already complete (run.done present). Nothing to do.")
    print(f"{'='*60}\n")
