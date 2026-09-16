#!/usr/bin/env python3
"""
auto_hotstart.py  (rendered per run directory by setup_run.py)
==============================================================
Self-contained SCHISM monthly run manager for ONE run directory.

Behaviour (ihot=1, single end-of-month hotstart):
  1. Submit run_test via sbatch.
  2. Poll squeue, watching mirror.out for advancement and hang detection.
  3. On successful completion:
       - (UFS-SCHISM) combine ALL output stacks at end of month
       - submit run_comb (combine_hotstart7) and wait
       - delete per-rank hotstart files
       - symlink combined hotstart into next month's run directory
       - launch next month's auto_hotstart.py (if chain_hotstart=True)
       - write run.done sentinel
"""

import os
import re
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path


# ============================ CONFIG (filled by setup_run) ====================
RUNDIR         = r"{{RUNDIR}}"
NEXT_RUNDIR    = {{NEXT_RUNDIR}}
CHAIN_HOTSTART = {{CHAIN_HOTSTART}}
IS_LAST_MONTH  = {{IS_LAST_MONTH}}
NHOT_WRITE     = {{NHOT_WRITE}}
MONTH          = r"{{MONTH}}"
RUN_JOBNAME    = r"{{RUN_JOBNAME}}"

# --- New I/O diagnostic plots (SCHISM standalone) ---
DIAG_ENABLED        = {{DIAG_ENABLED}}
DIAG_SBATCH         = r"{{DIAG_SBATCH}}"
DIAG_VARS_MANIFEST  = r"{{DIAG_VARS_MANIFEST}}"
DIAG_NVAR           = {{DIAG_NVAR}}

# --- End-of-month output combination (UFS-SCHISM old I/O) ---
# Per-stack combine during run is DISABLED. All stacks are combined
# in one batch job at the end of the month after the run completes.
COMBINE_OUTPUT_ENABLED  = {{COMBINE_OUTPUT_ENABLED}}
COMBINE_OUTPUT_EXE      = r"{{COMBINE_OUTPUT_EXE}}"
COMBINE_OUTPUT_NRANKS   = {{COMBINE_OUTPUT_NRANKS}}
COMBINE_OUTPUT_SBATCH   = r"{{COMBINE_OUTPUT_SBATCH}}"
# =============================================================================

_POLL_SCHEDULE = [0, 60, 60, 60, 60, 60, 300, 1200, 1800]
_POLL_STEADY   = 1800
_COMBINE_POLL_SECONDS = 60
_MAX_RESUBMITS = 5

QUEUE_CMD = f"squeue -u {os.environ.get('USER', os.environ.get('LOGNAME', ''))}"


def _poll_sleep(poll_iter: int):
    secs = _POLL_SCHEDULE[poll_iter] if poll_iter < len(_POLL_SCHEDULE) \
        else _POLL_STEADY
    if secs > 0:
        time.sleep(secs)


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, (r.stdout.strip() + "\n" + r.stderr.strip()).strip()


def squeue_line_for(jobname):
    _, out = sh(QUEUE_CMD)
    pat = re.compile(rf"\b{re.escape(jobname)}\b")
    for line in out.splitlines():
        if pat.search(line):
            return line.strip()
    return None


def _clean_outputs():
    """Delete stale output stacks and mirror.out before (re)submitting."""
    outdir = Path(RUNDIR) / "outputs"
    deleted = []
    # Delete per-rank partition files (*_NNNNNN_N.nc pattern)
    for f in sorted(outdir.glob("schout_??????_*.nc")):
        if re.match(r"^schout_\d{6}_\d+\.nc$", f.name):
            f.unlink(missing_ok=True)
            deleted.append(f.name)
    # Delete any already-combined schout_N.nc files from a previous attempt
    for f in sorted(outdir.glob("schout_*.nc")):
        if re.match(r"^schout_\d+\.nc$", f.name):
            f.unlink(missing_ok=True)
            deleted.append(f.name)
    # Delete combine sentinels
    for f in sorted(outdir.glob("combine_*.done")):
        f.unlink(missing_ok=True)
        deleted.append(f.name)
    mirror = outdir / "mirror.out"
    if mirror.exists():
        mirror.unlink(missing_ok=True)
        deleted.append("mirror.out")
    if deleted:
        log(f"Cleaned {len(deleted)} stale file(s) from outputs/ before submission.")
    else:
        log("outputs/ is clean (no stale stacks or mirror.out to remove).")


