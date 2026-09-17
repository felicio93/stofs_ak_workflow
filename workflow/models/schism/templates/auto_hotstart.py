"""
models/schism_wwm/run/setup_run.py
==================================
Phase 4, step "setup_run" for SCHISM+WWM (interactive, fast).

Extends standalone SCHISM setup_run with WWM-specific additions:
  - Symlinks wwmbnd.gr3 and hgrid_WWM.gr3 from fix/
  - Symlinks wwminput.nml from I{ID}_{group_id}/
  - Uses executables.schism_wwm instead of executables.schism
  - Checks gen_wwminput sentinel
  - Pre-links hotfile_in_WWM.nc for non-first groups (points to
    previous group's hotfile_out_WWM.nc if it already exists)

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

Sentinel: R{ID}_{group_id}/setup_run.done
"""

import re
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

from workflow.core.config import (
    list_groups,
    model_dir,
    group_date_range,
    get_group_ndays,
)
from workflow.core.environment import env_python

SCHISM_TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "schism" / "templates"
)
AUTO_HOTSTART_TEMPLATE = SCHISM_TEMPLATE_DIR / "auto_hotstart.py"
DIAG_SBATCH_TEMPLATE   = (
    SCHISM_TEMPLATE_DIR / "slurm" / "diag_run.sbatch"
)

# Static files symlinked from fix/
FIX_LINKS = [
    "hgrid.gr3", "hgrid.ll", "vgrid.in", "partition.prop", "tvd.prop",
    "albedo.gr3", "diffmin.gr3", "diffmax.gr3", "watertype.gr3",
    "shapiro.gr3", "windrot_geo2proj.gr3", "rough.gr3",
    "estuary.gr3", "TEM_nudge.gr3", "SAL_nudge.gr3", "station.in",
    # WWM-specific fix/ files
    "wwmbnd.gr3",
    "hgrid_WWM.gr3",
]

# Per-group inputs symlinked from I{ID}_{group_id}/
INPUT_LINKS = [
    "bctides.in", "param.nml", "source.nc",
    "TEM_3D.th.nc", "SAL_3D.th.nc", "elev2D.th.nc", "uv3D.th.nc",
    "TEM_nu.nc", "SAL_nu.nc",
    # WWM-specific per-group input
    "wwminput.nml",
]

OUTPUT_PLACEHOLDERS = (
    [f"staout_{i}" for i in range(1, 21)] + ["flux.out"]
)


# =============================================================================
# Freshness helpers
# =============================================================================

def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return float("-inf")


def _fmt_mtime(p: Path) -> str:
    t = _mtime(p)
    if t == float("-inf"):
        return "(missing)"
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def check_fix_freshness(cfg: dict, mdir: Path,
                        group_id: str) -> list:
    pid  = cfg["project_id"]
    fix  = mdir / "fix"
    bind = mdir / "bin"
    idir = mdir / f"I{pid}" / f"I{pid}_{group_id}"
    rdir = mdir / f"R{pid}" / f"R{pid}_{group_id}"
    exes = cfg.get("executables", {})

    checks = {
        "param.nml": ("idir", "param.nml"),
        "run_test":  ("rdir", "run_test"),
        "run_comb":  ("rdir", "run_comb"),
    }

    warnings = []
    for fix_name, (dest_key, dest_name) in checks.items():
        src = fix / fix_name
        if not src.exists():
            continue
        dest_dir = idir if dest_key == "idir" else rdir
        dst      = dest_dir / dest_name
        if not dst.exists():
            continue
        if _mtime(src) > _mtime(dst):
            warnings.append(
                f"  fix/{fix_name} ({_fmt_mtime(src)}) is NEWER "
                f"than {dest_dir.name}/{dest_name} "
                f"({_fmt_mtime(dst)})."
            )

    wwm_exe = exes.get("schism_wwm")
    if wwm_exe:
        src_exe = bind / wwm_exe
        dst_exe = rdir / wwm_exe
        if src_exe.exists() and dst_exe.exists():
            if _mtime(src_exe) > _mtime(dst_exe):
                warnings.append(
                    f"  bin/{wwm_exe} ({_fmt_mtime(src_exe)}) is "
                    f"NEWER than the copy in {rdir.name}/ "
                    f"({_fmt_mtime(dst_exe)})."
                )
    return warnings


