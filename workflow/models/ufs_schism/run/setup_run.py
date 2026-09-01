"""
models/ufs_schism/run/setup_run.py
==================================
Phase 4, step "setup_run" (interactive, fast).

Populates each R{ID}_YYYYMM/ run directory so a UFS-SCHISM month can
be launched. Follows the SCHISM setup_run behavior exactly, with the
following UFS-SCHISM-specific differences:

  * forcing/ replaces the sflux/ directory.
  * modulefiles/ is symlinked from I{ID}_YYYYMM/modulefiles/.
  * combine_output11_MPI is copied from bin/ to outputs/ for old I/O combining.
  * Additional monthly inputs are linked from I{ID}_YYYYMM/:
      datm_in, datm.streams, fd_ufs.yaml, noahmptable.tbl,
      model_configure, ufs.configure
  * The UFS-SCHISM executable (e.g. fv3_datm2sch.exe) is copied from bin/.
  * auto_hotstart.py is rendered with COMBINE_OUTPUT_ENABLED=True so that
    after each successful run, combine_output11_MPI combines the per-rank
    schout_NNNNNN_*.nc files into global schout_*.nc daily files, then
    deletes the partition-specific files.

Sentinel: R{ID}_YYYYMM/setup_run.done
"""

import re
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python

TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "schism" / "templates"
)
AUTO_HOTSTART_TEMPLATE = TEMPLATE_DIR / "auto_hotstart.py"
DIAG_SBATCH_TEMPLATE   = TEMPLATE_DIR / "slurm" / "diag_run.sbatch"

# SLURM template for combine_output11_MPI (lives in ufs_schism templates)
COMBINE_OUTPUT_SBATCH_TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "templates" / "slurm" / "run_combine_output.sbatch"
)

FIX_LINKS = [
    "hgrid.gr3", "hgrid.ll", "vgrid.in", "partition.prop", "tvd.prop",
    "albedo.gr3", "diffmin.gr3", "diffmax.gr3", "watertype.gr3",
    "shapiro.gr3", "windrot_geo2proj.gr3", "rough.gr3",
    "estuary.gr3", "TEM_nudge.gr3", "SAL_nudge.gr3", "station.in",
]

INPUT_LINKS = [
    # SCHISM monthly inputs
    "bctides.in", "param.nml", "source.nc",
    "TEM_3D.th.nc", "SAL_3D.th.nc", "elev2D.th.nc", "uv3D.th.nc",
    "TEM_nu.nc", "SAL_nu.nc",
    # UFS-SCHISM monthly inputs
    "datm_in", "datm.streams", "fd_ufs.yaml", "noahmptable.tbl",
    "model_configure", "ufs.configure",
]

OUTPUT_PLACEHOLDERS = [f"staout_{i}" for i in range(1, 21)] + ["flux.out"]

_FIX_FRESHNESS_CHECKS = {
    "param.nml": ("idir", "param.nml"),
    "run_test":  ("rdir", "run_test"),
    "run_comb":  ("rdir", "run_comb"),
}


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


def check_fix_freshness(cfg: dict, mdir: Path, ym: str) -> list:
    pid  = cfg["project_id"]
    fix  = mdir / "fix"
    bind = mdir / "bin"
    idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
    rdir = mdir / f"R{pid}" / f"R{pid}_{ym}"
    exes = cfg.get("executables", {})

    warnings = []
    for fix_name, (dest_key, dest_name) in _FIX_FRESHNESS_CHECKS.items():
        src = fix / fix_name
        if not src.exists():
            continue
        dest_dir = idir if dest_key == "idir" else rdir
        dst = dest_dir / dest_name
        if not dst.exists():
            continue
        if _mtime(src) > _mtime(dst):
            warnings.append(
                f"  fix/{fix_name} ({_fmt_mtime(src)}) is NEWER than "
                f"{dest_dir.name}/{dest_name} ({_fmt_mtime(dst)})."
            )

    ufs_exe = exes.get("ufs_schism")
    if ufs_exe:
        src_exe = bind / ufs_exe
        dst_exe = rdir / ufs_exe
        if src_exe.exists() and dst_exe.exists():
            if _mtime(src_exe) > _mtime(dst_exe):
                warnings.append(
                    f"  bin/{ufs_exe} ({_fmt_mtime(src_exe)}) is NEWER than "
                    f"the copy in {rdir.name}/ ({_fmt_mtime(dst_exe)})."
                )

    combine_exe = exes.get("combine_hotstart")
    if combine_exe:
        src_combine = bind / combine_exe
        dst_combine = rdir / "outputs" / combine_exe
        if src_combine.exists() and dst_combine.exists():
            if _mtime(src_combine) > _mtime(dst_combine):
                warnings.append(
                    f"  bin/{combine_exe} ({_fmt_mtime(src_combine)}) is NEWER "
                    f"than the copy in {rdir.name}/outputs/ "
                    f"({_fmt_mtime(dst_combine)})."
                )

    return warnings


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
        print("  WARNING: no combine_hotstart7 '-i' line found in run_comb; "
              "the combine step may not run. Check fix/run_comb.")
    return new_text


