"""
models/schism/preprocess/gen_hycom_utils.py
===========================================
Steps F, G, H — Submit SLURM jobs for SCHISM Fortran preprocessing
utilities that consume the aggregated HYCOM monthly stacks.

  Step F: gen_hotstart  — single SLURM job, first month only
              gen_hot_from_hycom_0_noscaling.exe
              output: hotstart.nc

  Step G: gen_3Dth      — SLURM job array, one task per month
              gen_3Dth_from_hycom_noscaling.exe
              output: elev2D.th.nc, uv3D.th.nc, TEM_3D.th.nc, SAL_3D.th.nc

  Step H: gen_nudge     — SLURM job array, one task per month
              gen_nudge_from_hycom_noscaling.exe
              output: TEM_nu.nc, SAL_nu.nc

Each step:
  - Checks sentinel files (*.done) to skip already-completed months
  - Symlinks mesh files (fix/) and .in/executable (bin/) into I{ID}_YYYYMM/
    at job runtime via the SLURM script
  - Writes a sentinel file on successful completion for resume safety

Rendering/submission goes through workflow.core.slurm.SlurmSubmitter, which
also performs the `sbatch` availability check.
"""

import sys
from pathlib import Path

from workflow.core.config import list_months, model_dir
from workflow.core.slurm import SlurmSubmitter, write_manifest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slurm"
REMINDERS = """
  REMINDER: _noscaling executables expect UNPACKED float data (ncpdq -U done
            at download). DO NOT use stock SCHISM executables.
  REMINDER: lon=lon-360 is COMMENTED OUT -- mesh and HYCOM both on 0-360.
"""


def _common_subs(cfg: dict, mdir: Path) -> dict:
    slurm = cfg.get("slurm", {})
    pid   = cfg["project_id"]
    return {
        "ACCOUNT":           slurm.get("account",           "nos-surge"),
        "PARTITION":         slurm.get("partition",         "hercules-2"),
        "MEM":               slurm.get("schism_mem",        "8G"),
        "WALLTIME":          slurm.get("schism_walltime",   "02:00:00"),
        "HOTSTART_MEM":      slurm.get("hotstart_mem",      "64G"),
        "HOTSTART_WALLTIME": slurm.get("hotstart_walltime", "04:00:00"),
        "MAILUSER":          slurm.get("mail_user",         "felicio.cassalho@noaa.gov"),
        "FIXDIR":            str(mdir / "fix"),
        "BINDIR":            str(mdir / "bin"),
        "PID":               pid,
        "IBASEDIR":          str(mdir / f"I{pid}"),
    }


def _check_executables(cfg: dict, mdir: Path, keys: list):
    """Verify required executables exist in bin/ before submitting."""
    exes = cfg.get("executables", {})
    missing = []
    for key in keys:
        name = exes.get(key)
        if not name:
            print(f"  ERROR: executables.{key} not set in project.yaml")
            sys.exit(1)
        if not (mdir / "bin" / name).exists():
            missing.append(name)
    if missing:
        print(f"  ERROR: executables not found in M*/bin/:")
        for m in missing:
            print(f"    {m}")
        print("  Copy the compiled _noscaling executables into bin/ first.")
        sys.exit(1)


def _check_in_files(bin_dir: Path, names: list):
    """Verify required .in files exist in bin/."""
    missing = [n for n in names if not (bin_dir / n).exists()]
    if missing:
        print(f"  ERROR: .in files not found in bin/:")
        for m in missing:
            print(f"    {m}")
        print("  Run gen_estuary first (step gen_estuary in steps.yaml).")
        sys.exit(1)


# =============================================================================
# Step F — gen_hotstart (single job, first month only)
# =============================================================================

