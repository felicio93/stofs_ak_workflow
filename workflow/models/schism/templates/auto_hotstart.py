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

Key fix (vs previous version)
------------------------------
_clean_output_partition_files() previously deleted BOTH schout partition
files AND local_to_global_* files in one step, which ran BEFORE
combine_hotstart7.exe. combine_hotstart7.exe needs local_to_global_*
to map partition nodes to global IDs — deleting them first caused:
  forrtl: severe (29): file not found local_to_global_000000

Fix: cleanup is now split into two functions called at the right times:
  _clean_schout_partition_files()   called after combine_output_stacks()
  _clean_local_to_global_files()    called after combine_and_chain()
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
COMBINE_OUTPUT_ENABLED  = {{COMBINE_OUTPUT_ENABLED}}
COMBINE_OUTPUT_EXE      = r"{{COMBINE_OUTPUT_EXE}}"
COMBINE_OUTPUT_NRANKS   = {{COMBINE_OUTPUT_NRANKS}}
COMBINE_OUTPUT_SBATCH   = r"{{COMBINE_OUTPUT_SBATCH}}"
# =============================================================================

_POLL_SCHEDULE  = [0, 60, 60, 60, 60, 60, 300, 1200, 1800]
_POLL_STEADY    = 1800
_COMBINE_POLL_SECONDS = 60
_MAX_RESUBMITS  = 5
_GRACE_CHECKS   = 10   # x 30s = 5 min grace period after job leaves queue
_GRACE_SLEEP    = 30

QUEUE_CMD = (
    f"squeue -u "
    f"{os.environ.get('USER', os.environ.get('LOGNAME', ''))}"
)


def _poll_sleep(poll_iter: int):
    secs = (_POLL_SCHEDULE[poll_iter]
            if poll_iter < len(_POLL_SCHEDULE)
            else _POLL_STEADY)
    if secs > 0:
        time.sleep(secs)


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}",
          flush=True)


def sh(cmd):
    r = subprocess.run(cmd, shell=True,
                       capture_output=True, text=True)
    return r.returncode, (
        r.stdout.strip() + "\n" + r.stderr.strip()
    ).strip()


def squeue_line_for(jobname):
    _, out = sh(QUEUE_CMD)
    pat = re.compile(rf"\b{re.escape(jobname)}\b")
    for line in out.splitlines():
        if pat.search(line):
            return line.strip()
    return None


def _clean_outputs():
    """Delete stale output stacks and mirror.out before
    (re)submitting."""
    outdir  = Path(RUNDIR) / "outputs"
    deleted = []
    for f in sorted(outdir.glob("schout_??????_*.nc")):
        if re.match(r"^schout_\d{6}_\d+\.nc$", f.name):
            f.unlink(missing_ok=True)
            deleted.append(f.name)
    for f in sorted(outdir.glob("schout_*.nc")):
        if re.match(r"^schout_\d+\.nc$", f.name):
            f.unlink(missing_ok=True)
            deleted.append(f.name)
    for f in sorted(outdir.glob("combine_*.done")):
        f.unlink(missing_ok=True)
        deleted.append(f.name)
    mirror = outdir / "mirror.out"
    if mirror.exists():
        mirror.unlink(missing_ok=True)
        deleted.append("mirror.out")
    if deleted:
        log(f"Cleaned {len(deleted)} stale file(s) from "
            f"outputs/ before submission.")
    else:
        log("outputs/ is clean (no stale stacks or "
            "mirror.out to remove).")


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
    """Detect successful run completion.

    Supports both:
      - SCHISM standalone (New I/O): "Run completed successfully"
        in outputs/mirror.out
      - UFS-SCHISM (Old I/O): "hot start written" in mirror.out
        AND "HAS ENDED" in myout
    """
    mo    = Path(RUNDIR) / "outputs" / "mirror.out"
    myout = Path(RUNDIR) / "myout"

    # SCHISM standalone
    if mo.exists():
        try:
            lines = mo.read_text(
                errors="ignore").splitlines()
        except Exception:
            lines = []
        for line in reversed(lines[-25:]):
            if "Run completed successfully" in line:
                return True

    # UFS-SCHISM
    hotstart_written = False
    if mo.exists():
        try:
            content = mo.read_text(errors="ignore")
            if "hot start written" in content:
                hotstart_written = True
        except Exception:
            pass

    ufs_ended = False
    if myout.exists():
        try:
            content = myout.read_text(errors="ignore")
            if "HAS ENDED" in content:
                ufs_ended = True
        except Exception:
            pass

    if hotstart_written and ufs_ended:
        return True

    return False


