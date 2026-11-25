"""
models/schism/preprocess/aggregate_hycom.py
===========================================
Step 2 (no internet needed). By default one SLURM array task per group and
variable (SSH, UV, TS), on compute nodes, and the preprocess phase waits
for them (template
templates/slurm/aggregate_hycom.sbatch; slurm.aggregate_hycom_mem /
_walltime in project.yaml). aggregate_hycom_mode: local runs it in this
process instead (also the fallback where there is no sbatch).

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
- Days on a different HYCOM grid than most of the stack (the GLBv0.08 ->
  GLBy0.08 switch on 2018-12-04) are interpolated onto that grid first.
- Warns (but proceeds) when days are missing from a group.
"""

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


def fill_group_gaps(var_dir: Path, prefix: str, cfg: dict,
                    group_id: str, present, missing):
    """Interpolate missing daily files that fall INSIDE the group's own
    dates (group start..end). Days only in the read-ahead pad after the
    project end are left alone (warning only).

    Returns (present, missing, unfilled_dates): the recomputed file lists
    and the group-internal days that could not be filled.
    """
    from workflow.downloaders.hycom import fill_missing_days

    gstart, gend = group_date_range(cfg, group_id)
    inside = []
    for flat in missing:
        d = date(int(flat[:4]), int(flat[4:6]), int(flat[6:8]))
        if gstart <= d <= gend:
            inside.append(d)
    if not inside:
        return present, missing, []

    filled, unfilled = fill_missing_days(var_dir, prefix, inside)
    if filled:
        present, missing = daily_files_for_stack(
            var_dir, prefix, cfg, group_id)
    return present, missing, unfilled


def _grid_key(nc: Path) -> tuple:
    import netCDF4
    import numpy as np
    with netCDF4.Dataset(str(nc)) as ds:
        return tuple(
            tuple(np.round(np.asarray(ds[c][:], dtype="f8"), 4))
            if c in ds.variables else ()
            for c in ("xlon", "ylat", "depth"))


def harmonise_grids(present, prefix: str, tmp_dir: Path):
    """Daily files on the grid most of them use, for ncrcat.

    HYCOM changed product inside some groups (GLBv0.08 -> GLBy0.08 on
    2018-12-04: same longitudes, different latitudes), and ncrcat needs one
    grid. Each day on another grid is interpolated linearly (xlon, ylat,
    depth) onto the common one into tmp_dir; target points outside its grid
    take the edge values. Returns (files, regridded_tmp_files)."""
    from collections import Counter
    if len(present) < 2:
        return present, []
    keys = [_grid_key(f) for f in present]
    target = Counter(keys).most_common(1)[0][0]
    odd = [i for i, k in enumerate(keys) if k != target]
    if not odd:
        return present, []

    import numpy as np
    import xarray as xr
    ref = present[keys.index(target)]
    with xr.open_dataset(str(ref), decode_times=False) as r:
        tgt = {c: r[c].values for c in ("xlon", "ylat", "depth")
               if c in r.variables}
    files, made = list(present), []
    for i in odd:
        src = present[i]
        out = tmp_dir / f"regrid_{src.name}"
        with xr.open_dataset(str(src), decode_times=False) as ds:
            at = {c: np.clip(v, float(ds[c].min()), float(ds[c].max()))
                  for c, v in tgt.items() if c in ds.dims}
            new = ds.interp(at, method="linear").assign_coords(
                {c: tgt[c] for c in at})
            enc = {c: {"dtype": "float32", "_FillValue": None} for c in at}
            for v in new.data_vars:
                fv = ds[v].encoding.get("_FillValue")
                enc[v] = {"dtype": "float32",
                          "_FillValue": np.float32(fv) if fv is not None
                          else np.float32(np.nan)}
                mv = ds[v].encoding.get("missing_value")
                if mv is not None:
                    enc[v]["missing_value"] = np.float32(mv)
            new.load().to_netcdf(str(out), encoding=enc,
                                 unlimited_dims=["time"])
        files[i] = out
        made.append(out)
    print(f"  {prefix.upper()}: {len(odd)} day(s) on another HYCOM grid "
          f"regridded onto the grid of {len(present) - len(odd)} day(s): "
          + ", ".join(present[i].stem.split("_")[-1] for i in odd))
    return files, made


