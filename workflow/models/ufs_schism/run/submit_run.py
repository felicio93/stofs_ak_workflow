import subprocess
import sys
from pathlib import Path

from workflow.core.config import list_months, model_dir

def run_submit_run(cfg: dict):
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)

    print(f"
{'='*60}")
    print(f"  submit_run for M{pid}")
    print(f"  {len(months)} month(s): {months[0]} -> {months[-1]}")
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

        print(f"
{'='*60}")
        print(f"  Launching auto_hotstart.py for {ym}")
        print(f"  Run dir: {rdir}")
        print(f"  Screen log: {rdir}/scrn.out (auto_hotstart prints to stdout too)")
        print(f"{'='*60}
")

        launched_any = True
        result = subprocess.run([sys.executable, str(auto)], cwd=str(rdir))
        if result.returncode != 0:
            print(f"
  ERROR: auto_hotstart.py failed for {ym} "
                  f"(exit {result.returncode}).")
            print(f"  Inspect {rdir}/outputs/mirror.out and re-run submit_run "
                  f"to resume.")
            sys.exit(result.returncode)

    print(f"
{'='*60}")
    if launched_any:
        print("  submit_run complete. All launched months finished.")
    else:
        print("  All months already complete (run.done present). Nothing to do.")
    print(f"{'='*60}
")