def job_exit_code(job_id: str) -> int:
    rc, out = sh(
        f"sacct -n -X -o ExitCode -j {job_id}")
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
        lines = err_log.read_text(
            errors="ignore").splitlines()
        diag = [l for l in lines
                if any(k in l for k in (
                    "forrtl:", "ABORT", "srun: error",
                    "severe", "error (78)"))]
        if diag:
            return (f"Fortran/MPI error in err2.out:\n"
                    + "\n".join(diag[:6]))
    myout = Path(RUNDIR) / "myout"
    if myout.exists():
        for line in myout.read_text(
                errors="ignore").splitlines():
            if "ABORT" in line:
                return f"ABORT in myout: {line.strip()}"
    code = job_exit_code(job_id)
    if code > 0:
        return (f"Job {job_id} exited with non-zero "
                f"code {code}.")
    return ""


# =============================================================================
# New I/O diagnostic dispatch (SCHISM standalone only)
# =============================================================================

_diag_submitted = set()


def dispatch_diag_plots(run_finished: bool = False):
    if not DIAG_ENABLED or not DIAG_SBATCH:
        return
    outdir = Path(RUNDIR) / "outputs"
    stacks = sorted(
        int(p.stem.split("_")[1])
        for p in outdir.glob("out2d_*.nc")
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
            f"sbatch --export=ALL,"
            f"DIAG_MONTH={MONTH},DIAG_STACK={n} "
            f"{DIAG_SBATCH}"
        )
        if rc == 0:
            log(f"diag_run_plots: submitted stack {n} "
                f"array 1-{DIAG_NVAR}  ({out})")
            _diag_submitted.add(n)
        else:
            log(f"diag_run_plots: sbatch FAILED for "
                f"stack {n}: {out}")


# =============================================================================
# End-of-month output combination (UFS-SCHISM old I/O)
# Cleanup is split into two functions:
#   _clean_schout_partition_files() — safe to call after combine_output
#   _clean_local_to_global_files()  — must wait until after combine_hotstart7
# =============================================================================

def _clean_schout_partition_files():
    """Delete per-rank schout_NNNNNN_*.nc partition files only.

    Does NOT delete local_to_global_* because combine_hotstart7.exe
    reads those files and must be called before this cleanup.
    """
    outdir         = Path(RUNDIR) / "outputs"
    pat_schout     = re.compile(r"^schout_\d{6}_\d+\.nc$")
    deleted_schout = freed_schout = 0

    for f in sorted(outdir.glob("schout_??????_*.nc")):
        if pat_schout.match(f.name):
            try:
                freed_schout += f.stat().st_size
            except OSError:
                pass
            f.unlink(missing_ok=True)
            deleted_schout += 1

    if deleted_schout:
        log(f"Deleted {deleted_schout} schout partition "
            f"file(s) "
            f"(~{freed_schout / 1e9:.1f} GB freed). "
            f"local_to_global_* preserved for "
            f"combine_hotstart7.")


def _clean_local_to_global_files():
    """Delete local_to_global_* files.

    Must be called AFTER combine_hotstart7.exe has finished —
    these files are needed by combine_hotstart7 and must not be
    deleted before it runs.
    """
    outdir      = Path(RUNDIR) / "outputs"
    deleted_l2g = freed_l2g = 0

    for f in sorted(outdir.glob("local_to_global_*")):
        try:
            freed_l2g += f.stat().st_size
        except OSError:
            pass
        f.unlink(missing_ok=True)
        deleted_l2g += 1

    if deleted_l2g:
        log(f"Deleted {deleted_l2g} local_to_global "
            f"file(s) "
            f"(~{freed_l2g / 1e9:.1f} GB freed).")


