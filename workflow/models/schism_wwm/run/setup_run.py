"""
models/schism_wwm/run/setup_run.py
==================================
Phase 4, step "setup_run" for SCHISM+WWM (interactive, fast).

Extends standalone SCHISM setup_run with WWM-specific additions:
  - Symlinks wwmbnd.gr3 and hgrid_WWM.gr3 from fix/
  - Symlinks wwminput.nml from I{ID}_YYYYMM/
  - Uses executables.schism_wwm instead of executables.schism
  - Checks gen_wwminput sentinel
"""

import re
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python

SCHISM_TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "schism" / "templates"
)
AUTO_HOTSTART_TEMPLATE = SCHISM_TEMPLATE_DIR / "auto_hotstart.py"
DIAG_SBATCH_TEMPLATE   = SCHISM_TEMPLATE_DIR / "slurm" / "diag_run.sbatch"

FIX_LINKS = [
    "hgrid.gr3", "hgrid.ll", "vgrid.in", "partition.prop", "tvd.prop",
    "albedo.gr3", "diffmin.gr3", "diffmax.gr3", "watertype.gr3",
    "shapiro.gr3", "windrot_geo2proj.gr3", "rough.gr3",
    "estuary.gr3", "TEM_nudge.gr3", "SAL_nudge.gr3", "station.in",
    # WWM-specific fix/ files
    "wwmbnd.gr3",
    "hgrid_WWM.gr3",
]

INPUT_LINKS = [
    "bctides.in", "param.nml", "source.nc",
    "TEM_3D.th.nc", "SAL_3D.th.nc", "elev2D.th.nc", "uv3D.th.nc",
    "TEM_nu.nc", "SAL_nu.nc",
    # WWM-specific per-month input
    "wwminput.nml",
]

OUTPUT_PLACEHOLDERS = [f"staout_{i}" for i in range(1, 21)] + ["flux.out"]


