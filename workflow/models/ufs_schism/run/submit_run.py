"""
models/ufs_schism/run/submit_run.py
===================================

Phase 4, step "submit_run".

Launches auto_hotstart.py for the UFS-SCHISM monthly runs.

Behavior:

  * blocking execution
  * month-by-month processing
  * run.done resume behavior
  * setup_run.done validation
  * auto_hotstart.py validation
  * auto_hotstart.py handles hotstart chaining

When chain_hotstart=true, auto_hotstart.py may launch the next month itself.

When chain_hotstart=false, submit_run launches each incomplete month
explicitly, one after another.

This step is BLOCKING and should normally be run inside screen/tmux.

Example:

    screen -S stofs_run

    stofs-ak --run --phase run --only submit_run --config <cfg>

Detach:

    Ctrl-A D

Reattach:

    screen -r stofs_run
"""

import subprocess
import sys
from pathlib import Path

from workflow.core.config import list_months, model_dir


def run_submit_run(cfg: dict):
    """
    Launch the UFS-SCHISM monthly run chain.

    Months with run.done are skipped, making the operation resume-safe.
    """

    pid = cfg["project_id"]

    mdir = model_dir(cfg)

    months = list_months(cfg)

    if not months:
        print(
            "ERROR: no months configured."
        )
        sys.exit(1)

    print(
        f"\n{'=' * 60}"
    )

    print(
        f"  submit_run for M{pid}"
    )

    print(
        f"  {len(months)} month(s): "
        f"{months[0]} -> {months[-1]}"
    )

    print(
        f"  chain_hotstart: "
        f"{bool(cfg.get('chain_hotstart', True))}"
    )

    print(
        "  NOTE: this is BLOCKING. "
        "Run inside screen/tmux."
    )

    print(
        f"{'=' * 60}"
    )

    launched_any = False

    for ym in months:

        rdir = (
            mdir
            / f"R{pid}"
            / f"R{pid}_{ym}"
        )

        print()

        # --------------------------------------------------------------
        # Already complete
        # --------------------------------------------------------------

        if (
            rdir / "run.done"
        ).exists():

            print(
                f"  {ym}: run.done present, skipping."
            )

            continue

        # --------------------------------------------------------------
        # setup_run validation
        # --------------------------------------------------------------

        if not (
            rdir / "setup_run.done"
        ).exists():

            print(
                f"  ERROR {ym}: setup_run not done "
                f"for {rdir}."
            )

            print(
                "    Run:"
            )

            print(
                "      stofs-ak --run --phase run "
                "--only setup_run --config <cfg>"
            )

            sys.exit(1)

        # --------------------------------------------------------------
        # auto_hotstart validation
        # --------------------------------------------------------------

        auto = (
            rdir / "auto_hotstart.py"
        )

        if not auto.is_file():

            print(
                f"  ERROR {ym}: {auto} not found."
            )

            print(
                "    Re-run setup_run."
            )

            sys.exit(1)

        # --------------------------------------------------------------
        # Launch
        # --------------------------------------------------------------

        print(
            f"\n{'=' * 60}"
        )

        print(
            f"  Launching auto_hotstart.py for {ym}"
        )

        print(
            f"  Run dir: {rdir}"
        )

        print(
            f"  Screen log: {rdir}/scrn.out "
            "(auto_hotstart prints to stdout too)"
        )

        print(
            f"{'=' * 60}\n"
        )

        launched_any = True

        # Use the current Python interpreter/environment.
        #
        # auto_hotstart.py is intentionally executed with Python rather
        # than relying on its executable bit or shebang.
        result = subprocess.run(
            [
                sys.executable,
                str(auto),
            ],
            cwd=str(rdir),
        )

        # --------------------------------------------------------------
        # Failure
        # --------------------------------------------------------------

        if result.returncode != 0:

            print(
                f"\n  ERROR: auto_hotstart.py failed "
                f"for {ym} "
                f"(exit {result.returncode})."
            )

            print(
                f"  Inspect:"
            )

            print(
                f"    {rdir}/outputs/mirror.out"
            )

            print(
                "  Then fix the problem and re-run submit_run "
                "to resume."
            )

            sys.exit(
                result.returncode
            )

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------

    print(
        f"\n{'=' * 60}"
    )

    if launched_any:

        print(
            "  submit_run complete. "
            "All launched months finished."
        )

    else:

        print(
            "  All months already complete "
            "(run.done present). Nothing to do."
        )

    print(
        f"{'=' * 60}\n"
    )


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Submit UFS-SCHISM monthly runs."
        )
    )

    parser.add_argument(
        "--config",
        required=True,
        help="Path to the config directory.",
    )

    args = parser.parse_args()

    config_dir = Path(
        args.config
    )

    config = __import__(
        "workflow.core.config",
        fromlist=["load_config"],
    ).load_config(
        config_dir
    )

    run_submit_run(
        config
    )

