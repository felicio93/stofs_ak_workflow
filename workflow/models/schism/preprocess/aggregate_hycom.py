"""
models/schism/preprocess/aggregate_hycom.py
==================
Step 2 (interactive, runs in swf_main on any node -- no internet needed).

For each month in the date range, concatenate the daily raw HYCOM files into
monthly SCHISM "stack" files inside the corresponding I{ID}_YYYYMM/ directory:

    I{ID}_YYYYMM/
      SSH_1.nc   (ncrcat of daily ssh_*, variable surf_el)
      UV_1.nc    (ncrcat of daily uv_*,  variables water_u, water_v)
      TS_1.nc    (ncrcat of daily ts_*, then cdo adipot -> potential temp,
                  variable renamed water_temp -> temperature)

The "_1" index matches the SCHISM Fortran convention: each program loops
ifile=1..nfiles reading e.g. TS_<ifile>.nc. One month == one stack == index 1
within its own self-contained directory.

- Skips months whose output already exists (resume-safe, atomic finalize).
- Warns (but proceeds) when days are missing from a month.
"""

import os
import shutil
import subprocess
import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import list_months, model_dir, ProgressTracker, DebugLog
from workflow.core.environment import check_active_env as _check_active_env


REQUIRED_TOOLS = ["ncrcat", "ncrename", "ncap2", "cdo", "ncks"]

# Module-level debug log (set in run_aggregate). Command traces go here.
_DEBUG = None


def check_active_env(cfg: dict):
    _check_active_env(cfg, "aggregate_hycom")


def check_required_tools():
    missing = [t for t in REQUIRED_TOOLS if shutil.which(t) is None]
    if missing:
        print("ERROR: required tools not found on PATH:")
        for t in missing:
            print(f"    - {t}")
        print("These are provided by NCO/CDO. Activate swf_main or 'module load nco cdo'.")
        sys.exit(1)


def _dbg(line: str):
    if _DEBUG is not None:
        _DEBUG.write(line)