def _read_nml_int(nml_path: Path, param: str):
    text = nml_path.read_text()
    m = re.search(r'^\s*' + re.escape(param) + r'\s*=\s*([^\s!]+)',
                  text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    try:
        return int(float(m.group(1)))
    except ValueError:
        return None


def _set_sbatch_jobname(text: str, jobname: str) -> str:
    return re.sub(r'(#SBATCH\s+-J\s+)(\S+)', rf'\g<1>{jobname}', text)


def _set_sbatch_workdir(text: str, workdir: str) -> str:
    return re.sub(r'(#SBATCH\s+-D\s+)(\S+)', rf'\g<1>{workdir}', text)


def _set_combine_command(text: str, combine_exe: str, step: int) -> str:
    configured_name = Path(combine_exe).name
    possible_names  = {"combine_hotstart7", "combine_hotstart7.exe",
                       configured_name}
    escaped = "|".join(
        re.escape(n) for n in sorted(possible_names, key=len, reverse=True)
    )
    pattern = re.compile(rf"(?:\S*/)?(?:{escaped})\s+-i\s+\d+")
    new_text, n = pattern.subn(f"./{configured_name} -i {step}", text)
    if n == 0:
        print("  WARNING: no combine_hotstart7 '-i' line found in run_comb.")
    return new_text


def _render_auto_hotstart(run_dir: Path, subs: dict):
    text = AUTO_HOTSTART_TEMPLATE.read_text()
    for key, val in subs.items():
        text = text.replace("{{" + key + "}}", str(val))
    out = run_dir / "auto_hotstart.py"
    out.write_text(text)
    out.chmod(out.stat().st_mode | stat.S_IXUSR)


def _link(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    dst.symlink_to(src)
    return True


def _setup_month(cfg: dict, mdir: Path, ym: str, month_index: int,
                 next_ym: str, is_last: bool, config_dir: Path) -> bool:
    pid  = cfg["project_id"]
    fix  = mdir / "fix"
    bind = mdir / "bin"
    idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
    rdir = mdir / f"R{pid}" / f"R{pid}_{ym}"

    print(f"\n--- setup_run {ym}  ({rdir}) ---")

    if (rdir / "setup_run.done").exists():
        print(f"  {ym}: already set up, skipping.")
        return True

    if not idir.is_dir():
        print(f"  ERROR {ym}: input dir not found: {idir}")
        return False

    rdir.mkdir(parents=True, exist_ok=True)

    # --- validate executables ---
    exes        = cfg.get("executables", {})
    schism_exe  = exes.get("schism_wwm")   # WWM key
    combine_exe = exes.get("combine_hotstart")

    if not schism_exe:
        print("  ERROR: executables.schism_wwm must be set in project.yaml")
        return False
    if not combine_exe:
        print("  ERROR: executables.combine_hotstart must be set in project.yaml")
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
    def _check_sentinel(sentinel_path: Path, step: str) -> bool:
        if not sentinel_path.exists():
            print(f"  ERROR {ym}: '{step}' has not completed successfully.")
            print(f"    Missing sentinel: {sentinel_path}")
            return False
        return True

    if not _check_sentinel(idir / "gen_3Dth.done",            "gen_3Dth"):      return False
    if not _check_sentinel(idir / "gen_nudge.done",            "gen_nudge"):     return False
    if not _check_sentinel(idir / "sflux" / "gen_sflux.done", "gen_sflux"):     return False
    if not _check_sentinel(idir / "gen_wwminput.done",         "gen_wwminput"):  return False
    if not (fix / "wwmbnd.gr3").exists():
        print(f"  ERROR {ym}: fix/wwmbnd.gr3 not found — run gen_wwmbnd first.")
        return False
    if not (fix / "hgrid_WWM.gr3").exists():
        print(f"  ERROR {ym}: fix/hgrid_WWM.gr3 not found.")
        print("    Create it with:  cp fix/hgrid.gr3 fix/hgrid_WWM.gr3")
        return False
    if month_index == 0:
        if not _check_sentinel(idir / "gen_hotstart.done", "gen_hotstart"):
            return False

    # --- symlink static fix/ files (includes wwmbnd.gr3 + hgrid_WWM.gr3) ---
    for name in FIX_LINKS:
        if not _link(fix / name, rdir / name):
            print(f"  NOTE: fix/{name} not found, skipped.")

    # --- symlink monthly inputs (includes wwminput.nml) ---
    for name in INPUT_LINKS:
        if not _link(idir / name, rdir / name):
            print(f"  ERROR {ym}: required input missing: {idir / name}")
            return False

    # --- symlink sflux/ directory ---
    if not _link(idir / "sflux", rdir / "sflux"):
        print(f"  ERROR {ym}: sflux dir missing: {idir / 'sflux'}")
        return False

    # --- month-1: symlink SCHISM hotstart ---
    if month_index == 0:
        if not _link(idir / "hotstart.nc", rdir / "hotstart.nc"):
            print(f"  ERROR {ym}: month-1 hotstart.nc missing.")
            return False

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
        print(f"  ERROR {ym}: could not read nhot_write from param.nml")
        return False

    # --- adapt run_test ---
    run_jobname = f"R{pid}_{month_index + 1:02d}"
    run_test    = _set_sbatch_jobname(
        (fix / "run_test").read_text(), run_jobname)
    run_test    = _set_sbatch_workdir(run_test, ".")
    (rdir / "run_test").write_text(run_test)

    # --- adapt run_comb ---
    comb_jobname = f"C{pid}_{month_index + 1:02d}"
    run_comb     = _set_sbatch_jobname(
        (fix / "run_comb").read_text(), comb_jobname)
    run_comb     = _set_sbatch_workdir(run_comb, "./outputs")
    run_comb     = _set_combine_command(run_comb, combine_exe, nhot_write)
    (rdir / "run_comb").write_text(run_comb)

    # --- render auto_hotstart.py ---
    next_rdir = (mdir / f"R{pid}" / f"R{pid}_{next_ym}") if next_ym else None
    _render_auto_hotstart(rdir, {
        "RUNDIR":                  str(rdir),
        "NEXT_RUNDIR":             f'r"{next_rdir}"' if next_rdir else "None",
        "CHAIN_HOTSTART":          bool(cfg.get("chain_hotstart", True)),
        "IS_LAST_MONTH":           bool(is_last),
        "NHOT_WRITE":              nhot_write,
        "MONTH":                   ym,
        "RUN_JOBNAME":             run_jobname,
        "DIAG_ENABLED":            False,
        "DIAG_SBATCH":             "",
        "DIAG_VARS_MANIFEST":      "",
        "DIAG_NVAR":               0,
        "COMBINE_DIAG_ENABLED":    False,
        "COMBINE_DIAG_SBATCH":     "",
        "COMBINE_DIAG_NRANKS":     0,
        "COMBINE_OUTPUT_ENABLED":  False,
        "COMBINE_OUTPUT_EXE":      "",
        "COMBINE_OUTPUT_NRANKS":   0,
        "COMBINE_OUTPUT_SBATCH":   "",
    })

    (rdir / "setup_run.done").touch()
    print(f"  {ym}: run directory ready "
          f"(job {run_jobname}, nhot_write={nhot_write}).")
    return True


def run_setup_run(cfg: dict, config_dir=None):
    from pathlib import Path as _Path
    pid        = cfg["project_id"]
    mdir       = model_dir(cfg)
    months     = list_months(cfg)
    config_dir = _Path(config_dir) if config_dir is not None else _Path(".")

    print(f"\n{'='*60}")
    print(f"  setup_run (SCHISM+WWM) for M{pid}")
    print(f"  {len(months)} month(s): {months[0]} -> {months[-1]}")
    print(f"{'='*60}")

    failed = []
    for i, ym in enumerate(months):
        next_ym = months[i + 1] if i + 1 < len(months) else None
        is_last = (i + 1 == len(months))
        if not _setup_month(cfg, mdir, ym, i, next_ym, is_last, config_dir):
            failed.append(ym)

    print(f"\n{'='*60}")
    if not failed:
        print("  setup_run complete. No failures.")
    else:
        print(f"  setup_run finished with {len(failed)} failure(s):")
        for m in failed:
            print(f"    {m}")
    print(f"{'='*60}\n")
    if failed:
        sys.exit(1)