def combine_output_stacks():
    if not COMBINE_OUTPUT_ENABLED:
        return

    outdir   = Path(RUNDIR) / "outputs"
    sentinel = outdir / "combine_output.done"

    if sentinel.exists():
        log("combine_output: already complete "
            "(sentinel found). Skipping.")
        # schout partition files can be cleaned now;
        # local_to_global_* will be cleaned after hotstart combine
        _clean_schout_partition_files()
        return

    rank0_stacks = {
        int(m.group(1))
        for f in outdir.glob("schout_000000_*.nc")
        for m in [re.match(
            r"^schout_000000_(\d+)\.nc$", f.name)]
        if m
    }

    if not rank0_stacks:
        log("combine_output: no partition files found "
            "to combine.")
        _clean_schout_partition_files()
        sentinel.touch()
        return

    begin        = min(rank0_stacks)
    end          = max(rank0_stacks)
    comb_jobname = "CO" + RUN_JOBNAME[1:]

    log(f"combine_output: combining "
        f"{len(rank0_stacks)} stack(s) "
        f"(stacks {begin} to {end}) "
        f"with {COMBINE_OUTPUT_NRANKS} MPI ranks ...")

    rc, out = sh(
        f"sbatch --export=ALL,"
        f"COMBINE_BEGIN={begin},COMBINE_END={end},"
        f"COMBINE_JOBNAME={comb_jobname} "
        f"{COMBINE_OUTPUT_SBATCH}"
    )
    if rc != 0:
        log(f"ERROR: sbatch {COMBINE_OUTPUT_SBATCH} "
            f"failed:\n{out}")
        sys.exit(1)
    log(f"Submitted combine_output job: {out}")

    while squeue_line_for(comb_jobname) is not None:
        log(f"  waiting for combine_output job "
            f"({comb_jobname}) ...")
        time.sleep(_COMBINE_POLL_SECONDS)
    time.sleep(10)

    missing = [
        f"schout_{i}.nc" for i in rank0_stacks
        if not (outdir / f"schout_{i}.nc").exists()
    ]
    if missing:
        log(f"ERROR: combine_output finished but "
            f"{len(missing)} file(s) missing:")
        for m_name in missing[:10]:
            log(f"  {m_name}")
        sys.exit(1)

    log(f"combine_output: all {len(rank0_stacks)} "
        f"stack(s) combined successfully.")
    sentinel.touch()

    # Delete schout partition files — no longer needed after
    # combine_output11_MPI has finished.
    # IMPORTANT: local_to_global_* are NOT deleted here because
    # combine_hotstart7.exe needs them. They are cleaned in
    # _clean_local_to_global_files() after combine_and_chain().
    _clean_schout_partition_files()


# =============================================================================
# Hotstart management
# =============================================================================

def _clean_partition_hotstarts():
    """Delete per-rank hotstart_NNNNNN_*.nc files.
    Keeps hotstart_it=*.nc (the combined output).
    """
    outdir  = Path(RUNDIR) / "outputs"
    pat     = re.compile(r"^hotstart_\d+_\d+\.nc$")
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
            f"(~{freed / 1e9:.1f} GB freed); "
            f"kept the combined hotstart.")


def _partition_hotstarts_exist():
    outdir = Path(RUNDIR) / "outputs"
    pat    = re.compile(
        rf"^hotstart_\d+_{NHOT_WRITE}\.nc$")
    return any(
        pat.match(f.name)
        for f in outdir.glob(
            f"hotstart_*_{NHOT_WRITE}.nc")
    )