def submit_gen_hotstart(cfg: dict, config_dir: Path):
    print(REMINDERS)

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)
    first  = months[0]

    _check_executables(cfg, mdir, ["gen_hotstart"])
    _check_in_files(mdir / "bin", ["gen_hot_from_nc.in"])
    exe = cfg["executables"]["gen_hotstart"]

    idir   = mdir / f"I{pid}" / f"I{pid}_{first}"
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    # Skip if already completed.
    if (idir / "gen_hotstart.done").exists():
        print(f"  gen_hotstart already complete (sentinel found in "
              f"I{pid}_{first}). Skipping.")
        return

    subs = _common_subs(cfg, mdir)
    subs.update({
        "JOBNAME":      f"hotstart_M{pid}",
        "IDIR":         str(idir),
        "LOGDIR":       str(logdir),
        "MONTH":        first,
        "HOTSTART_EXE": exe,
    })

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_hotstart for month {first} ...")
    submitter.render_and_submit("gen_hotstart.sbatch", subs,
                                logdir / "gen_hotstart.sbatch")
    print(f"  Log: {logdir}/gen_hotstart.out")


# =============================================================================
# Step G — gen_3Dth (array, every month)
# =============================================================================

def submit_gen_3Dth(cfg: dict, config_dir: Path):
    print(REMINDERS)

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)

    _check_executables(cfg, mdir, ["gen_3Dth"])
    _check_in_files(mdir / "bin", ["gen_3Dth_from_nc.in"])
    exe = cfg["executables"]["gen_3Dth"]

    logdir   = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    # Filter out months already completed.
    pending = []
    for ym in months:
        idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
        if (idir / "gen_3Dth.done").exists():
            print(f"  {ym}: gen_3Dth already complete, skipping.")
        else:
            pending.append(ym)

    if not pending:
        print("  All months already complete. Nothing to submit.")
        return

    manifest = write_manifest(pending, logdir / "gen_3Dth_months.manifest")
    nmonths = len(pending)

    subs = _common_subs(cfg, mdir)
    subs.update({
        "JOBNAME":     f"gen3Dth_M{pid}",
        "WORKDIR":     str(mdir),
        "NMONTHS":     str(nmonths),
        "LOGDIR":      str(logdir),
        "MANIFEST":    str(manifest),
        "GEN3DTH_EXE": exe,
    })

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_3Dth array: {nmonths} month(s) "
          f"({pending[0]} -> {pending[-1]})")
    submitter.render_and_submit("gen_3Dth.sbatch", subs,
                                logdir / "gen_3Dth.sbatch")
    print(f"  Monitor: squeue -u $USER")
    print(f"  Logs: {logdir}/gen_3Dth_*.out")


# =============================================================================
# Step H — gen_nudge (array, every month)
# =============================================================================

def submit_gen_nudge(cfg: dict, config_dir: Path):
    print(REMINDERS)

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)

    _check_executables(cfg, mdir, ["gen_nudge"])
    _check_in_files(mdir / "bin", ["gen_nudge_from_nc.in"])
    exe = cfg["executables"]["gen_nudge"]

    logdir   = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    # Filter out months already completed.
    pending = []
    for ym in months:
        idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
        if (idir / "gen_nudge.done").exists():
            print(f"  {ym}: gen_nudge already complete, skipping.")
        else:
            pending.append(ym)

    if not pending:
        print("  All months already complete. Nothing to submit.")
        return

    manifest = write_manifest(pending, logdir / "gen_nudge_months.manifest")
    nmonths = len(pending)

    subs = _common_subs(cfg, mdir)
    subs.update({
        "JOBNAME":      f"gennudge_M{pid}",
        "WORKDIR":      str(mdir),
        "NMONTHS":      str(nmonths),
        "LOGDIR":       str(logdir),
        "MANIFEST":     str(manifest),
        "GENNUDGE_EXE": exe,
    })

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_nudge array: {nmonths} month(s) "
          f"({pending[0]} -> {pending[-1]})")
    submitter.render_and_submit("gen_nudge.sbatch", subs,
                                logdir / "gen_nudge.sbatch")
    print(f"  Monitor: squeue -u $USER")
    print(f"  Logs: {logdir}/gen_nudge_*.out")
