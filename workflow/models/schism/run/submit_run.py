"""
models/schism/run/submit_run.py
===============================
Phase 4, step "submit_run".

When chain_hotstart=True, launches ONLY the first pending group.
auto_hotstart.py chains to subsequent groups automatically on completion.
Launching more than one group here would double-launch every group
after the first.

When chain_hotstart=False, launches each pending group one at a time
and waits for each to finish before starting the next.
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
    chain    = bool(cfg.get("chain_hotstart", True))

    print(f"\n{'='*60}")
    print(f"  submit_run for M{pid}")
    print(f"  Grouping  : {grouping}"
          + (f"  (group_ndays={cfg['group_ndays']})"
             if grouping == "ndays" else ""))
    print(f"  {len(groups)} group(s): "
          f"{groups[0]} -> {groups[-1]}")
    print(f"  chain_hotstart: {chain}")
    print(f"  NOTE: this is BLOCKING. Run inside screen/tmux.")
    print(f"{'='*60}")

    # Identify pending groups
    pending = []
    for group_id in groups:
        rdir = mdir / f"R{pid}" / f"R{pid}_{group_id}"
        if (rdir / "run.done").exists():
            print(f"  {group_id}: run.done present, skipping.")
        else:
            pending.append(group_id)

    if not pending:
        print(f"\n{'='*60}")
        print("  All groups already complete. Nothing to do.")
        print(f"{'='*60}\n")
        return

    if chain:
        # Launch only the first pending group.
        # auto_hotstart.py chains all subsequent groups itself.
        launch_groups = pending[:1]
        print(f"\n  chain_hotstart=True: launching only "
              f"group {launch_groups[0]}. "
              f"Remaining {len(pending)-1} group(s) will be "
              f"chained automatically.")
    else:
        # No chaining — launch each group independently.
        launch_groups = pending
        print(f"\n  chain_hotstart=False: launching "
              f"{len(launch_groups)} group(s) independently.")

    for group_id in launch_groups:
        rdir = mdir / f"R{pid}" / f"R{pid}_{group_id}"

        if not (rdir / "setup_run.done").exists():
            print(f"  ERROR {group_id}: setup_run not done.")
            print("    Run:  stofs-ak --run --phase run "
                  "--only setup_run --config <cfg>")
            sys.exit(1)

        auto = rdir / "auto_hotstart.py"
        if not auto.exists():
            print(f"  ERROR {group_id}: {auto} not found.")
            sys.exit(1)

        # Sanity check: confirm auto_hotstart.py is rendered
        # (no unreplaced {{}} placeholders at module level)
        content = auto.read_text()
        if "{{RUNDIR}}" in content or "{{NEXT_RUNDIR}}" in content:
            print(f"  ERROR {group_id}: {auto} still contains "
                  f"unrendered template placeholders.")
            print("    Re-run setup_run to fix it:")
            print("      rm {rdir}/setup_run.done")
            print("      stofs-ak --run --phase run "
                  "--only setup_run --config <cfg>")
            sys.exit(1)

        print(f"\n{'='*60}")
        print(f"  Launching auto_hotstart.py for {group_id}")
        print(f"  Run dir: {rdir}")
        print(f"{'='*60}\n")

        result = subprocess.run(
            [sys.executable, str(auto)],
            cwd=str(rdir))

        if result.returncode != 0:
            print(f"\n  ERROR: auto_hotstart.py failed for "
                  f"{group_id} (exit {result.returncode}).")
            print(f"  Inspect {rdir}/outputs/mirror.out and "
                  f"re-run submit_run to resume.")
            sys.exit(result.returncode)

    print(f"\n{'='*60}")
    print("  submit_run complete.")
    print(f"{'='*60}\n")