def run(cmd, check=True):
    """Run a command; echo it to the debug log (not the screen)."""
    cmd = [str(c) for c in cmd]
    _dbg("CMD: " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stderr.strip():
        _dbg("STDERR: " + result.stderr.strip())
    if check and result.returncode != 0:
        last = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "(no stderr)"
        print(f"  COMMAND FAILED (rc={result.returncode}): {cmd[0]}")
        print(f"    {last}")
        raise RuntimeError(f"Command failed ({result.returncode}): {cmd[0]}")
    return result


def is_complete_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def cleanup(*paths: Path):
    for p in paths:
        p.unlink(missing_ok=True)


def hours_since_2000(d: date) -> int:
    return int((d - date(2000, 1, 1)).total_seconds() // 3600)


# Fixed daily-record ceiling for every monthly stack.
#
# SCHISM's nudging and *.th.nc readers pre-read one record ahead: at model
# time t they read nudging record index (t/step + 2). For a full 31-day month
# with daily (86400 s) forcing, the last timestep needs record index 33, so a
# stack containing only the 31 calendar days (records 1..31 spanning days 0..30)
# runs out and SCHISM aborts with 'tracer nudging nc(2)'.
#
# To always satisfy the read-ahead we build every monthly stack to a FIXED
# ceiling of 34 daily records: the month's own days plus the first few days of
# the following month(s). 34 >= (31 + 2) with margin, so every month is safe.
# The extra days come from the next month's raw HYCOM files, which exist
# because download_hycom fetches 6 days past end_date (covering the last month).
STACK_CEILING = 34


def daily_files_for_stack(var_dir: Path, prefix: str, ym: str):
    """
    Return (present_files, missing_dates) for a 34-daily-record stack that
    starts on day 1 of `ym` and continues into the following day(s) until
    STACK_CEILING files have been considered.

    present_files: sorted list of existing daily Path objects (calendar order).
    missing_dates: list of 'YYYYMMDD' strings with no file (within the ceiling).
    """
    year  = int(ym[:4])
    month = int(ym[4:])
    d = date(year, month, 1)
    present, missing = [], []
    for _ in range(STACK_CEILING):
        flat = d.strftime("%Y%m%d")
        f = var_dir / f"{prefix}_{flat}.nc"
        if f.exists() and f.stat().st_size > 0:
            present.append(f)
        else:
            missing.append(flat)
        d += timedelta(days=1)
    return present, missing


def report_missing(var, ym, missing):
    if missing:
        print(f"  WARNING [{var} {ym}]: {len(missing)} missing day(s) within the "
              f"{STACK_CEILING}-record stack window: {', '.join(missing)}")
        print(f"           Proceeding with available days only.")


def aggregate_ssh(present, out_file: Path, tmp_dir: Path) -> bool:
    if not present:
        print("  SSH: no daily files, skipping.")
        return False
    final_tmp = tmp_dir / f"{out_file.name}.tmp"
    cleanup(final_tmp)
    # ncrcat sorts by record (time) as given; pass files in date order
    run(["ncrcat", "-O"] + present + [final_tmp])
    if not is_complete_file(final_tmp):
        raise RuntimeError("SSH aggregation produced empty output")
    final_tmp.replace(out_file)
    return True


def aggregate_uv(present, out_file: Path, tmp_dir: Path) -> bool:
    if not present:
        print("  UV:  no daily files, skipping.")
        return False
    final_tmp = tmp_dir / f"{out_file.name}.tmp"
    cleanup(final_tmp)
    run(["ncrcat", "-O"] + present + [final_tmp])
    if not is_complete_file(final_tmp):
        raise RuntimeError("UV aggregation produced empty output")
    final_tmp.replace(out_file)
    return True


def aggregate_ts(present, out_file: Path, tmp_dir: Path) -> bool:
    """
    Concatenate daily TS files, then compute potential temperature with
    cdo adipot and rename water_temp -> temperature (SCHISM Fortran expects a
    variable literally named 'temperature').
    """
    if not present:
        print("  TS:  no daily files, skipping.")
        return False

    stack   = tmp_dir / f"ts_stack_{out_file.stem}.nc"
    potnc   = tmp_dir / f"ts_pot_{out_file.stem}.nc"
    final_tmp = tmp_dir / f"{out_file.name}.tmp"
    cleanup(stack, potnc, final_tmp)

    # 1. Concatenate daily files along the time record dimension
    run(["ncrcat", "-O"] + present + [stack])

    # 2. Potential temperature. cdo adipot resets the time axis to the last
    #    value in the input; we restore it afterward from the concatenated
    #    stack's own time coordinate by copying it back.
    #    (cdo adipot expects in-situ temp 'water_temp' + 'salinity'.)
    run(["cdo", "adipot", stack, potnc])

    # 3. cdo may rename/alter the temperature variable to 'tho' (potential
    #    temperature) and salinity to 's'. Normalize to SCHISM names.
    #    Detect which names are present and rename to temperature/salinity.
    varlist = run(["cdo", "-s", "showname", potnc]).stdout.split()
    rename_args = []
    if "tho" in varlist:
        rename_args += ["-v", "tho,temperature"]
    elif "water_temp" in varlist:
        rename_args += ["-v", "water_temp,temperature"]
    if "s" in varlist and "salinity" not in varlist:
        rename_args += ["-v", "s,salinity"]
    if rename_args:
        run(["ncrename", "-O"] + rename_args + [potnc])

    # 4. Restore the correct time axis from the concatenated stack (cdo adipot
    #    can corrupt it). Copy the time coordinate variable back.
    run(["ncks", "-A", "-v", "time", stack, potnc])

    if not is_complete_file(potnc):
        raise RuntimeError("TS aggregation produced empty output")
    potnc.replace(out_file)
    cleanup(stack)
    return True


def run_aggregate(cfg: dict):
    global _DEBUG
    check_active_env(cfg)
    check_required_tools()

    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    raw = mdir / "raw" / "hycom"
    ssh_dir, ts_dir, uv_dir = raw / "ssh", raw / "ts", raw / "uv"
    tmp_dir = raw / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    _DEBUG = DebugLog(mdir / "logs", "aggregate_hycom")

    months = list_months(cfg)

    print(f"\n{'='*60}")
    print(f"  HYCOM monthly aggregation for M{pid}")
    print(f"  {len(months)} month(s): {months[0]} -> {months[-1]}")
    print(f"  Debug trace: {_DEBUG.path}")
    print(f"{'='*60}\n")

    failures = []
    prog = ProgressTracker(total=len(months), label="HYCOM aggregation")

    for ym in months:
        idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
        idir.mkdir(parents=True, exist_ok=True)
        print(f"\n--- {ym}  ({idir}) ---")

        ssh_out = idir / "SSH_1.nc"
        ts_out  = idir / "TS_1.nc"
        uv_out  = idir / "UV_1.nc"

        # SSH
        if is_complete_file(ssh_out):
            print("  SSH: already aggregated, skipping.")
        else:
            present, missing = daily_files_for_stack(ssh_dir, "ssh", ym)
            report_missing("ssh", ym, missing)
            try:
                aggregate_ssh(present, ssh_out, tmp_dir)
            except Exception as exc:
                print(f"  ERROR: SSH aggregation failed for {ym}: {exc}")
                failures.append((ym, "ssh"))

        # UV
        if is_complete_file(uv_out):
            print("  UV:  already aggregated, skipping.")
        else:
            present, missing = daily_files_for_stack(uv_dir, "uv", ym)
            report_missing("uv", ym, missing)
            try:
                aggregate_uv(present, uv_out, tmp_dir)
            except Exception as exc:
                print(f"  ERROR: UV aggregation failed for {ym}: {exc}")
                failures.append((ym, "uv"))

        # TS
        if is_complete_file(ts_out):
            print("  TS:  already aggregated, skipping.")
        else:
            present, missing = daily_files_for_stack(ts_dir, "ts", ym)
            report_missing("ts", ym, missing)
            try:
                aggregate_ts(present, ts_out, tmp_dir)
            except Exception as exc:
                print(f"  ERROR: TS aggregation failed for {ym}: {exc}")
                failures.append((ym, "ts"))

        prog.update(ym)

    print(f"\n{'='*60}")
    if not failures:
        print("  Aggregation complete. No failures.")
    else:
        print(f"  Aggregation complete with {len(failures)} failure(s):")
        for ym, var in failures:
            print(f"    {ym}  {var}")
    print(f"  Full command trace: {_DEBUG.path}")
    print(f"{'='*60}\n")

    _DEBUG.close()