def submit(script):
    rc, out = sh(f"sbatch {script}")
    if rc != 0:
        log(f"ERROR: sbatch {script} failed:\n{out}")
        sys.exit(1)
    log(out)
    return out.split()[-1]


def mirror_time_step():
    mo = Path(RUNDIR) / "outputs" / "mirror.out"
    if not mo.exists():
        return None
    try:
        lines = mo.read_text(errors="ignore").splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        m = re.search(r"TIME STEP\s*=?\s*(\d+)", line)
        if m:
            return int(m.group(1))
    return None


def run_completed():
    mo = Path(RUNDIR) / "outputs" / "mirror.out"
    if not mo.exists():
        return False
    try:
        lines = mo.read_text(errors="ignore").splitlines()
    except Exception:
        return False
    for line in reversed(lines[-25:]):
        if "Run completed successfully" in line:
            return True
    return False


def job_exit_code(job_id: str) -> int:
    rc, out = sh(f"sacct -n -X -o ExitCode -j {job_id}")
    if rc != 0 or not out.strip():
        return -1
    for token in out.strip().splitlines():
        token = token.strip()
        if token:
            try:
                return int(token.split(":")[0])
            except ValueError:
                return -1
    return -1


def diagnose_failure(job_id: str) -> str:
    fe = Path(RUNDIR) / "outputs" / "fatal.error"
    if fe.exists():
        txt = fe.read_text(errors="ignore").strip()
        if txt:
            return f"SCHISM fatal.error:\n{txt}"
    err_log = Path(RUNDIR) / "err2.out"
    if err_log.exists():
        lines = err_log.read_text(errors="ignore").splitlines()
        diag = [l for l in lines
                if any(k in l for k in ("forrtl:", "ABORT", "srun: error",
                                        "severe", "error (78)"))]
        if diag:
            return f"Fortran/MPI error in err2.out:\n" + "\n".join(diag[:6])
    myout = Path(RUNDIR) / "myout"
    if myout.exists():
        for line in myout.read_text(errors="ignore").splitlines():
            if "ABORT" in line:
                return f"ABORT in myout: {line.strip()}"
    code = job_exit_code(job_id)
    if code > 0:
        return f"Job {job_id} exited with non-zero code {code}."
    return ""


# =============================================================================
# New I/O diagnostic dispatch (SCHISM standalone only)
# =============================================================================

_diag_submitted = set()


def dispatch_diag_plots(run_finished: bool = False):
    """Submit diag_run job arrays for newly-completed New I/O stacks.
    Only used for SCHISM standalone (New I/O). Not called for UFS-SCHISM.
    """
    if not DIAG_ENABLED or not DIAG_SBATCH:
        return
    outdir = Path(RUNDIR) / "outputs"
    stacks = sorted(
        int(p.stem.split("_")[1]) for p in outdir.glob("out2d_*.nc")
    )
    if not stacks:
        return
    max_stack = max(stacks)
    for n in stacks:
        if n in _diag_submitted:
            continue
        if not ((n < max_stack) or run_finished):
            continue
        rc, out = sh(
            f"sbatch --export=ALL,DIAG_MONTH={MONTH},DIAG_STACK={n} "
            f"{DIAG_SBATCH}"
        )
        if rc == 0:
            log(f"diag_run_plots: submitted stack {n} array 1-{DIAG_NVAR}  ({out})")
            _diag_submitted.add(n)
        else:
            log(f"diag_run_plots: sbatch FAILED for stack {n}: {out}")


# =============================================================================
# End-of-month output combination (UFS-SCHISM old I/O)
# All stacks are combined in one batch job after the run completes.
# =============================================================================

