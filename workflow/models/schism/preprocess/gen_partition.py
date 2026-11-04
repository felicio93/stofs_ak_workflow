"""
models/schism/preprocess/gen_partition.py
=========================================
Phase 0 (interactive) — make fix/partition.prop for the core count of
fix/run_test.

The compute core count is read from fix/run_test: #SBATCH -n minus the scribe
cores (last argument of the srun line), so the job card is the one place to
set it. bin/partition_offline.pl (build target partition_tools: metis_prep +
gpmetis) runs in M{ID}/partition_work/ on fix/hgrid.gr3 and fix/vgrid.in
(metis_prep weights the elements by their number of levels, so the partition
belongs to one vgrid.in) and the result is copied to fix/partition.prop.

An existing fix/partition.prop that already matches the mesh and the core
count is kept. One that does not match is renamed to
partition.prop.replaced_<timestamp> (never deleted) before the new one is
copied in. partition_work/ is kept (graphinfo, partition.prop.<n>).
"""

import shutil
import subprocess
import sys
from datetime import datetime

from workflow.core.config import model_dir
from workflow.core.partition import check_partition, compute_cores

PARTITION_TOOLS = ("partition_offline.pl", "metis_prep", "gpmetis")


def _link(src, dst):
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    dst.symlink_to(src)


def run_gen_partition(cfg: dict):
    mdir = model_dir(cfg)
    fix  = mdir / "fix"
    bind = mdir / "bin"

    print(f"\n{'='*60}")
    print(f"  gen_partition for M{cfg['project_id']}")
    print(f"{'='*60}")

    missing = [str(p) for p in (fix / "run_test", fix / "hgrid.gr3",
                                fix / "vgrid.in")
               if not p.exists()]
    missing += [str(bind / n) for n in PARTITION_TOOLS
                if not (bind / n).exists()]
    if missing:
        print("  ERROR: required file(s) not found:")
        for m in missing:
            print(f"    {m}")
        print("  (metis_prep, gpmetis and partition_offline.pl come from the "
              "build target partition_tools)")
        sys.exit(1)

    cores = compute_cores(fix / "run_test")
    if cores is None:
        print("  ERROR: could not read the core count from fix/run_test: "
              "need an '#SBATCH -n <N>' line and the scribe count as the "
              "last argument of the srun line.")
        sys.exit(1)
    ntasks, nscribes, ncompute = cores
    print(f"  run_test: -n {ntasks}, {nscribes} scribe(s) -> "
          f"{ncompute} compute cores")
    if ncompute < 1:
        print("  ERROR: no compute cores left after the scribes.")
        sys.exit(1)

    prop = fix / "partition.prop"
    if prop.exists():
        errors = check_partition(fix, ncompute)
        if not errors:
            print(f"  fix/partition.prop already matches hgrid.gr3 and "
                  f"{ncompute} cores, kept.")
            return
        for e in errors:
            print(f"  {e}")

    work = mdir / "partition_work"
    work.mkdir(exist_ok=True)
    for name in ("hgrid.gr3", "vgrid.in"):
        _link(fix / name, work / name)
    print(f"  Running partition_offline.pl {ncompute} in {work} ...")
    r = subprocess.run(["perl", str(bind / "partition_offline.pl"),
                        str(ncompute)], cwd=work)
    out = work / f"partition.prop.{ncompute}"
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        print(f"  ERROR: partition_offline.pl failed (exit {r.returncode}); "
              f"{out.name} not written. See the output above.")
        sys.exit(1)

    if prop.exists():
        old = fix / f"partition.prop.replaced_{datetime.now():%Y%m%dT%H%M%S}"
        prop.rename(old)
        print(f"  Previous fix/partition.prop renamed to {old.name}")
    shutil.copy2(out, prop)

    errors = check_partition(fix, ncompute)
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        sys.exit(1)
    print(f"  Written: {prop}  ({ncompute} parts)")
    print("  Keep run_test -n / -N and the scribe count as they are, or "
          "re-run gen_partition after changing them.")
