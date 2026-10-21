"""
Build targets of build.yaml.

    python -m workflow.build --config <config_dir> --target schism [--dry-run]
    python -m workflow.build --config <config_dir> --target all

Run on a login node (the sources are cloned/fetched there; compilation is
submitted as SLURM jobs). --dry-run only renders the job script.
"""

import argparse
from pathlib import Path

from workflow.build.builder import build_target
from workflow.build.recipe import check_build
from workflow.core.config import load_config


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--target", required=True,
                    help="target name from build.targets, or 'all'")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    check_build(cfg)
    targets = cfg["build"]["targets"]
    names = list(targets) if args.target == "all" else [args.target]
    for n in names:
        if n not in targets:
            raise SystemExit(f"ERROR: unknown target {n!r}; "
                             f"choose from {sorted(targets)}")
    for n in names:
        try:
            jid = build_target(cfg, Path(args.config), n,
                               dry_run=args.dry_run)
        except NotImplementedError as exc:
            print(f"  [N/A] {n}: {exc}")
            continue
        if jid:
            print(f"  {n}: job {jid}")


if __name__ == "__main__":
    main()
