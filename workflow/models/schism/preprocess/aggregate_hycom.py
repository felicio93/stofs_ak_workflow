"""
models/schism/preprocess/aggregate_hycom.py
==================
Step 2 (interactive, runs in swf_main on any node -- no internet needed).

For each group in the date range, concatenate the daily raw HYCOM files into
SCHISM "stack" files inside the corresponding I{ID}_{group_id}/ directory:

    I{ID}_{group_id}/
      SSH_1.nc   (ncrcat of daily ssh_*, variable surf_el)
      UV_1.nc    (ncrcat of daily uv_*,  variables water_u, water_v)
      TS_1.nc    (ncrcat of daily ts_*, then cdo adipot -> potential temp,
                  variable renamed water_temp -> temperature)

The "_1" index matches the SCHISM Fortran convention: each program loops
ifile=1..nfiles reading e.g. TS_<ifile>.nc. One group == one stack == index 1
within its own self-contained directory.

Stack length
------------
Every stack is built to stack_ceiling(cfg, group_id) daily records:
  monthly  : up to 34 records (31 days + 3 read-ahead)
  weekly   : 10 records (7 days + 3 read-ahead)
  daily    : 4 records (1 day + 3 read-ahead)
  ndays N  : N + 3 records

The extra days beyond the group end come from the following group's raw
HYCOM files, which exist because download_hycom fetches 6 days past end_date.

- Skips groups whose output already exists (resume-safe, atomic finalize).
- Warns (but proceeds) when days are missing from a group.
"""

import os
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import (
    list_groups,
    model_dir,
    stack_ceiling,
    group_date_range,
    ProgressTracker,
    DebugLog,
)
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
        print("These are provided by NCO/CDO. "
              "Activate swf_main or 'module load nco cdo'.")
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
        last = (result.stderr.strip().splitlines()[-1]
                if result.stderr.strip() else "(no stderr)")
        print(f"  COMMAND FAILED (rc={result.returncode}): {cmd[0]}")
        print(f"    {last}")
        raise RuntimeError(
            f"Command failed ({result.returncode}): {cmd[0]}")
    return result


def is_complete_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def cleanup(*paths: Path):
    for p in paths:
        p.unlink(missing_ok=True)


def hours_since_2000(d: date) -> int:
    return int((d - date(2000, 1, 1)).total_seconds() // 3600)


def daily_files_for_stack(var_dir: Path, prefix: str,
                          cfg: dict, group_id: str):
    """Return (present_files, missing_dates) for a stack that starts on the
    first day of group_id and runs for stack_ceiling(cfg, group_id) records.

    present_files : sorted list of existing daily Path objects.
    missing_dates : list of 'YYYYMMDD' strings with no file.
    """
    ceiling       = stack_ceiling(cfg, group_id)
    group_start, _ = group_date_range(cfg, group_id)

    present, missing = [], []
    for i in range(ceiling):
        d    = group_start + timedelta(days=i)
        flat = d.strftime("%Y%m%d")
        f    = var_dir / f"{prefix}_{flat}.nc"
        if f.exists() and f.stat().st_size > 0:
            present.append(f)
        else:
            missing.append(flat)
    return present, missing


def report_missing(var, group_id, missing):
    if missing:
        ceiling = len(missing)   # just for labelling
        print(f"  WARNING [{var} {group_id}]: {len(missing)} missing day(s) "
              f"within the stack window: {', '.join(missing)}")
        print(f"           Proceeding with available days only.")


def aggregate_ssh(present, out_file: Path, tmp_dir: Path) -> bool:
    if not present:
        print("  SSH: no daily files, skipping.")
        return False
    final_tmp = tmp_dir / f"{out_file.name}.tmp"
    cleanup(final_tmp)
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
    """Concatenate daily TS files, compute potential temperature with
    cdo adipot, rename water_temp -> temperature.
    """
    if not present:
        print("  TS:  no daily files, skipping.")
        return False

    stack     = tmp_dir / f"ts_stack_{out_file.stem}.nc"
    potnc     = tmp_dir / f"ts_pot_{out_file.stem}.nc"
    final_tmp = tmp_dir / f"{out_file.name}.tmp"
    cleanup(stack, potnc, final_tmp)

    # 1. Concatenate
    run(["ncrcat", "-O"] + present + [stack])

    # 2. Potential temperature
    run(["cdo", "adipot", stack, potnc])

    # 3. Normalise variable names
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

    # 4. Restore correct time axis from concatenated stack
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

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    raw    = mdir / "raw" / "hycom"
    ssh_dir, ts_dir, uv_dir = raw / "ssh", raw / "ts", raw / "uv"
    tmp_dir = raw / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    _DEBUG = DebugLog(mdir / "logs", "aggregate_hycom")

    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")

    print(f"\n{'='*60}")
    print(f"  HYCOM aggregation for M{pid}")
    print(f"  Grouping : {grouping}"
          + (f"  (group_ndays={cfg['group_ndays']})"
             if grouping == "ndays" else ""))
    print(f"  {len(groups)} group(s): {groups[0]} -> {groups[-1]}")
    print(f"  Debug trace: {_DEBUG.path}")
    print(f"{'='*60}\n")

    failures = []
    prog     = ProgressTracker(total=len(groups), label="HYCOM aggregation")

    for group_id in groups:
        idir = mdir / f"I{pid}" / f"I{pid}_{group_id}"
        idir.mkdir(parents=True, exist_ok=True)

        ceil = stack_ceiling(cfg, group_id)
        gstart, gend = group_date_range(cfg, group_id)
        print(f"\n--- {group_id}  ({gstart} -> {gend}, "
              f"ceiling={ceil} records)  ({idir}) ---")

        ssh_out = idir / "SSH_1.nc"
        ts_out  = idir / "TS_1.nc"
        uv_out  = idir / "UV_1.nc"

        # SSH
        if is_complete_file(ssh_out):
            print("  SSH: already aggregated, skipping.")
        else:
            present, missing = daily_files_for_stack(
                ssh_dir, "ssh", cfg, group_id)
            report_missing("ssh", group_id, missing)
            try:
                aggregate_ssh(present, ssh_out, tmp_dir)
            except Exception as exc:
                print(f"  ERROR: SSH aggregation failed for {group_id}: {exc}")
                failures.append((group_id, "ssh"))

        # UV
        if is_complete_file(uv_out):
            print("  UV:  already aggregated, skipping.")
        else:
            present, missing = daily_files_for_stack(
                uv_dir, "uv", cfg, group_id)
            report_missing("uv", group_id, missing)
            try:
                aggregate_uv(present, uv_out, tmp_dir)
            except Exception as exc:
                print(f"  ERROR: UV aggregation failed for {group_id}: {exc}")
                failures.append((group_id, "uv"))

        # TS
        if is_complete_file(ts_out):
            print("  TS:  already aggregated, skipping.")
        else:
            present, missing = daily_files_for_stack(
                ts_dir, "ts", cfg, group_id)
            report_missing("ts", group_id, missing)
            try:
                aggregate_ts(present, ts_out, tmp_dir)
            except Exception as exc:
                print(f"  ERROR: TS aggregation failed for {group_id}: {exc}")
                failures.append((group_id, "ts"))

        prog.update(group_id)

    print(f"\n{'='*60}")
    if not failures:
        print("  Aggregation complete. No failures.")
    else:
        print(f"  Aggregation complete with {len(failures)} failure(s):")
        for gid, var in failures:
            print(f"    {gid}  {var}")
    print(f"  Full command trace: {_DEBUG.path}")
    print(f"{'='*60}\n")

    _DEBUG.close()
