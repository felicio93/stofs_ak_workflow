"""
models/schism/run/submit_run.py
===============================
Phase 4, step "submit_run".

When chain_hotstart=True, launches ONLY the first pending group.
auto_hotstart.py chains subsequent groups automatically.
When chain_hotstart=False (single-group testing), also only the first
pending group is launched: later groups have no hotstart.nc link until it is
put in place by hand; the next submit_run then launches the next group.
"""

import subprocess
import sys

from workflow.core.config import list_groups, model_dir
from workflow.core.templating import unreplaced


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

    # Only the first pending group: with chain_hotstart=True auto_hotstart.py
    # launches the next ones; with False the next group has no hotstart.nc
    # until it is put in place by hand, so each submit_run runs one group.
    launch_groups = pending[:1]
    if chain:
        print(f"\n  chain_hotstart=True: launching only "
              f"group {launch_groups[0]}. "
              f"Remaining {len(pending)-1} group(s) will be "
              f"chained automatically by auto_hotstart.py.")
    else:
        print(f"\n  chain_hotstart=False: launching only group "
              f"{launch_groups[0]} ({len(pending)-1} more pending group(s) "
              f"are not started: put each one's hotstart in place, then "
              f"run submit_run again).")

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

        # Guard: confirm auto_hotstart.py is rendered
        content = auto.read_text()
        if unreplaced(content):
            print(f"  ERROR {group_id}: auto_hotstart.py "
                  f"contains unrendered placeholders.")
            print(f"    Delete {rdir}/setup_run.done and "
                  f"re-run setup_run.")
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
