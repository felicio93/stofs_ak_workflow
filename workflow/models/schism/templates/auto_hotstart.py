#!/usr/bin/env python3
"""
auto_hotstart.py  (rendered per run directory by setup_run.py)
==============================================================
Self-contained SCHISM monthly run manager for ONE run directory.

Behaviour (ihot=1, single end-of-month hotstart):
  1. Submit run_test via sbatch.
  2. Poll squeue, watching mirror.out for advancement and hang detection.
     _POLL_STEADY is set to 3600s (1 hour) to allow time for large
     restart file I/O at the end of each group (WW3 + mediator restarts
     can total 9+ GB and take 30-60 minutes to write).
  3. On successful completion:
       - (UFS-SCHISM) combine ALL output stacks at end of month
       - submit run_comb (combine_hotstart7) and wait
       - delete per-rank hotstart files
       - (UFS-SCHISM+WW3) convert out_pnt.ww3 -> NetCDF via ww3_ounp
       - symlink combined SCHISM hotstart into next group's run directory
       - chain WW3 restart: copy ufs.cpld.ww3.r.{date}.nc to next run dir
       - chain DATM restart: copy ufs.cpld.datm.r.{date}.nc to next run dir
       - chain mediator restart: copy RESTART/ufs.cpld.cpl.r.{date}.nc
         to next group's RESTART/
       - write rpointer.atm and rpointer.cpl in next run dir
       - symlink WWM hotfile_out_WWM.nc -> next group's hotfile_in_WWM.nc
       - launch next group's auto_hotstart.py (if chain_hotstart=True)
       - write run.done sentinel
"""