def _render_auto_hotstart(run_dir: Path, subs: dict):
    text = AUTO_HOTSTART_TEMPLATE.read_text()
    for key, val in subs.items():
        text = text.replace("{{" + key + "}}", str(val))
    out = run_dir / "auto_hotstart.py"
    out.write_text(text)
    out.chmod(out.stat().st_mode | stat.S_IXUSR)


def _render_diag_sbatch(cfg: dict, mdir: Path, rdir: Path,
                        config_dir: Path) -> Path:
    slurm    = cfg.get("slurm", {})
    var_cfgs = cfg.get("diag_run_vars", [])
    varnames = [v["var_name"] if isinstance(v, dict) else v
                for v in var_cfgs]

    manifest_path = rdir / "diag_vars.manifest"
    manifest_path.write_text("\n".join(varnames) + "\n")

    nvar = max(len(varnames), 1)
    subs = {
        "WORKDIR":            str(rdir),
        "JOBNAME":            f"diag_{rdir.name}",
        "ACCOUNT":            slurm.get("account",          "nos-surge"),
        "PARTITION":          slurm.get("partition",         "hercules-2"),
        "NDIAG_VARS":         str(nvar),
        "MEM":                slurm.get("diag_run_mem",      "16G"),
        "WALLTIME":           slurm.get("diag_run_walltime", "00:10:00"),
        "LOGDIR":             str(mdir / "logs"),
        "MAILUSER":           slurm.get("mail_user",
                                        "felicio.cassalho@noaa.gov"),
        "PY":                 env_python(cfg, "diag_run_plots",
                                         default="swf_plot"),
        "SCRIPT":             "-m workflow.models.schism.postprocess.diag_run",
        "CONFIG_DIR":         str(config_dir),
        "DIAG_VARS_MANIFEST": str(manifest_path),
    }
    text = DIAG_SBATCH_TEMPLATE.read_text()
    for k, v in subs.items():
        text = text.replace("{{" + k + "}}", str(v))
    out = rdir / "diag_run.sbatch"
    out.write_text(text)
    return out