def combine_and_chain():
    combined = (Path(RUNDIR) / "outputs"
                / f"hotstart_it={NHOT_WRITE}.nc")

    if not combined.exists():
        comb_jobname = "C" + RUN_JOBNAME[1:]
        log(f"Submitting run_comb to build "
            f"{combined.name} ...")
        submit("run_comb")
        while squeue_line_for(comb_jobname) is not None:
            log("  waiting for combine job to finish ...")
            time.sleep(_COMBINE_POLL_SECONDS)
        time.sleep(10)
        if not combined.exists():
            log(f"ERROR: combine finished but "
                f"{combined} was not created.")
            sys.exit(1)
    else:
        log(f"{combined.name} already exists; "
            f"skipping combine.")

    log(f"End-of-month hotstart ready: {combined}")
    _clean_partition_hotstarts()

    # Now safe to delete local_to_global_* —
    # combine_hotstart7.exe has finished successfully.
    _clean_local_to_global_files()

    if IS_LAST_MONTH:
        log("This is the last month; no chaining.")
    elif not CHAIN_HOTSTART:
        log("chain_hotstart=false; not launching "
            "the next month.")
    elif NEXT_RUNDIR is None:
        log("No next run directory configured; "
            "not chaining.")
    else:
        next_hot = Path(NEXT_RUNDIR) / "hotstart.nc"
        if next_hot.exists() or next_hot.is_symlink():
            next_hot.unlink()
        next_hot.symlink_to(combined)
        log(f"Symlinked next month's hotstart: "
            f"{next_hot} -> {combined}")

    (Path(RUNDIR) / "run.done").touch()
    log(f"Wrote sentinel: "
        f"{Path(RUNDIR) / 'run.done'}")

    if ((not IS_LAST_MONTH)
            and CHAIN_HOTSTART
            and NEXT_RUNDIR is not None):
        next_script = (Path(NEXT_RUNDIR)
                       / "auto_hotstart.py")
        if not next_script.exists():
            log(f"ERROR: {next_script} not found; "
                f"run setup_run for the next month.")
            sys.exit(1)
        log(f"Launching next month: {next_script}")
        r = subprocess.run(
            [sys.executable, str(next_script)],
            cwd=str(NEXT_RUNDIR))
        if r.returncode != 0:
            log(f"ERROR: next month ({NEXT_RUNDIR}) "
                f"failed (exit {r.returncode}).")
            sys.exit(r.returncode)


# =============================================================================
# Main
# =============================================================================