import glob
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
# Increased from 1800s to 3600s to allow time for large restart file I/O
# at end of group (WW3 restart ~7GB + mediator restart ~2GB can take
# 30-60 minutes to write after the last time step).
_POLL_STEADY    = 3600
_COMBINE_POLL_SECONDS = 300
_MAX_RESUBMITS  = 5
_GRACE_CHECKS   = 10
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
    """Return True if the model run has finished successfully.

    Checks two completion signals:

    1. SCHISM standalone: 'Run completed successfully' in mirror.out
    2. UFS coupled: 'HAS ENDED' in myout — appears only after ALL
       components finish (including WW3 restart file I/O).
    """
    mo    = Path(RUNDIR) / "outputs" / "mirror.out"
    myout = Path(RUNDIR) / "myout"

    # Check 1: SCHISM standalone
    if mo.exists():
        try:
            lines = mo.read_text(errors="ignore").splitlines()
            for line in reversed(lines[-25:]):
                if "Run completed successfully" in line:
                    return True
        except Exception:
            pass

    # Check 2: UFS coupled
    if myout.exists():
        try:
            if "HAS ENDED" in myout.read_text(errors="ignore"):
                return True
        except Exception:
            pass

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
                if any(k in l for k in (
                    "forrtl:", "ABORT", "srun: error",
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
# New I/O diagnostic dispatch
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
            log(f"diag_run_plots: submitted stack {n} array 1-{DIAG_NVAR}  ({out})")
            _diag_submitted.add(n)
        else:
            log(f"diag_run_plots: sbatch FAILED for stack {n}: {out}")


# =============================================================================
# End-of-month output combination (UFS-SCHISM old I/O)
# =============================================================================

def _clean_schout_partition_files():
    outdir     = Path(RUNDIR) / "outputs"
    pat_schout = re.compile(r"^schout_\d{6}_\d+\.nc$")
    deleted = freed = 0
    for f in sorted(outdir.glob("schout_??????_*.nc")):
        if pat_schout.match(f.name):
            try:    freed += f.stat().st_size
            except: pass
            f.unlink(missing_ok=True)
            deleted += 1
    if deleted:
        log(f"Deleted {deleted} schout partition file(s) "
            f"(~{freed/1e9:.1f} GB freed). "
            f"local_to_global_* preserved for combine_hotstart.")


def _clean_local_to_global_files():
    outdir  = Path(RUNDIR) / "outputs"
    deleted = freed = 0
    for f in sorted(outdir.glob("local_to_global_*")):
        try:    freed += f.stat().st_size
        except: pass
        f.unlink(missing_ok=True)
        deleted += 1
    if deleted:
        log(f"Deleted {deleted} local_to_global file(s) (~{freed/1e9:.1f} GB freed).")


def combine_output_stacks():
    if not COMBINE_OUTPUT_ENABLED:
        return
    outdir   = Path(RUNDIR) / "outputs"
    sentinel = outdir / "combine_output.done"
    if sentinel.exists():
        log("combine_schout: already complete (sentinel found). Skipping.")
        _clean_schout_partition_files()
        return
    rank0_stacks = {
        int(m.group(1))
        for f in outdir.glob("schout_000000_*.nc")
        for m in [re.match(r"^schout_000000_(\d+)\.nc$", f.name)]
        if m
    }
    if not rank0_stacks:
        log("combine_schout: no partition files found to combine.")
        _clean_schout_partition_files()
        sentinel.touch()
        return
    begin        = min(rank0_stacks)
    end          = max(rank0_stacks)
    comb_jobname = "CO" + RUN_JOBNAME[1:]
    log(f"combine_schout: combining {len(rank0_stacks)} stack(s) "
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
    log(f"Submitted combine_schout job: {out}")
    while squeue_line_for(comb_jobname) is not None:
        log(f"  combine_schout ({comb_jobname}): waiting ... "
            f"(checking every {_COMBINE_POLL_SECONDS//60} min)")
        time.sleep(_COMBINE_POLL_SECONDS)
    time.sleep(10)
    missing = [f"schout_{i}.nc" for i in rank0_stacks
               if not (outdir / f"schout_{i}.nc").exists()]
    if missing:
        log(f"ERROR: combine_schout finished but {len(missing)} file(s) missing:")
        for m_name in missing[:10]:
            log(f"  {m_name}")
        sys.exit(1)
    log(f"combine_schout: all {len(rank0_stacks)} stack(s) combined successfully.")
    sentinel.touch()
    _clean_schout_partition_files()


# =============================================================================
# WW3 station output conversion (UFS-SCHISM+WW3)
# =============================================================================

def _convert_ww3_stations():
    """Submit ww3_ounp to convert out_pnt.ww3 -> NetCDF after each group.

    Only runs if:
      1. out_pnt.ww3 exists in the run directory (WW3 point output present)
      2. convert_ww3_sta.sbatch exists (rendered by setup_run for
         model_type=ufs_schism_ww3)
      3. convert_ww3_sta.done does NOT already exist

    Waits for the job to complete before returning so that the NetCDF
    is available for wave_skill postprocessing.

    The output file is ww3.{YYYYMM}_tab.nc containing hs, tr (TM01),
    fp, th1m and other mean wave parameters for all 51 stations.
    """
    rundir   = Path(RUNDIR)
    out_pnt  = rundir / "out_pnt.ww3"
    sbatch   = rundir / "convert_ww3_sta.sbatch"
    sentinel = rundir / "convert_ww3_sta.done"

    # Skip if not a WW3 run
    if not out_pnt.exists():
        return
    if not sbatch.exists():
        log("WARNING: out_pnt.ww3 exists but convert_ww3_sta.sbatch "
            "not found — skipping WW3 station conversion. "
            "Re-run setup_run to generate the sbatch script.")
        return

    if sentinel.exists():
        log("convert_ww3_stations: already complete "
            "(sentinel found). Skipping.")
        return

    log("Submitting convert_ww3_stations job ...")
    rc, out = sh(f"sbatch {sbatch}")
    if rc != 0:
        log(f"WARNING: convert_ww3_stations sbatch failed:\n{out}")
        log("  WW3 station NetCDF will not be available "
            "for wave_skill. Continuing.")
        return

    # Extract job name from sbatch script for squeue polling
    try:
        sbatch_text = sbatch.read_text()
        m = re.search(r"#SBATCH\s+-J\s+(\S+)", sbatch_text)
        jobname = m.group(1) if m else out.strip().split()[-1]
    except Exception:
        jobname = out.strip().split()[-1]

    log(f"Submitted convert_ww3_stations: {out.strip()}")

    # Wait for completion
    while squeue_line_for(jobname) is not None:
        log(f"  convert_ww3_stations ({jobname}): "
            f"waiting ... (checking every 60s)")
        time.sleep(60)

    time.sleep(5)

    # Check sentinel and report
    if sentinel.exists():
        nc_files = list(rundir.glob("ww3.*.nc"))
        log(f"convert_ww3_stations complete. "
            f"{len(nc_files)} NetCDF file(s) written: "
            f"{[f.name for f in nc_files]}")
    else:
        log("WARNING: convert_ww3_stations finished but "
            "convert_ww3_sta.done not found. "
            f"Check {rundir.name}/convert_ww3_sta.err")


# =============================================================================
# Hotstart management
# =============================================================================

def _clean_partition_hotstarts():
    outdir  = Path(RUNDIR) / "outputs"
    pat     = re.compile(r"^hotstart_\d+_\d+\.nc$")
    deleted = freed = 0
    for f in sorted(outdir.glob("hotstart_*.nc")):
        if f.name.startswith("hotstart_it="):
            continue
        if pat.match(f.name):
            try:    freed += f.stat().st_size
            except: pass
            f.unlink(missing_ok=True)
            deleted += 1
    if deleted:
        log(f"Deleted {deleted} per-rank hotstart file(s) "
            f"(~{freed/1e9:.1f} GB freed); kept the combined hotstart.")


def _partition_hotstarts_exist():
    outdir = Path(RUNDIR) / "outputs"
    pat    = re.compile(rf"^hotstart_\d+_{NHOT_WRITE}\.nc$")
    return any(pat.match(f.name)
               for f in outdir.glob(f"hotstart_*_{NHOT_WRITE}.nc"))


def _chain_ww3_restart():
    """Copy WW3, DATM, and mediator restart files from current group
    into next group's run directory, and write rpointer files.

    Files copied to NEXT_RUNDIR/:
      1. ufs.cpld.ww3.r.{date}.nc   — WW3 restart
      2. ufs.cpld.datm.r.{date}.nc  — DATM restart
      3. rpointer.atm               — plain text: DATM restart filename
      4. rpointer.cpl               — plain text: mediator restart path

    Files copied to NEXT_RUNDIR/RESTART/:
      5. ufs.cpld.cpl.r.{date}.nc   — mediator (CMEPS) restart

    Notes:
    - rpointer.atm contains just the filename (no path)
    - rpointer.cpl contains the path relative to the run dir:
        RESTART/ufs.cpld.cpl.r.2025-09-03-00000.nc
    - DATM (datamode=ATMMESH) has a CDEPS bug where ATMMESH is missing
      from the restart READ case list. The workaround is
      skip_restart_read=.true. in datm_in (added by gen_datm_in.py
      for groups 2+). The rpointer.atm is still written here so it is
      available if the bug is ever fixed in a future recompile.
    """
    if NEXT_RUNDIR is None:
        return

    import shutil
    next_rdir = Path(NEXT_RUNDIR)

    # ---- WW3 restart ----
    ww3_files = sorted(glob.glob(
        str(Path(RUNDIR) / "ufs.cpld.ww3.r.*.nc")))
    if ww3_files:
        src = Path(ww3_files[-1])
        dst = next_rdir / src.name
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        shutil.copy2(str(src), str(dst))
        log(f"Copied WW3 restart: {src.name} -> "
            f"{next_rdir.name}/")
    else:
        log(f"WARNING: no ufs.cpld.ww3.r.*.nc found in "
            f"{RUNDIR} — next group WW3 will cold-start.")

    # ---- DATM restart + rpointer.atm ----
    datm_files = sorted(glob.glob(
        str(Path(RUNDIR) / "ufs.cpld.datm.r.*.nc")))
    if datm_files:
        src = Path(datm_files[-1])
        dst = next_rdir / src.name
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        shutil.copy2(str(src), str(dst))
        log(f"Copied DATM restart: {src.name} -> "
            f"{next_rdir.name}/")
        rpointer_atm = next_rdir / "rpointer.atm"
        rpointer_atm.write_text(src.name + "\n")
        log(f"Wrote rpointer.atm -> {src.name}")
    else:
        log(f"WARNING: no ufs.cpld.datm.r.*.nc found in "
            f"{RUNDIR} — next group DATM restart missing.")

    # ---- Mediator (CMEPS) restart + rpointer.cpl ----
    restart_dir = Path(RUNDIR) / "RESTART"
    cpl_files   = sorted(glob.glob(
        str(restart_dir / "ufs.cpld.cpl.r.*.nc")))
    if cpl_files:
        src          = Path(cpl_files[-1])
        next_restart = next_rdir / "RESTART"
        next_restart.mkdir(parents=True, exist_ok=True)
        dst = next_restart / src.name
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        shutil.copy2(str(src), str(dst))
        log(f"Copied mediator restart: {src.name} -> "
            f"{next_rdir.name}/RESTART/")
        rpointer_cpl = next_rdir / "rpointer.cpl"
        rpointer_cpl.write_text(
            f"RESTART/{src.name}\n")
        log(f"Wrote rpointer.cpl -> RESTART/{src.name}")
    else:
        log(f"WARNING: no ufs.cpld.cpl.r.*.nc found in "
            f"{RUNDIR}/RESTART/ — next group mediator "
            f"will cold-start.")


def combine_and_chain():
    combined = Path(RUNDIR) / "outputs" / f"hotstart_it={NHOT_WRITE}.nc"

    if not combined.exists():
        comb_jobname = "C" + RUN_JOBNAME[1:]
        log(f"Submitting combine_hotstart to build {combined.name} ...")
        submit("run_comb")
        while squeue_line_for(comb_jobname) is not None:
            log(f"  combine_hotstart ({comb_jobname}): waiting for "
                f"hotstart_it={NHOT_WRITE}.nc ... "
                f"(checking every {_COMBINE_POLL_SECONDS//60} min)")
            time.sleep(_COMBINE_POLL_SECONDS)
        time.sleep(10)
        if not combined.exists():
            log(f"ERROR: combine_hotstart finished but {combined} was not created.")
            sys.exit(1)
    else:
        log(f"{combined.name} already exists; skipping combine_hotstart.")

    log(f"End-of-group hotstart ready: {combined}")
    _clean_partition_hotstarts()
    _clean_local_to_global_files()

    # Convert WW3 station output to NetCDF (UFS-SCHISM+WW3 only)
    _convert_ww3_stations()

    if IS_LAST_MONTH:
        log("This is the last group; no chaining.")
    elif not CHAIN_HOTSTART:
        log("chain_hotstart=false; not launching the next group.")
    elif NEXT_RUNDIR is None:
        log("No next run directory configured; not chaining.")
    else:
        # ----------------------------------------------------------------
        # Chain SCHISM hotstart
        # ----------------------------------------------------------------
        next_hot = Path(NEXT_RUNDIR) / "hotstart.nc"
        if next_hot.exists() or next_hot.is_symlink():
            next_hot.unlink()
        next_hot.symlink_to(combined)
        log(f"Symlinked SCHISM hotstart: {next_hot} -> {combined}")

        # ----------------------------------------------------------------
        # Chain WW3 + DATM + mediator restarts (UFS-SCHISM+WW3)
        # ----------------------------------------------------------------
        _chain_ww3_restart()

        # ----------------------------------------------------------------
        # Chain WWM hotfile (SCHISM+WWM internal coupling)
        # ----------------------------------------------------------------
        wwm_src = Path(RUNDIR) / "hotfile_out_WWM.nc"
        wwm_dst = Path(NEXT_RUNDIR) / "hotfile_in_WWM.nc"
        if wwm_src.exists():
            if wwm_dst.exists() or wwm_dst.is_symlink():
                wwm_dst.unlink()
            wwm_dst.symlink_to(wwm_src)
            log(f"Symlinked WWM hotfile: {wwm_dst.name} -> {wwm_src}")

    (Path(RUNDIR) / "run.done").touch()
    log(f"Wrote sentinel: {Path(RUNDIR) / 'run.done'}")

    if ((not IS_LAST_MONTH)
            and CHAIN_HOTSTART
            and NEXT_RUNDIR is not None):
        next_script = Path(NEXT_RUNDIR) / "auto_hotstart.py"
        if not next_script.exists():
            log(f"ERROR: {next_script} not found; run setup_run for the next group.")
            sys.exit(1)
        log(f"Launching next group: {next_script}")
        r = subprocess.run(
            [sys.executable, str(next_script)],
            cwd=str(NEXT_RUNDIR))
        if r.returncode != 0:
            log(f"ERROR: next group ({NEXT_RUNDIR}) failed (exit {r.returncode}).")
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
    if (run_completed()
            and (combined.exists() or _partition_hotstarts_exist())):
        log("Run already complete; proceeding to combine outputs and chain.")
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
                    rf"{re.escape(RUN_JOBNAME)}\s+\S+\s+R",
                    status):
                if poll_iter >= 4:
                    step = mirror_time_step()
                    if step is None:
                        log(f"{RUN_JOBNAME}: running but mirror.out has no TIME STEP yet.")
                    elif step == previous_step:
                        log(f"{RUN_JOBNAME}: HANG detected at TIME STEP {step}; cancelling.")
                        sh(f"scancel {job_id}")
                    else:
                        log(f"{RUN_JOBNAME}: advancing, TIME STEP {step}.")
                        previous_step = step
                else:
                    log(f"{RUN_JOBNAME}: running (job {job_id}), early poll - skipping hang check.")
            else:
                log(f"{RUN_JOBNAME}: queued (job {job_id}), waiting ...")
        else:
            if run_completed():
                break
            log(f"{RUN_JOBNAME}: not in queue - waiting up to "
                f"{_GRACE_CHECKS * _GRACE_SLEEP}s for mirror.out to flush ...")
            for _grace in range(_GRACE_CHECKS):
                time.sleep(_GRACE_SLEEP)
                if run_completed():
                    log(f"{RUN_JOBNAME}: run completed successfully "
                        f"(detected after {(_grace+1)*_GRACE_SLEEP}s grace period).")
                    break
            else:
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
                    log(f"SCHISM abort:     {Path(RUNDIR) / 'outputs' / 'fatal.error'}")
                    log("Fix the input, then re-run:")
                    log("  stofs-ak --run --phase run --only submit_run --config <cfg>")
                    sys.exit(1)
                if exit_code > 0:
                    log(f"WARNING: job {job_id} exited with code {exit_code}.")
                    resubmit_count += 1
                    if resubmit_count > _MAX_RESUBMITS:
                        log(f"ERROR: max resubmit limit reached. Exiting.")
                        sys.exit(1)
                    log(f"Resubmitting (attempt {resubmit_count}/{_MAX_RESUBMITS}).")
                    previous_step = -1
                    _clean_outputs()
                    submit("run_test")
                    continue
                previous_step   = -1
                resubmit_count += 1
                if resubmit_count > _MAX_RESUBMITS:
                    log(f"ERROR: max resubmit limit reached. Exiting.")
                    sys.exit(1)
                log(f"{RUN_JOBNAME}: not in queue and run incomplete after grace period "
                    f"- resubmitting (attempt {resubmit_count}/{_MAX_RESUBMITS}).")
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
