"""
submit_hycom_utils.py
=====================
Steps B, C, D — Submit SLURM jobs for SCHISM Fortran preprocessing
utilities that consume the aggregated HYCOM monthly stacks.

  Step B: gen_hotstart  — single SLURM job, first month only
              gen_hot_from_hycom_0_noscaling.exe
              output: hotstart.nc

  Step C: gen_3Dth      — SLURM job array, one task per month
              gen_3Dth_from_hycom_noscaling.exe
              output: elev2D.th.nc, uv3D.th.nc, TEM_3D.th.nc, SAL_3D.th.nc

  Step D: gen_nudge     — SLURM job array, one task per month
              gen_nudge_from_hycom_noscaling.exe
              output: TEM_nu.nc, SAL_nu.nc

Each step:
  - Checks sentinel files (*.done) to skip already-completed months
  - Symlinks mesh files (fix/) and .in/executable (bin/) into I{ID}_YYYYMM/
    at job runtime via the SLURM script
  - Writes a sentinel file on successful completion for resume safety
"""

import shutil
import subprocess
import sys
from pathlib import Path

from workflow.config import list_months, model_dir

REPO_ROOT = Path(__file__).resolve().parent.parent
REMINDERS = """
  REMINDER: _noscaling executables expect UNPACKED float data (ncpdq -U done
            at download). DO NOT use stock SCHISM executables.
  REMINDER: lon=lon-360 is COMMENTED OUT -- mesh and HYCOM both on 0-360.
"""


def _render_template(template_path: Path, subs: dict) -> str:
    text = template_path.read_text()
    for key, val in subs.items():
        text = text.replace("{{" + key + "}}", str(val))
    return text


def _sbatch(script_path: Path) -> str:
    """Submit a SLURM script and return the job ID string."""
    result = subprocess.run(["sbatch", str(script_path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: sbatch failed: {result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip()


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
# Step B — gen_hotstart (single job, first month only)
# =============================================================================

def submit_gen_hotstart(cfg: dict, config_dir: Path):
    if shutil.which("sbatch") is None:
        print("ERROR: sbatch not found. Run from a login node.")
        sys.exit(1)

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

    template     = REPO_ROOT / "templates" / "slurm" / "gen_hotstart.sbatch"
    rendered_path = logdir / "gen_hotstart.sbatch"
    rendered_path.write_text(_render_template(template, subs))

    print(f"  Submitting gen_hotstart for month {first} ...")
    print(f"  Rendered script: {rendered_path}")
    out = _sbatch(rendered_path)
    print(f"  {out}")
    print(f"  Log: {logdir}/gen_hotstart.out")


# =============================================================================
# Step C — gen_3Dth (array, every month)
# =============================================================================

def submit_gen_3Dth(cfg: dict, config_dir: Path):
    if shutil.which("sbatch") is None:
        print("ERROR: sbatch not found. Run from a login node.")
        sys.exit(1)

    print(REMINDERS)

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)

    _check_executables(cfg, mdir, ["gen_3Dth"])
    _check_in_files(mdir / "bin", ["gen_3Dth_from_nc.in"])
    exe = cfg["executables"]["gen_3Dth"]

    logdir   = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    manifest = logdir / "gen_3Dth_months.manifest"

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

    manifest.write_text("\n".join(pending) + "\n")
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

    template      = REPO_ROOT / "templates" / "slurm" / "gen_3Dth.sbatch"
    rendered_path = logdir / "gen_3Dth.sbatch"
    rendered_path.write_text(_render_template(template, subs))

    print(f"  Submitting gen_3Dth array: {nmonths} month(s) "
          f"({pending[0]} -> {pending[-1]})")
    print(f"  Rendered script: {rendered_path}")
    out = _sbatch(rendered_path)
    print(f"  {out}")
    print(f"  Monitor: squeue -u $USER")
    print(f"  Logs: {logdir}/gen_3Dth_*.out")


# =============================================================================
# Step D — gen_nudge (array, every month)
# =============================================================================

def submit_gen_nudge(cfg: dict, config_dir: Path):
    if shutil.which("sbatch") is None:
        print("ERROR: sbatch not found. Run from a login node.")
        sys.exit(1)

    print(REMINDERS)

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)

    _check_executables(cfg, mdir, ["gen_nudge"])
    _check_in_files(mdir / "bin", ["gen_nudge_from_nc.in"])
    exe = cfg["executables"]["gen_nudge"]

    logdir   = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    manifest = logdir / "gen_nudge_months.manifest"

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

    manifest.write_text("\n".join(pending) + "\n")
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

    template      = REPO_ROOT / "templates" / "slurm" / "gen_nudge.sbatch"
    rendered_path = logdir / "gen_nudge.sbatch"
    rendered_path.write_text(_render_template(template, subs))

    print(f"  Submitting gen_nudge array: {nmonths} month(s) "
          f"({pending[0]} -> {pending[-1]})")
    print(f"  Rendered script: {rendered_path}")
    out = _sbatch(rendered_path)
    print(f"  {out}")
    print(f"  Monitor: squeue -u $USER")
    print(f"  Logs: {logdir}/gen_nudge_*.out")