def _count_output_stacks() -> int:
    """Count total output stacks (combined + uncombined) in outputs/."""
    outdir = Path(RUNDIR) / "outputs"
    combined = {
        int(m.group(1))
        for f in outdir.glob("schout_*.nc")
        for m in [re.match(r"^schout_(\d+)\.nc$", f.name)]
        if m
    }
    rank0 = {
        int(m.group(1))
        for f in outdir.glob("schout_000000_*.nc")
        for m in [re.match(r"^schout_000000_(\d+)\.nc$", f.name)]
        if m
    }
    all_stacks = combined | rank0
    return max(all_stacks) if all_stacks else 0


def _clean_output_partition_files():
    """Delete ALL per-rank schout partition files and local_to_global_* files."""
    outdir = Path(RUNDIR) / "outputs"
    pat_schout = re.compile(r"^schout_\d{6}_\d+\.nc$")
    deleted_schout = 0
    freed_schout   = 0
    for f in sorted(outdir.glob("schout_??????_*.nc")):
        if pat_schout.match(f.name):
            try:
                freed_schout += f.stat().st_size
            except OSError:
                pass
            f.unlink(missing_ok=True)
            deleted_schout += 1

    deleted_l2g = 0
    freed_l2g   = 0
    for f in sorted(outdir.glob("local_to_global_*")):
        try:
            freed_l2g += f.stat().st_size
        except OSError:
            pass
        f.unlink(missing_ok=True)
        deleted_l2g += 1

    total = (freed_schout + freed_l2g) / 1e9
    if deleted_schout or deleted_l2g:
        log(f"End-of-month cleanup: deleted {deleted_schout} partition file(s) "
            f"and {deleted_l2g} local_to_global file(s) (~{total:.1f} GB freed).")


def combine_output_stacks():
    """Combine ALL output stacks at end of month using one SLURM job.

    Called once after the run completes successfully, before hotstart
    combination. All schout_NNNNNN_N.nc partition files are combined
    into schout_N.nc global files.
    """
    if not COMBINE_OUTPUT_ENABLED:
        return

    outdir   = Path(RUNDIR) / "outputs"
    sentinel = outdir / "combine_output.done"

    if sentinel.exists():
        log("combine_output: already complete (sentinel found). Skipping.")
        _clean_output_partition_files()
        return

    # Find all stacks that need combining
    rank0_stacks = {
        int(m.group(1))
        for f in outdir.glob("schout_000000_*.nc")
        for m in [re.match(r"^schout_000000_(\d+)\.nc$", f.name)]
        if m
    }

    if not rank0_stacks:
        log("combine_output: no partition files found to combine.")
        _clean_output_partition_files()
        sentinel.touch()
        return

    begin = min(rank0_stacks)
    end   = max(rank0_stacks)
    comb_jobname = "CO" + RUN_JOBNAME[1:]

    log(f"combine_output: combining {len(rank0_stacks)} stack(s) "
        f"(stacks {begin} to {end}) with {COMBINE_OUTPUT_NRANKS} MPI ranks ...")

    rc, out = sh(
        f"sbatch --export=ALL,"
        f"COMBINE_BEGIN={begin},COMBINE_END={end},"
        f"COMBINE_JOBNAME={comb_jobname} "
        f"{COMBINE_OUTPUT_SBATCH}"
    )
    if rc != 0:
        log(f"ERROR: sbatch {COMBINE_OUTPUT_SBATCH} failed:\n{out}")
        sys.exit(1)
    log(f"Submitted combine_output job: {out}")

    # Wait for combine job to finish
    while squeue_line_for(comb_jobname) is not None:
        log(f"  waiting for combine_output job ({comb_jobname}) ...")
        time.sleep(_COMBINE_POLL_SECONDS)
    time.sleep(10)

    # Verify combined files were created
    missing = [f"schout_{i}.nc" for i in rank0_stacks
               if not (outdir / f"schout_{i}.nc").exists()]
    if missing:
        log(f"ERROR: combine_output finished but {len(missing)} file(s) missing:")
        for m_name in missing[:10]:
            log(f"  {m_name}")
        sys.exit(1)

    log(f"combine_output: all {len(rank0_stacks)} stack(s) combined successfully.")
    sentinel.touch()
    _clean_output_partition_files()