def report_missing(var, group_id, missing):
    if missing:
        print(f"  WARNING [{var} {group_id}]: {len(missing)} missing day(s) "
              f"within the stack window: {', '.join(missing)}")
        print(f"           (read-ahead pad days beyond the project end "
              f"are not filled; days inside the group are.)")


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


def _aggregate_var(var: str, var_dir: Path, out_file: Path, func,
                   cfg: dict, group_id: str, tmp_dir: Path) -> bool:
    """One variable of one group; True on success (or already done)."""
    label = f"{var.upper()}:".ljust(5)
    if is_complete_file(out_file):
        print(f"  {label} already aggregated, skipping.")
        return True
    present, missing = daily_files_for_stack(var_dir, var, cfg, group_id)
    present, missing, unfilled = fill_group_gaps(
        var_dir, var, cfg, group_id, present, missing)
    report_missing(var, group_id, missing)
    try:
        if unfilled:
            raise RuntimeError(
                "missing day(s) inside the group could not be filled "
                "(gap > 3 days or no good neighbours): "
                + ", ".join(d.strftime("%Y%m%d") for d in unfilled))
        present, regridded = harmonise_grids(present, var, tmp_dir)
        try:
            func(present, out_file, tmp_dir)
        finally:
            cleanup(*regridded)
    except Exception as exc:
        print(f"  ERROR: {var.upper()} aggregation failed for {group_id}: "
              f"{exc}")
        return False
    return True


# variable -> (output file name, aggregation function)
VARIABLES = {"ssh": ("SSH_1.nc", aggregate_ssh),
             "uv":  ("UV_1.nc",  aggregate_uv),
             "ts":  ("TS_1.nc",  aggregate_ts)}


def group_dir(cfg: dict, group_id: str) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"I{pid}" / f"I{pid}_{group_id}"


def aggregate_group(cfg: dict, group_id: str, variables=None) -> list:
    """The stacks of one group (all variables, or the ones given); returns
    the failed variables.

    Temporary files go to raw/hycom/tmp/<group_id>_<var>/, so groups and
    variables can run at the same time (the read-ahead days of one group
    are the first days of the next)."""
    raw = model_dir(cfg) / "raw" / "hycom"
    idir = group_dir(cfg, group_id)
    idir.mkdir(parents=True, exist_ok=True)
    ceil = stack_ceiling(cfg, group_id)
    gstart, gend = group_date_range(cfg, group_id)
    print(f"\n--- {group_id}  ({gstart} -> {gend}, ceiling={ceil} records)"
          f"  ({idir}) ---")
    failed = []
    for var in variables or list(VARIABLES):
        name, func = VARIABLES[var]
        tmp_dir = raw / "tmp" / f"{group_id}_{var}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        if not _aggregate_var(var, raw / var, idir / name, func, cfg,
                              group_id, tmp_dir):
            failed.append(var)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
    return failed


def _pending(cfg: dict) -> list:
    """[(group_id, var), ...] whose stack is not complete yet."""
    return [(g, v) for g in list_groups(cfg) for v, (name, _) in VARIABLES.items()
            if not is_complete_file(group_dir(cfg, g) / name)]


def _submit_tasks(cfg: dict, config_dir: Path, pending: list) -> str:
    """One SLURM array task per (group, variable); returns the job id."""
    from workflow.core.environment import env_python
    from workflow.core.slurm import (
        DEFAULT_ACCOUNT, DEFAULT_MAIL_USER, DEFAULT_PARTITION,
        SlurmSubmitter, write_manifest,
    )
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    manifest = write_manifest([f"{g} {v}" for g, v in pending],
                              logdir / "aggregate_hycom_tasks.manifest")
    slurm = cfg.get("slurm", {})
    py = env_python(cfg, "aggregate_hycom")
    subs = {
        "ACCOUNT":    slurm.get("account", DEFAULT_ACCOUNT),
        "PARTITION":  slurm.get("partition", DEFAULT_PARTITION),
        "MAILUSER":   slurm.get("mail_user", DEFAULT_MAIL_USER),
        "JOBNAME":    f"agghycom_M{pid}",
        "WORKDIR":    str(mdir),
        "NTASKS":     str(len(pending)),
        "MEM":        slurm.get("aggregate_hycom_mem", "32G"),
        "WALLTIME":   slurm.get("aggregate_hycom_walltime", "02:00:00"),
        "LOGDIR":     str(logdir),
        "MANIFEST":   str(manifest),
        "PY":         py,
        "ENVBIN":     str(Path(py).parent),
        "CONFIG_DIR": str(config_dir),
    }
    templates = Path(__file__).resolve().parent.parent / "templates" / "slurm"
    print(f"  Submitting aggregate_hycom: {len(pending)} job(s), one per group "
          f"and variable ({pending[0][0]} -> {pending[-1][0]})")
    out = SlurmSubmitter(templates).render_and_submit(
        "aggregate_hycom.sbatch", subs, logdir / "aggregate_hycom.sbatch")
    print(f"  Logs: {logdir}/aggregate_hycom_<task>.out")
    return SlurmSubmitter.parse_jobid(out)