# =============================================================================
# Namelist helper
# =============================================================================

def _read_nml_int(nml_path: Path, param: str):
    text = nml_path.read_text()
    m = re.search(
        r'^\s*' + re.escape(param) + r'\s*=\s*([^\s!]+)',
        text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except ValueError:
        return None


# =============================================================================
# Job-card helpers
# =============================================================================

def _set_sbatch_jobname(text: str, jobname: str) -> str:
    return re.sub(
        r'(#SBATCH\s+-J\s+)(\S+)', rf'\g<1>{jobname}', text)


def _set_sbatch_workdir(text: str, workdir: str) -> str:
    return re.sub(
        r'(#SBATCH\s+-D\s+)(\S+)', rf'\g<1>{workdir}', text)


def _set_combine_command(text: str, combine_exe: str,
                         step: int) -> str:
    configured_name = Path(combine_exe).name
    possible_names  = {
        "combine_hotstart7",
        "combine_hotstart7.exe",
        configured_name,
    }
    escaped = "|".join(
        re.escape(n)
        for n in sorted(possible_names, key=len, reverse=True)
    )
    pattern  = re.compile(
        rf"(?:\S*/)?(?:{escaped})\s+-i\s+\d+")
    new_text, n = pattern.subn(
        f"./{configured_name} -i {step}", text)
    if n == 0:
        print("  WARNING: no combine_hotstart7 '-i' line found "
              "in run_comb.")
    return new_text


# =============================================================================
# Template renderers
# =============================================================================

def _render_auto_hotstart(run_dir: Path, subs: dict):
    text = AUTO_HOTSTART_TEMPLATE.read_text()
    for key, val in subs.items():
        text = text.replace("{{" + key + "}}", str(val))
    out = run_dir / "auto_hotstart.py"
    out.write_text(text)
    out.chmod(out.stat().st_mode | stat.S_IXUSR)


# =============================================================================
# Symlink helper
# =============================================================================

def _link(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    dst.symlink_to(src)
    return True


# =============================================================================
# Per-group setup
# =============================================================================

def _setup_group(cfg: dict, mdir: Path, group_id: str,
                 group_index: int, groups: list,
                 next_group_id: str,
                 is_last: bool, config_dir: Path) -> bool:
    pid  = cfg["project_id"]
    fix  = mdir / "fix"
    bind = mdir / "bin"
    idir = mdir / f"I{pid}" / f"I{pid}_{group_id}"
    rdir = mdir / f"R{pid}" / f"R{pid}_{group_id}"

    gstart, gend = group_date_range(cfg, group_id)
    ndays        = get_group_ndays(cfg, group_id)

    print(f"\n--- setup_run {group_id}  "
          f"({gstart} -> {gend}, {ndays} days)  ({rdir}) ---")

    if (rdir / "setup_run.done").exists():
        print(f"  {group_id}: already set up, skipping.")
        return True

    if not idir.is_dir():
        print(f"  ERROR {group_id}: input dir not found: {idir}")
        return False

    rdir.mkdir(parents=True, exist_ok=True)

    # --- validate executables ---
    exes        = cfg.get("executables", {})
    schism_exe  = exes.get("schism_wwm")
    combine_exe = exes.get("combine_hotstart")

    if not schism_exe:
        print("  ERROR: executables.schism_wwm must be set "
              "in project.yaml")
        return False
    if not combine_exe:
        print("  ERROR: executables.combine_hotstart must be set "
              "in project.yaml")
        return False

    missing = []
    for p in (fix / "run_test", fix / "run_comb",
              bind / schism_exe, bind / combine_exe):
        if not p.exists():
            missing.append(str(p))
    if missing:
        print("  ERROR: required file(s) not found:")
        for m in missing:
            print(f"    {m}")
        return False

    # --- check preprocessing sentinels ---
    def _check_sentinel(sentinel_path: Path,
                        step: str) -> bool:
        if not sentinel_path.exists():
            print(f"  ERROR {group_id}: '{step}' has not "
                  f"completed successfully.")
            print(f"    Missing sentinel: {sentinel_path}")
            print(f"    Re-run:  stofs-ak --run --only {step} "
                  f"--config <cfg>")
            return False
        return True

    if not _check_sentinel(
            idir / "gen_3Dth.done", "gen_3Dth"):
        return False
    if not _check_sentinel(
            idir / "gen_nudge.done", "gen_nudge"):
        return False
    if not _check_sentinel(
            idir / "sflux" / "gen_sflux.done", "gen_sflux"):
        return False
    if not _check_sentinel(
            idir / "gen_wwminput.done", "gen_wwminput"):
        return False
    if not (fix / "wwmbnd.gr3").exists():
        print(f"  ERROR {group_id}: fix/wwmbnd.gr3 not found — "
              f"run gen_wwmbnd first.")
        return False
    if not (fix / "hgrid_WWM.gr3").exists():
        print(f"  ERROR {group_id}: fix/hgrid_WWM.gr3 not found.")
        print("    Create it with:  "
              "cp fix/hgrid.gr3 fix/hgrid_WWM.gr3")
        return False
    if group_index == 0:
        if not _check_sentinel(
                idir / "gen_hotstart.done",
                "gen_hotstart"):
            return False

    # --- symlink static fix/ files ---
    for name in FIX_LINKS:
        if not _link(fix / name, rdir / name):
            print(f"  NOTE: fix/{name} not found, skipped.")

    # --- symlink per-group inputs ---
    for name in INPUT_LINKS:
        if not _link(idir / name, rdir / name):
            print(f"  ERROR {group_id}: required input missing: "
                  f"{idir / name}")
            return False

    # --- symlink sflux/ directory ---
    if not _link(idir / "sflux", rdir / "sflux"):
        print(f"  ERROR {group_id}: sflux dir missing: "
              f"{idir / 'sflux'}")
        return False

    # --- SCHISM hotstart ---
    # First group: symlink from I{ID}_{first}/hotstart.nc (cold start file)
    # Non-first groups: hotstart.nc is chained at runtime by auto_hotstart.py
    if group_index == 0:
        if not _link(idir / "hotstart.nc",
                     rdir / "hotstart.nc"):
            print(f"  ERROR {group_id}: first-group "
                  f"hotstart.nc missing.")
            return False

    # --- WWM hotfile pre-link ---
    # First group: cold start, no hotfile needed (LHOTR=.false.)
    # Non-first groups: link previous group's hotfile_out_WWM.nc
    # as this group's hotfile_in_WWM.nc (FILEHOT_IN in &HOTFILE).
    # This is pre-linked here so the run directory is self-contained
    # even if auto_hotstart.py from the previous group did not run
    # the chaining step yet. If the previous group has not finished,
    # the file won't exist yet and auto_hotstart.py will link it
    # at chain time instead.
    if group_index > 0:
        prev_group_id = groups[group_index - 1]
        prev_rdir     = mdir / f"R{pid}" / f"R{pid}_{prev_group_id}"
        prev_wwm_hot  = prev_rdir / "hotfile_out_WWM.nc"
        this_wwm_in   = rdir / "hotfile_in_WWM.nc"
        if prev_wwm_hot.exists():
            if this_wwm_in.exists() or this_wwm_in.is_symlink():
                this_wwm_in.unlink()
            this_wwm_in.symlink_to(prev_wwm_hot)
            print(f"  {group_id}: pre-linked WWM hotfile: "
                  f"hotfile_in_WWM.nc -> "
                  f"{prev_wwm_hot}")
        else:
            print(f"  {group_id}: NOTE: previous group's "
                  f"hotfile_out_WWM.nc not yet available "
                  f"({prev_wwm_hot}). "
                  f"auto_hotstart.py will link it at "
                  f"chain time.")

    # --- copy SCHISM+WWM executable ---
    shutil.copy2(bind / schism_exe, rdir / schism_exe)

    # --- outputs/ + placeholders + combine exe ---
    outdir = rdir / "outputs"
    outdir.mkdir(exist_ok=True)
    for name in OUTPUT_PLACEHOLDERS:
        f = outdir / name
        if not f.exists():
            f.touch()
    shutil.copy2(bind / combine_exe, outdir / combine_exe)

    # --- nhot_write from param.nml ---
    nhot_write = _read_nml_int(idir / "param.nml", "nhot_write")
    if nhot_write is None:
        print(f"  ERROR {group_id}: could not read nhot_write "
              f"from param.nml")
        return False

    # --- adapt run_test ---
    run_jobname = f"R{pid}_{group_index + 1:02d}"
    run_test    = _set_sbatch_jobname(
        (fix / "run_test").read_text(), run_jobname)
    run_test    = _set_sbatch_workdir(run_test, ".")
    (rdir / "run_test").write_text(run_test)

    # --- adapt run_comb ---
    comb_jobname = f"C{pid}_{group_index + 1:02d}"
    run_comb     = _set_sbatch_jobname(
        (fix / "run_comb").read_text(), comb_jobname)
    run_comb     = _set_sbatch_workdir(run_comb, "./outputs")
    run_comb     = _set_combine_command(
        run_comb, combine_exe, nhot_write)
    (rdir / "run_comb").write_text(run_comb)

    # --- render auto_hotstart.py ---
    next_rdir = (
        mdir / f"R{pid}" / f"R{pid}_{next_group_id}"
        if next_group_id else None
    )
    _render_auto_hotstart(rdir, {
        "RUNDIR":                 str(rdir),
        "NEXT_RUNDIR":            (f'r"{next_rdir}"'
                                   if next_rdir else "None"),
        "CHAIN_HOTSTART":         bool(cfg.get(
                                       "chain_hotstart", True)),
        "IS_LAST_MONTH":          bool(is_last),
        "NHOT_WRITE":             nhot_write,
        "MONTH":                  group_id,
        "RUN_JOBNAME":            run_jobname,
        "DIAG_ENABLED":           False,
        "DIAG_SBATCH":            "",
        "DIAG_VARS_MANIFEST":     "",
        "DIAG_NVAR":              0,
        "COMBINE_DIAG_ENABLED":   False,
        "COMBINE_DIAG_SBATCH":    "",
        "COMBINE_DIAG_NRANKS":    0,
        "COMBINE_OUTPUT_ENABLED": False,
        "COMBINE_OUTPUT_EXE":     "",
        "COMBINE_OUTPUT_NRANKS":  0,
        "COMBINE_OUTPUT_SBATCH":  "",
    })

    (rdir / "setup_run.done").touch()
    print(f"  {group_id}: run directory ready  "
          f"(job {run_jobname}, nhot_write={nhot_write}).")
    return True


# =============================================================================
# Entry point
# =============================================================================

def run_setup_run(cfg: dict, config_dir=None):
    from pathlib import Path as _Path
    pid        = cfg["project_id"]
    mdir       = model_dir(cfg)
    groups     = list_groups(cfg)
    grouping   = cfg.get("grouping", "monthly")
    config_dir = (
        _Path(config_dir)
        if config_dir is not None else _Path(".")
    )

    print(f"\n{'='*60}")
    print(f"  setup_run (SCHISM+WWM) for M{pid}")
    print(f"  Grouping  : {grouping}"
          + (f"  (group_ndays={cfg['group_ndays']})"
             if grouping == "ndays" else ""))
    print(f"  {len(groups)} group(s): "
          f"{groups[0]} -> {groups[-1]}")
    print(f"  chain_hotstart: "
          f"{bool(cfg.get('chain_hotstart', True))}")
    print(f"{'='*60}")

    # Freshness summary
    all_stale = []
    for group_id in groups:
        for w in check_fix_freshness(cfg, mdir, group_id):
            all_stale.append(f"  [{group_id}] {w.strip()}")
    if all_stale:
        print(f"\n  {'!'*58}")
        print("  WARNING: one or more files in fix/ are NEWER "
              "than their derived counterparts.")
        for w in all_stale:
            print(w)
        print(f"  {'!'*58}\n")

    failed = []
    for i, group_id in enumerate(groups):
        next_group_id = (groups[i + 1]
                         if i + 1 < len(groups) else None)
        is_last       = (i + 1 == len(groups))
        if not _setup_group(cfg, mdir, group_id, i,
                            groups,
                            next_group_id, is_last,
                            config_dir):
            failed.append(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  setup_run complete. No failures.")
    else:
        print(f"  setup_run finished with "
              f"{len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
    print(f"{'='*60}\n")
    if failed:
        sys.exit(1)