def main():
    os.chdir(RUNDIR)
    log(f"=== auto_hotstart for {MONTH}  "
        f"({RUNDIR}) ===")
    log(f"run job name: {RUN_JOBNAME}   "
        f"combine step: {NHOT_WRITE}")

    if (Path(RUNDIR) / "run.done").exists():
        log("run.done already present; nothing to do.")
        return

    if not (Path(RUNDIR) / "hotstart.nc").exists():
        log("ERROR: hotstart.nc not found in run "
            "directory.")
        sys.exit(1)

    combined = (Path(RUNDIR) / "outputs"
                / f"hotstart_it={NHOT_WRITE}.nc")
    if (run_completed()
            and (combined.exists()
                 or _partition_hotstarts_exist())):
        log("Run already complete; proceeding to "
            "combine outputs and chain.")
        dispatch_diag_plots(run_finished=True)
        combine_output_stacks()
        combine_and_chain()
        log(f"=== auto_hotstart for {MONTH} done ===")
        return

    _clean_outputs()
    job_id         = submit("run_test")
    previous_step  = -1
    poll_iter      = 0
    resubmit_count = 0

    while not run_completed():
        _poll_sleep(poll_iter)
        poll_iter += 1
        print(
            f"\n{'%'*72}\n"
            f"  {datetime.now():%Y-%m-%d %H:%M:%S}  "
            f"(poll #{poll_iter})\n"
            f"{'%'*72}")
        status = squeue_line_for(RUN_JOBNAME)
        _, full = sh(QUEUE_CMD)
        print(full)

        dispatch_diag_plots(run_finished=False)

        if status is not None:
            m = re.search(r"\b\d+\b", status)
            job_id = m.group() if m else job_id
            if re.search(
                    rf"{re.escape(RUN_JOBNAME)}"
                    rf"\s+\S+\s+R",
                    status):
                if poll_iter >= 4:
                    step = mirror_time_step()
                    if step is None:
                        log(f"{RUN_JOBNAME}: running "
                            f"but mirror.out has no "
                            f"TIME STEP yet.")
                    elif step == previous_step:
                        log(f"{RUN_JOBNAME}: HANG "
                            f"detected at TIME STEP "
                            f"{step}; cancelling.")
                        sh(f"scancel {job_id}")
                    else:
                        log(f"{RUN_JOBNAME}: advancing,"
                            f" TIME STEP {step}.")
                        previous_step = step
                else:
                    log(f"{RUN_JOBNAME}: running "
                        f"(job {job_id}), early poll "
                        f"— skipping hang check.")
            else:
                log(f"{RUN_JOBNAME}: queued "
                    f"(job {job_id}), waiting ...")
        else:
            if run_completed():
                break

            log(f"{RUN_JOBNAME}: not in queue — "
                f"waiting up to "
                f"{_GRACE_CHECKS * _GRACE_SLEEP}s "
                f"for mirror.out to flush ...")
            for _grace in range(_GRACE_CHECKS):
                time.sleep(_GRACE_SLEEP)
                if run_completed():
                    log(f"{RUN_JOBNAME}: run completed "
                        f"successfully (detected after "
                        f"{(_grace+1)*_GRACE_SLEEP}s "
                        f"grace period).")
                    break
            else:
                diag = diagnose_failure(
                    job_id if job_id != "?" else "0")
                exit_code = job_exit_code(
                    job_id if job_id != "?" else "0")

                if diag:
                    log("ERROR: SCHISM run failed with "
                        "a non-recoverable error.")
                    log("Do NOT resubmit until the "
                        "problem is fixed.")
                    log("Details:")
                    for line in diag.splitlines()[:12]:
                        log(f"  {line}")
                    log(f"SLURM output log: "
                        f"{Path(RUNDIR) / 'myout'}")
                    log(f"SLURM error log:  "
                        f"{Path(RUNDIR) / 'err2.out'}")
                    log(f"SCHISM abort:     "
                        f"{Path(RUNDIR) / 'outputs' / 'fatal.error'}")
                    log("Fix the input, then re-run:")
                    log("  stofs-ak --run --phase run "
                        "--only submit_run --config <cfg>")
                    sys.exit(1)

                if exit_code > 0:
                    log(f"WARNING: job {job_id} exited "
                        f"with code {exit_code}.")
                    resubmit_count += 1
                    if resubmit_count > _MAX_RESUBMITS:
                        log(f"ERROR: max resubmit limit "
                            f"reached. Exiting.")
                        sys.exit(1)
                    log(f"Resubmitting "
                        f"(attempt "
                        f"{resubmit_count}/"
                        f"{_MAX_RESUBMITS}).")
                    previous_step = -1
                    _clean_outputs()
                    submit("run_test")
                    continue

                previous_step   = -1
                resubmit_count += 1
                if resubmit_count > _MAX_RESUBMITS:
                    log(f"ERROR: max resubmit limit "
                        f"reached. Exiting.")
                    sys.exit(1)
                log(f"{RUN_JOBNAME}: not in queue and "
                    f"run incomplete after grace period "
                    f"— resubmitting "
                    f"(attempt {resubmit_count}/"
                    f"{_MAX_RESUBMITS}).")
                _clean_outputs()
                submit("run_test")

            if run_completed():
                break

    log(f"{RUN_JOBNAME}: run completed successfully.")
    dispatch_diag_plots(run_finished=True)
    combine_output_stacks()
    combine_and_chain()
    log(f"=== auto_hotstart for {MONTH} done ===")


if __name__ == "__main__":
    main()