# =============================================================================
# Hotstart management
# =============================================================================

def _clean_partition_hotstarts():
    """Delete per-rank hotstart files after combined hotstart is built."""
    outdir = Path(RUNDIR) / "outputs"
    pat    = re.compile(r"^hotstart_\d+_\d+\.nc$")
    deleted = freed = 0
    for f in sorted(outdir.glob("hotstart_*.nc")):
        if f.name.startswith("hotstart_it="):
            continue
        if pat.match(f.name):
            try:
                freed += f.stat().st_size
            except OSError:
                pass
            f.unlink(missing_ok=True)
            deleted += 1
    if deleted:
        log(f"Deleted {deleted} per-rank hotstart file(s) "
            f"(~{freed / 1e9:.1f} GB freed); kept the combined hotstart.")


def _partition_hotstarts_exist():
    outdir = Path(RUNDIR) / "outputs"
    pat    = re.compile(rf"^hotstart_\d+_{NHOT_WRITE}\.nc$")
    return any(pat.match(f.name)
               for f in outdir.glob(f"hotstart_*_{NHOT_WRITE}.nc"))


def combine_and_chain():
    """Submit run_comb, wait, verify combined hotstart, clean up, then chain."""
    combined = Path(RUNDIR) / "outputs" / f"hotstart_it={NHOT_WRITE}.nc"
    if not combined.exists():
        comb_jobname = "C" + RUN_JOBNAME[1:]
        log(f"Submitting run_comb to build {combined.name} ...")
        submit("run_comb")
        while squeue_line_for(comb_jobname) is not None:
            log("  waiting for combine job to finish ...")
            time.sleep(_COMBINE_POLL_SECONDS)
        time.sleep(10)
        if not combined.exists():
            log(f"ERROR: combine finished but {combined} was not created.")
            sys.exit(1)
    else:
        log(f"{combined.name} already exists; skipping combine.")

    log(f"End-of-month hotstart ready: {combined}")
    _clean_partition_hotstarts()

    if IS_LAST_MONTH:
        log("This is the last month; no chaining.")
    elif not CHAIN_HOTSTART:
        log("chain_hotstart=false; not launching the next month.")
    elif NEXT_RUNDIR is None:
        log("No next run directory configured; not chaining.")
    else:
        next_hot = Path(NEXT_RUNDIR) / "hotstart.nc"
        if next_hot.exists() or next_hot.is_symlink():
            next_hot.unlink()
        next_hot.symlink_to(combined)
        log(f"Symlinked next month's hotstart: {next_hot} -> {combined}")

    (Path(RUNDIR) / "run.done").touch()
    log(f"Wrote sentinel: {Path(RUNDIR) / 'run.done'}")

    if (not IS_LAST_MONTH) and CHAIN_HOTSTART and NEXT_RUNDIR is not None:
        next_script = Path(NEXT_RUNDIR) / "auto_hotstart.py"
        if not next_script.exists():
            log(f"ERROR: {next_script} not found; run setup_run for the next month.")
            sys.exit(1)
        log(f"Launching next month: {next_script}")
        r = subprocess.run([sys.executable, str(next_script)],
                           cwd=str(NEXT_RUNDIR))
        if r.returncode != 0:
            log(f"ERROR: next month ({NEXT_RUNDIR}) failed (exit {r.returncode}).")
            sys.exit(r.returncode)


# =============================================================================
# Main
# =============================================================================

