"""
models/ufs_schism/run/submit_run.py
===================================
Phase 4, step "submit_run" for UFS-SCHISM.

Launches auto_hotstart.py for the UFS-SCHISM group runs and blocks
until they complete. Behavior is identical to the SCHISM submit_run:
groups with run.done are skipped (resume-safe), chain_hotstart controls
whether auto_hotstart.py launches the next group automatically.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

This step is BLOCKING. Run inside a screen/tmux session:

    screen -S stofs_run
    stofs-ak --run --phase run --only submit_run --config <cfg>
    # detach: Ctrl-A D    reattach: screen -r stofs_run
"""

import subprocess
import sys
from pathlib import Path

from workflow.core.config import list_groups, model_dir


def run_submit_run(cfg: dict):
    pid      = cfg["project_id"]
    mdir     = model_dir(cfg)
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")

    print(f"\n{'='*60}")
    print(f"  submit_run for M{pid} (UFS-SCHISM)")
    print(f"  Grouping  : {grouping}"
          + (f"  (group_ndays={cfg['group_ndays']})"
             if grouping == "ndays" else ""))
    print(f"  {len(groups)} group(s): "
          f"{groups[0]} -> {groups[-1]}")
    print(f"  chain_hotstart: "
          f"{bool(cfg.get('chain_hotstart', True))}")
    print(f"  NOTE: this is BLOCKING. "
          f"Run inside screen/tmux.")
    print(f"{'='*60}")

    launched_any = False

    for group_id in groups:
        rdir = mdir / f"R{pid}" / f"R{pid}_{group_id}"

        if (rdir / "run.done").exists():
            print(f"  {group_id}: run.done present, "
                  f"skipping.")
            continue

        if not (rdir / "setup_run.done").exists():
            print(f"  ERROR {group_id}: setup_run not done "
                  f"for {rdir}.")
            print("    Run:  stofs-ak --run --phase run "
                  "--only setup_run --config <cfg>")
            sys.exit(1)

        auto = rdir / "auto_hotstart.py"
        if not auto.exists():
            print(f"  ERROR {group_id}: {auto} not found "
                  f"(re-run setup_run).")
            sys.exit(1)

        print(f"\n{'='*60}")
        print(f"  Launching auto_hotstart.py for {group_id}")
        print(f"  Run dir: {rdir}")
        print(f"  Screen log: {rdir}/scrn.out")
        print(f"{'='*60}\n")

        launched_any = True

        result = subprocess.run(
            [sys.executable, str(auto)],
            cwd=str(rdir))

        if result.returncode != 0:
            print(f"\n  ERROR: auto_hotstart.py failed for "
                  f"{group_id} "
                  f"(exit {result.returncode}).")
            print(f"  Inspect {rdir}/outputs/mirror.out "
                  f"and re-run submit_run to resume.")
            sys.exit(result.returncode)

    print(f"\n{'='*60}")
    if launched_any:
        print("  submit_run complete. "
              "All launched groups finished.")
    else:
        print("  All groups already complete "
              "(run.done present). Nothing to do.")
    print(f"{'='*60}\n")