def run_aggregate(cfg: dict, config_dir=None):
    """All groups: as a SLURM array job (one task per group and variable,
    on compute nodes; the default) and wait for it, or in this process with
    aggregate_hycom_mode: local (or when there is no sbatch/config_dir)."""
    global _DEBUG
    pid      = cfg["project_id"]
    mdir     = model_dir(cfg)
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    mode     = str(cfg.get("aggregate_hycom_mode", "slurm")).lower()
    if mode not in ("slurm", "local"):
        sys.exit(f"ERROR: aggregate_hycom_mode must be slurm or local, "
                 f"got {mode!r}")
    if mode == "slurm" and (config_dir is None or shutil.which("sbatch") is None):
        print("  aggregate_hycom: no sbatch here (or no config folder); "
              "running in this process.")
        mode = "local"

    print(f"\n{'='*60}")
    print(f"  HYCOM aggregation for M{pid}  ({mode})")
    print(f"  Grouping : {grouping}"
          + (f"  (group_ndays={cfg['group_ndays']})"
             if grouping == "ndays" else ""))
    print(f"  {len(groups)} group(s): {groups[0]} -> {groups[-1]}")
    print(f"{'='*60}")

    pending = _pending(cfg)
    if not pending:
        print("  All groups already aggregated. Nothing to do.\n")
        return
    done = len(groups) * len(VARIABLES) - len(pending)
    if done:
        print(f"  {done} stack(s) already aggregated, skipped.")

    if mode == "slurm":
        from workflow.core.slurm import wait_for_slurm_jobs
        jid = _submit_tasks(cfg, Path(config_dir), pending)
        wait_for_slurm_jobs([jid], label="aggregate_hycom job(s)",
                            tag="[aggregate_hycom]",
                            next_step="the next preprocessing steps")
        left = _pending(cfg)
        if left:
            print("  ERROR: aggregate_hycom: stack(s) still incomplete: "
                  + ", ".join(f"{g} {v}" for g, v in left)
                  + f"; see {mdir / 'logs'}/aggregate_hycom_<task>.err "
                  f"(task = line of logs/aggregate_hycom_tasks.manifest)")
            sys.exit(1)
        print("  Aggregation complete (SLURM). No failures.\n")
        return

    check_active_env(cfg)
    check_required_tools()
    _DEBUG = DebugLog(mdir / "logs", "aggregate_hycom")
    print(f"  Debug trace: {_DEBUG.path}")
    failures = []
    todo = [g for g in groups if any(p[0] == g for p in pending)]
    prog = ProgressTracker(total=len(todo))
    for group_id in todo:
        failures += [(group_id, v) for v in aggregate_group(cfg, group_id)]
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


def main(argv=None):
    """One group, or one variable of it (what each SLURM array task runs)."""
    global _DEBUG
    import argparse
    from workflow.core.config import load_config
    ap = argparse.ArgumentParser(description="Aggregate the HYCOM stacks "
                                             "of one group")
    ap.add_argument("--config", required=True)
    ap.add_argument("--group", required=True)
    ap.add_argument("--var", choices=list(VARIABLES), default=None,
                    help="only this variable (default: all)")
    a = ap.parse_args(argv)
    cfg = load_config(Path(a.config))
    check_required_tools()
    tag = a.group + (f"_{a.var}" if a.var else "")
    _DEBUG = DebugLog(model_dir(cfg) / "logs", f"aggregate_hycom_{tag}")
    print(f"  Debug trace: {_DEBUG.path}")
    failed = aggregate_group(cfg, a.group, [a.var] if a.var else None)
    _DEBUG.close()
    if failed:
        print(f"  FAILED: {', '.join(failed)}")
        sys.exit(1)
    print(f"  {tag}: aggregation complete.")


if __name__ == "__main__":
    main()