def main():
    os.chdir(RUNDIR)
    log(f"=== auto_hotstart for {MONTH}  ({RUNDIR}) ===")
    log(f"run job name: {RUN_JOBNAME}   combine step: {NHOT_WRITE}")

    if (Path(RUNDIR) / "run.done").exists():
        log("run.done already present; nothing to do.")
        return

    if not (Path(RUNDIR) / "hotstart.nc").exists():
        log("ERROR: hotstart.nc not found in run directory.")
        sys.exit(1)

    combined = Path(RUNDIR) / "outputs" / f"hotstart_it={NHOT_WRITE}.nc"
    if run_completed() and (combined.exists() or _partition_hotstarts_exist()):
        log("Run already complete; proceeding to combine outputs and chain.")
        dispatch_diag_plots(run_finished=True)
        combine_output_stacks()
        combine_and_chain()
        log(f"=== auto_hotstart for {MONTH} done ===")
        return

    _clean_outputs()
    job_id        = submit("run_test")
    previous_step = -1
    poll_iter     = 0
    resubmit_count = 0

    while not run_completed():
        _poll_sleep(poll_iter)
        poll_iter += 1
        print(f"\n{'%'*72}\n  {datetime.now():%Y-%m-%d %H:%M:%S}  "
              f"(poll #{poll_iter})\n{'%'*72}")
        status = squeue_line_for(RUN_JOBNAME)
        _, full = sh(QUEUE_CMD)
        print(full)

        # Only dispatch New I/O diagnostics (SCHISM standalone)
        dispatch_diag_plots(run_finished=False)

        if status is not None:
            m = re.search(r"\b\d+\b", status)
            job_id = m.group() if m else job_id
            if re.search(rf"{re.escape(RUN_JOBNAME)}\s+\S+\s+R", status):
                if poll_iter >= 4:
                    step = mirror_time_step()
                    if step is None:
                        log(f"{RUN_JOBNAME}: running but mirror.out has "
                            f"no TIME STEP yet.")
                    elif step == previous_step:
                        log(f"{RUN_JOBNAME}: HANG detected at TIME STEP "
                            f"{step}; cancelling.")
                        sh(f"scancel {job_id}")
                    else:
                        log(f"{RUN_JOBNAME}: advancing, TIME STEP {step}.")
                        previous_step = step
                else:
                    log(f"{RUN_JOBNAME}: running (job {job_id}), "
                        f"early poll — skipping hang check.")
            else:
                log(f"{RUN_JOBNAME}: queued (job {job_id}), waiting ...")
        else:
            if run_completed():
                break

            diag      = diagnose_failure(job_id if job_id != "?" else "0")
            exit_code = job_exit_code(job_id if job_id != "?" else "0")

            if diag:
                log("ERROR: SCHISM run failed with a non-recoverable error.")
                log("Do NOT resubmit until the problem is fixed.")
                log("Details:")
                for line in diag.splitlines()[:12]:
                    log(f"  {line}")
                log(f"SLURM output log: {Path(RUNDIR) / 'myout'}")
                log(f"SLURM error log:  {Path(RUNDIR) / 'err2.out'}")
                log(f"SCHISM abort:     "
                    f"{Path(RUNDIR) / 'outputs' / 'fatal.error'}")
                log("Fix the input, then re-run:")
                log("  stofs-ak --run --phase run --only submit_run "
                    "--config <cfg>")
                sys.exit(1)

            if exit_code > 0:
                log(f"WARNING: job {job_id} exited with code {exit_code}.")
                resubmit_count += 1
                if resubmit_count > _MAX_RESUBMITS:
                    log(f"ERROR: max resubmit limit reached. Exiting.")
                    sys.exit(1)
                log(f"Resubmitting "
                    f"(attempt {resubmit_count}/{_MAX_RESUBMITS}).")
                previous_step = -1
                _clean_outputs()
                submit("run_test")
                continue

            previous_step = -1
            resubmit_count += 1
            if resubmit_count > _MAX_RESUBMITS:
                log(f"ERROR: max resubmit limit reached. Exiting.")
                sys.exit(1)
            log(f"{RUN_JOBNAME}: not in queue and run incomplete — "
                f"resubmitting (attempt {resubmit_count}/{_MAX_RESUBMITS}).")
            _clean_outputs()
            submit("run_test")

    log(f"{RUN_JOBNAME}: run completed successfully.")
    dispatch_diag_plots(run_finished=True)
    combine_output_stacks()
    combine_and_chain()
    log(f"=== auto_hotstart for {MONTH} done ===")


if __name__ == "__main__":
    main()
