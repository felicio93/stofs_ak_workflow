"""
models/schism_mice/preprocess/gen_mice_nml.py
=============================================
Phase 3 (interactive) — Copy fix/mice.nml into each I{ID}_{group_id}/
directory with ihot_mice set for that group.

    first group      : ihot_mice kept from fix/mice.nml
                       0 = ice-free start, 2 = ice from HYCOM (step
                       gen_ice_hotstart adds sic2/sih2/siu2/siv2 to the
                       first group's hotstart.nc)
    every later group: ihot_mice = 1 (restart from the Icepack state in the
                       hotstart.nc that the previous group wrote)

Everything else is copied unchanged. fix/namelist.icepack is the same for
every group and is linked by setup_run from fix/.

Resume-safe: groups whose mice.nml already exists are skipped.
"""

import sys

from workflow.core.config import list_groups, model_dir
from workflow.models.schism_mice import mice_nml


def run_gen_mice_nml(cfg: dict):
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    template = mdir / "fix" / "mice.nml"

    print(f"\n{'='*60}")
    print(f"  gen_mice_nml for M{pid}")
    print(f"{'='*60}")
    if not template.exists():
        sys.exit(f"ERROR (gen_mice_nml): {template} not found. Start from "
                 f"templates/schism_mice/fix/mice.nml.")
    probs = mice_nml.check(template)
    if probs:
        for p in probs:
            print(f"  ERROR: fix/{p}")
        sys.exit(1)

    text  = template.read_text()
    first = mice_nml.ihot_mice(text)
    if first == 1:
        print("  WARNING: ihot_mice = 1 in fix/mice.nml: the first group then "
              "needs a hotstart.nc with the Icepack state of an earlier MICE "
              "run; a hotstart from gen_hotstart has none and SCHISM stops "
              "('mice_init: nc aicen1'). Use 0 or 2.")

    groups = list_groups(cfg)
    print(f"  First group ({groups[0]}): ihot_mice = {first} "
          f"({mice_nml.IHOT_MICE_MEANING[first]})")
    if len(groups) > 1:
        print(f"  Later groups ({groups[1]} -> {groups[-1]}): ihot_mice = 1")

    failed = []
    for i, group_id in enumerate(groups):
        idir = mdir / f"I{pid}" / f"I{pid}_{group_id}"
        out  = idir / "mice.nml"
        if out.exists() and out.stat().st_size > 0:
            print(f"  {group_id}: mice.nml already exists, skipping.")
            continue
        if not idir.exists():
            print(f"  ERROR {group_id}: {idir} does not exist. "
                  f"Run --init first.")
            failed.append(group_id)
            continue
        value = first if i == 0 else 1
        out.write_text(mice_nml.set_value(text, "ihot_mice", value))
        print(f"  {group_id}: mice.nml written (ihot_mice = {value})")

    print(f"\n{'='*60}")
    if failed:
        print(f"  gen_mice_nml complete with {len(failed)} failure(s): "
              f"{', '.join(failed)}")
    else:
        print("  gen_mice_nml complete. No failures.")
    print(f"{'='*60}\n")
    return not failed