def _render_combine_output_sbatch(cfg: dict, mdir: Path, rdir: Path,
                                  month_index: int) -> Path:
    """Render run_combine_output.sbatch into the run directory.

    auto_hotstart.py submits this after the run completes to combine
    per-rank schout files into global daily files.
    """
    if not COMBINE_OUTPUT_SBATCH_TEMPLATE.exists():
        raise FileNotFoundError(
            f"combine_output sbatch template not found: "
            f"{COMBINE_OUTPUT_SBATCH_TEMPLATE}"
        )

    slurm = cfg.get("slurm", {})
    pid   = cfg["project_id"]

    # Number of ranks for combine_output11_MPI.
    # Can use fewer than the full job — memory-limited, not CPU-limited.
    # Default: 80 ranks (1 node). Override with slurm.combine_output_nranks.
    combine_nranks = int(slurm.get("combine_output_nranks", 80))
    combine_nodes  = max(1, (combine_nranks + 79) // 80)

    jobname = f"CO{pid}_{month_index + 1:02d}"

    subs = {
        "COMBINE_JOBNAME": jobname,
        "ACCOUNT":         slurm.get("account",                 "nos-surge"),
        "PARTITION":       slurm.get("partition",                "hercules-2"),
        "COMBINE_NODES":   str(combine_nodes),
        "COMBINE_NRANKS":  str(combine_nranks),
        "COMBINE_WALLTIME":slurm.get("combine_output_walltime",  "02:00:00"),
        "LOGDIR":          str(mdir / "logs"),
        "MAILUSER":        slurm.get("mail_user",
                                     "felicio.cassalho@noaa.gov"),
    }
    text = COMBINE_OUTPUT_SBATCH_TEMPLATE.read_text()
    for k, v in subs.items():
        text = text.replace("{{" + k + "}}", str(v))
    out = rdir / "run_combine_output.sbatch"
    out.write_text(text)
    return out


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
    ufs_exe     = exes.get("ufs_schism")
    combine_exe = exes.get("combine_hotstart")
    combine_output_exe = exes.get("combine_output", "combine_output11_MPI")

    if not ufs_exe or not combine_exe:
        print("  ERROR: executables.ufs_schism and executables.combine_hotstart "
              "must be set in project.yaml")
        return False

    missing_exes = [str(bind / e) for e in (ufs_exe, combine_exe)
                    if not (bind / e).exists()]
    if missing_exes:
        print("  ERROR: executables not found in bin/:")
        for m in missing_exes:
            print(f"    {m}")
        return False

    # combine_output11_MPI is required for UFS-SCHISM old I/O
    if not (bind / combine_output_exe).exists():
        print(f"  ERROR: {combine_output_exe} not found in bin/. "
              f"Compile and copy it before running setup_run.")
        return False

    # --- validate job-card templates ---
    missing_cards = [str(fix / n) for n in ("run_test", "run_comb")
                     if not (fix / n).exists()]
    if missing_cards:
        print("  ERROR: required job-card templates not found in fix/:")
        for m in missing_cards:
            print(f"    {m}")
        return False

    # --- check preprocessing sentinels ---
    def _check_sentinel(sentinel_path: Path, step: str) -> bool:
        if not sentinel_path.exists():
            print(f"  ERROR {ym}: '{step}' has not completed successfully.")
            print(f"    Missing sentinel: {sentinel_path}")
            print(f"    Re-run:  stofs-ak --run --only {step} --config <cfg>")
            return False
        return True

    datm_subdir = str(cfg.get("datm_subdir", "forcing"))

    if not _check_sentinel(idir / "gen_3Dth.done",              "gen_3Dth"):      return False
    if not _check_sentinel(idir / "gen_nudge.done",             "gen_nudge"):     return False
    if not _check_sentinel(idir / "sflux" / "gen_sflux.done",  "gen_sflux"):     return False
    if not _check_sentinel(idir / datm_subdir / "gen_datm.done",      "gen_datm"):      return False
    if not _check_sentinel(idir / datm_subdir / "gen_esmf_mesh.done", "gen_esmf_mesh"): return False

    if month_index == 0:
        if not _check_sentinel(idir / "gen_hotstart.done", "gen_hotstart"): return False

    # --- symlink static fix/ files ---
    for name in FIX_LINKS:
        if not _link(fix / name, rdir / name):
            print(f"  NOTE: fix/{name} not found, skipped.")

    # --- symlink monthly inputs ---
    for name in INPUT_LINKS:
        if not _link(idir / name, rdir / name):
            print(f"  ERROR {ym}: required input missing: {idir / name}")
            return False

    # --- symlink forcing/ directory ---
    if not _link(idir / datm_subdir, rdir / "forcing"):
        print(f"  ERROR {ym}: forcing dir missing: {idir / datm_subdir}")
        return False

    # --- symlink modulefiles/ directory ---
    modulefiles_src = idir / "modulefiles"
    if modulefiles_src.is_dir():
        if not _link(modulefiles_src, rdir / "modulefiles"):
            print(f"  WARNING {ym}: could not symlink modulefiles/.")
    else:
        print(f"  WARNING {ym}: no modulefiles/ found in {idir}.")

    # --- month-1: symlink hotstart ---
    if month_index == 0:
        if not _link(idir / "hotstart.nc", rdir / "hotstart.nc"):
            print(f"  ERROR {ym}: month-1 hotstart missing: "
                  f"{idir / 'hotstart.nc'}")
            return False

    # --- copy UFS-SCHISM MPI executable ---
    shutil.copy2(bind / ufs_exe, rdir / ufs_exe)

    # --- outputs/ + placeholders + combine executables ---
    outdir = rdir / "outputs"
    outdir.mkdir(exist_ok=True)
    for name in OUTPUT_PLACEHOLDERS:
        f = outdir / name
        if not f.exists():
            f.touch()
    # copy combine_hotstart7 for hotstart combining
    shutil.copy2(bind / combine_exe, outdir / combine_exe)
    # copy combine_output11_MPI for output combining
    shutil.copy2(bind / combine_output_exe, outdir / combine_output_exe)

    # --- nhot_write from param.nml ---
    nhot_write = _read_nml_int(idir / "param.nml", "nhot_write")
    if nhot_write is None:
        print(f"  ERROR {ym}: could not read nhot_write from "
              f"{idir / 'param.nml'}")
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

    # --- render run_combine_output.sbatch ---
    combine_output_sbatch = _render_combine_output_sbatch(
        cfg, mdir, rdir, month_index)

    # --- diagnostic hook ---
    diag_enabled       = bool(cfg.get("diag_run_plots", False))
    diag_sbatch        = ""
    diag_vars_manifest = ""
    diag_nvar          = 0
    if diag_enabled:
        diag_sbatch        = str(_render_diag_sbatch(cfg, mdir, rdir,
                                                     config_dir))
        diag_vars_manifest = str(rdir / "diag_vars.manifest")
        diag_nvar          = max(len(cfg.get("diag_run_vars", [])), 1)

    # --- render auto_hotstart.py ---
    # UFS-SCHISM uses old I/O: enable combine_output11_MPI after each run.
    slurm = cfg.get("slurm", {})
    combine_nranks = int(slurm.get("combine_output_nranks", 80))

    next_rdir = (mdir / f"R{pid}" / f"R{pid}_{next_ym}") if next_ym else None
    _render_auto_hotstart(rdir, {
        "RUNDIR":                  str(rdir),
        "NEXT_RUNDIR":             f'r"{next_rdir}"' if next_rdir else "None",
        "CHAIN_HOTSTART":          bool(cfg.get("chain_hotstart", True)),
        "IS_LAST_MONTH":           bool(is_last),
        "NHOT_WRITE":              nhot_write,
        "MONTH":                   ym,
        "RUN_JOBNAME":             run_jobname,
        "DIAG_ENABLED":            diag_enabled,
        "DIAG_SBATCH":             diag_sbatch,
        "DIAG_VARS_MANIFEST":      diag_vars_manifest,
        "DIAG_NVAR":               diag_nvar,
        # UFS-SCHISM old I/O: enable output combination
        "COMBINE_OUTPUT_ENABLED":  True,
        "COMBINE_OUTPUT_EXE":      str(outdir / combine_output_exe),
        "COMBINE_OUTPUT_NRANKS":   combine_nranks,
        "COMBINE_OUTPUT_SBATCH":   str(combine_output_sbatch),
    })

    (rdir / "setup_run.done").touch()
    print(f"  {ym}: run directory ready  "
          f"(job {run_jobname}, combine step {nhot_write}, "
          f"combine_output enabled).")
    return True


def run_setup_run(cfg: dict, config_dir=None):
    from pathlib import Path as _Path
    pid        = cfg["project_id"]
    mdir       = model_dir(cfg)
    months     = list_months(cfg)
    config_dir = _Path(config_dir) if config_dir is not None else _Path(".")

    print(f"\n{'='*60}")
    print(f"  setup_run for M{pid}")
    print(f"  {len(months)} month(s): {months[0]} -> {months[-1]}")
    print(f"  chain_hotstart: {bool(cfg.get('chain_hotstart', True))}")
    print(f"{'='*60}")

    all_stale = []
    for ym in months:
        for w in check_fix_freshness(cfg, mdir, ym):
            all_stale.append(f"  [{ym}] {w.strip()}")
    if all_stale:
        print(f"\n  {'!'*58}")
        print("  WARNING: one or more files in fix/ are NEWER than their")
        print("  derived counterparts.")
        for w in all_stale:
            print(w)
        print(f"  {'!'*58}\n")

    failed = []
    for i, ym in enumerate(months):
        next_ym = months[i + 1] if i + 1 < len(months) else None
        is_last = (i + 1 == len(months))
        if not _setup_month(cfg, mdir, ym, i, next_ym, is_last, config_dir):
            failed.append(ym)

    print(f"\n{'='*60}")
    if not failed:
        print("  setup_run complete. No failures.")
        print("  Next: enable submit_run and run inside screen/tmux:")
        print("    stofs-ak --run --phase run --only submit_run "
              "--config <cfg>")
    else:
        print(f"  setup_run finished with {len(failed)} failure(s):")
        for m in failed:
            print(f"    {m}")
    print(f"{'='*60}\n")
    if failed:
        sys.exit(1)
