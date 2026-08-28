"""
models/ufs_schism/run/setup_run.py
==================================

Phase 4, step "setup_run".

Populates each R{ID}_YYYYMM/ run directory so a UFS-SCHISM month can be
launched.

This follows the SCHISM setup_run implementation as closely as possible.
The main UFS-SCHISM-specific differences are:

  * forcing/ replaces sflux/
  * UFS-specific monthly inputs are linked from I{ID}_YYYYMM/:
      datm_in
      datm.streams
      fd_ufs.yaml
      noahmptable.tbl
      model_configure
      ufs.configure
  * the UFS-SCHISM executable is copied from bin/

Everything else intentionally follows the SCHISM run-phase logic, including:

  * fix/ freshness checks
  * static fix/ symlinks
  * monthly input symlinks
  * month-1 hotstart handling
  * SCHISM executable deployment
  * outputs/ placeholders
  * combine_hotstart7
  * run_test
  * run_comb
  * nhot_write handling
  * auto_hotstart.py rendering
  * diagnostic support
  * resume/sentinel behavior
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
    / "schism"
    / "templates"
)

AUTO_HOTSTART_TEMPLATE = TEMPLATE_DIR / "auto_hotstart.py"
DIAG_SBATCH_TEMPLATE = TEMPLATE_DIR / "slurm" / "diag_run.sbatch"


# =============================================================================
# Static files from fix/
# =============================================================================

FIX_LINKS = [
    "hgrid.gr3",
    "hgrid.ll",
    "vgrid.in",
    "partition.prop",
    "tvd.prop",
    "albedo.gr3",
    "diffmin.gr3",
    "diffmax.gr3",
    "watertype.gr3",
    "shapiro.gr3",
    "windrot_geo2proj.gr3",
    "rough.gr3",
    "estuary.gr3",
    "TEM_nudge.gr3",
    "SAL_nudge.gr3",
    "station.in",
]


# =============================================================================
# Monthly inputs from I{ID}_YYYYMM/
#
# These are required.
#
# UFS-SCHISM uses "forcing/" instead of the SCHISM "sflux/" directory.
# =============================================================================

INPUT_LINKS = [
    # SCHISM monthly inputs
    "bctides.in",
    "param.nml",
    "source.nc",
    "TEM_3D.th.nc",
    "SAL_3D.th.nc",
    "elev2D.th.nc",
    "uv3D.th.nc",
    "TEM_nu.nc",
    "SAL_nu.nc",

    # UFS-SCHISM inputs
    "datm_in",
    "datm.streams",
    "fd_ufs.yaml",
    "noahmptable.tbl",
    "model_configure",
    "ufs.configure",
]


# =============================================================================
# Files SCHISM/UFS-SCHISM expects under outputs/
# =============================================================================

OUTPUT_PLACEHOLDERS = (
    [f"staout_{i}" for i in range(1, 21)]
    + ["flux.out"]
)


# =============================================================================
# fix/ freshness checks
# =============================================================================

_FIX_FRESHNESS_CHECKS = {
    "param.nml": ("idir", "param.nml"),
    "run_test": ("rdir", "run_test"),
    "run_comb": ("rdir", "run_comb"),
}


def _mtime(p: Path) -> float:
    """Return mtime of a path, following symlinks. -inf if missing."""
    try:
        return p.stat().st_mtime
    except OSError:
        return float("-inf")


def _fmt_mtime(p: Path) -> str:
    t = _mtime(p)

    if t == float("-inf"):
        return "(missing)"

    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def check_fix_freshness(
    cfg: dict,
    mdir: Path,
    ym: str,
) -> list:
    """
    Compare fix/ source files against their derived counterparts.

    This is intentionally the same behavior as the SCHISM version.
    """

    pid = cfg["project_id"]

    fix = mdir / "fix"
    bind = mdir / "bin"

    idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
    rdir = mdir / f"R{pid}" / f"R{pid}_{ym}"

    warnings = []

    # fix/ -> I{ID} / R{ID}
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

    # UFS executable -> R directory
    executable_name = cfg.get("executable")

    if executable_name:
        src_exe = bind / executable_name
        dst_exe = rdir / executable_name

        if src_exe.exists() and dst_exe.exists():
            if _mtime(src_exe) > _mtime(dst_exe):
                warnings.append(
                    f"  bin/{executable_name} ({_fmt_mtime(src_exe)}) is "
                    f"NEWER than the copy in {rdir.name}/ "
                    f"({_fmt_mtime(dst_exe)})."
                )

    # combine executable, if configured
    executables = cfg.get("executables", {})
    combine_exe = executables.get("combine_hotstart")

    if combine_exe:
        src_combine = bind / combine_exe
        dst_combine = rdir / "outputs" / combine_exe

        if src_combine.exists() and dst_combine.exists():
            if _mtime(src_combine) > _mtime(dst_combine):
                warnings.append(
                    f"  bin/{combine_exe} ({_fmt_mtime(src_combine)}) is "
                    f"NEWER than the copy in {rdir.name}/outputs/ "
                    f"({_fmt_mtime(dst_combine)})."
                )

    return warnings


# =============================================================================
# Namelist helper
# =============================================================================

def _read_nml_int(nml_path: Path, param: str):
    text = nml_path.read_text()

    m = re.search(
        r"^\s*"
        + re.escape(param)
        + r"\s*=\s*([^\s!]+)",
        text,
        re.IGNORECASE | re.MULTILINE,
    )

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
        r"(#SBATCH\s+-J\s+)(\S+)",
        rf"\g<1>{jobname}",
        text,
    )


def _set_sbatch_workdir(text: str, workdir: str) -> str:
    return re.sub(
        r"(#SBATCH\s+-D\s+)(\S+)",
        rf"\g<1>{workdir}",
        text,
    )


def _set_combine_command(
    text: str,
    combine_exe: str,
    step: int,
) -> str:
    """
    Normalize the combine_hotstart7 invocation.

    combine_hotstart7 expects its inputs in the current working directory,
    which is outputs/.
    """

    pattern = re.compile(
        r"(?:\S*/)?combine_hotstart7(?:\.exe)?\s+-i\s+\d+"
    )

    replacement = f"./{combine_exe} -i {step}"

    new_text, n = pattern.subn(replacement, text)

    if n == 0:
        print(
            "  WARNING: no combine_hotstart7 '-i' line found in run_comb; "
            "the combine step may not run. Check fix/run_comb."
        )

    return new_text


# =============================================================================
# auto_hotstart.py
# =============================================================================

def _render_auto_hotstart(
    run_dir: Path,
    subs: dict,
):
    """
    Render the SAME auto_hotstart.py template used by SCHISM.
    """

    text = AUTO_HOTSTART_TEMPLATE.read_text()

    for key, val in subs.items():
        text = text.replace(
            "{{" + key + "}",
            str(val),
        )

    out = run_dir / "auto_hotstart.py"

    out.write_text(text)

    out.chmod(
        out.stat().st_mode | stat.S_IXUSR
    )


# =============================================================================
# Diagnostic sbatch
# =============================================================================

def _render_diag_sbatch(
    cfg: dict,
    mdir: Path,
    rdir: Path,
    config_dir: Path,
) -> Path:
    slurm = cfg.get("slurm", {})
    var_cfgs = cfg.get("diag_run_vars", [])

    varnames = [
        v["var_name"] if isinstance(v, dict) else v
        for v in var_cfgs
    ]

    manifest_path = rdir / "diag_vars.manifest"
    manifest_path.write_text(
        "\n".join(varnames) + "\n"
    )

    nvar = max(len(varnames), 1)

    subs = {
        "WORKDIR": str(rdir),
        "JOBNAME": f"diag_{rdir.name}",
        "ACCOUNT": slurm.get(
            "account",
            "nos-surge",
        ),
        "PARTITION": slurm.get(
            "partition",
            "hercules-2",
        ),
        "NDIAG_VARS": str(nvar),
        "MEM": slurm.get(
            "diag_run_mem",
            "16G",
        ),
        "WALLTIME": slurm.get(
            "diag_run_walltime",
            "00:10:00",
        ),
        "LOGDIR": str(mdir / "logs"),
        "MAILUSER": slurm.get(
            "mail_user",
            "felicio.cassalho@noaa.gov",
        ),
        "PY": env_python(
            cfg,
            "diag_run_plots",
            default="swf_plot",
        ),
        "SCRIPT": (
            "-m workflow.models.schism.postprocess.diag_run"
        ),
        "CONFIG_DIR": str(config_dir),
        "DIAG_VARS_MANIFEST": str(manifest_path),
    }

    text = DIAG_SBATCH_TEMPLATE.read_text()

    for key, value in subs.items():
        text = text.replace(
            "{{" + key + "}}",
            str(value),
        )

    out = rdir / "diag_run.sbatch"
    out.write_text(text)

    return out


# =============================================================================
# Symlink helper
# =============================================================================

def _link(src: Path, dst: Path):
    """
    Create/refresh a symlink dst -> src.

    Returns False if src does not exist.
    """

    if not src.exists():
        return False

    if dst.exists() or dst.is_symlink():
        dst.unlink()

    dst.symlink_to(src)

    return True


# =============================================================================
# Per-month setup
# =============================================================================

def _setup_month(
    cfg: dict,
    mdir: Path,
    ym: str,
    month_index: int,
    next_ym: str,
    is_last: bool,
    config_dir: Path,
) -> bool:

    pid = cfg["project_id"]

    fix = mdir / "fix"
    bind = mdir / "bin"

    idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
    rdir = mdir / f"R{pid}" / f"R{pid}_{ym}"

    print(f"\n--- setup_run {ym}  ({rdir}) ---")

    if (rdir / "setup_run.done").exists():
        print(f"  {ym}: already set up, skipping.")
        return True

    if not idir.is_dir():
        print(
            f"  ERROR {ym}: input dir not found: {idir}"
        )
        return False

    rdir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ------------------------------------------------------------------
    # Executables
    # ------------------------------------------------------------------

    executable_name = cfg.get("executable")

    if not executable_name:
        print(
            "  ERROR: 'executable' must be set in project.yaml"
        )
        return False

    executable_path = bind / executable_name

    if not executable_path.exists():
        print(
            f"  ERROR: Executable not found: {executable_path}"
        )
        return False

    executables = cfg.get("executables", {})
    combine_exe = executables.get("combine_hotstart")

    if not combine_exe:
        print(
            "  ERROR: executables.combine_hotstart must be set "
            "in project.yaml"
        )
        return False

    combine_path = bind / combine_exe

    if not combine_path.exists():
        print(
            f"  ERROR: combine executable not found: {combine_path}"
        )
        return False

    # ------------------------------------------------------------------
    # Required preprocessing sentinels
    #
    # Keep these exactly analogous to the SCHISM implementation.
    # ------------------------------------------------------------------

    def _check_sentinel(
        sentinel_path: Path,
        step: str,
    ) -> bool:

        if not sentinel_path.exists():
            print(
                f"  ERROR {ym}: '{step}' has not completed successfully."
            )
            print(
                f"    Missing sentinel: {sentinel_path}"
            )
            print(
                "    Either the SLURM job is still running, or it failed."
            )
            print(
                f"    Re-run: stofs-ak --run --only {step} "
                f"--config <cfg>"
            )
            print(
                "    Then re-run setup_run once it completes."
            )
            return False

        return True

    if not _check_sentinel(
        idir / "gen_3Dth.done",
        "gen_3Dth",
    ):
        return False

    if not _check_sentinel(
        idir / "gen_nudge.done",
        "gen_nudge",
    ):
        return False

    # UFS-SCHISM uses forcing rather than sflux.
    forcing_sentinel = (
        idir / "forcing" / "gen_forcing.done"
    )

    if not _check_sentinel(
        forcing_sentinel,
        "gen_forcing",
    ):
        return False

    if month_index == 0:
        if not _check_sentinel(
            idir / "gen_hotstart.done",
            "gen_hotstart",
        ):
            return False

    # ------------------------------------------------------------------
    # Static fix/ files
    # ------------------------------------------------------------------

    for name in FIX_LINKS:
        if not _link(
            fix / name,
            rdir / name,
        ):
            print(
                f"  NOTE: fix/{name} not found, skipped."
            )

    # ------------------------------------------------------------------
    # Monthly inputs
    # ------------------------------------------------------------------

    for name in INPUT_LINKS:
        if not _link(
            idir / name,
            rdir / name,
        ):
            print(
                f"  ERROR {ym}: required input missing: "
                f"{idir / name}"
            )
            return False

    # ------------------------------------------------------------------
    # UFS forcing directory
    #
    # This is the intentional UFS-SCHISM difference from SCHISM's
    # sflux directory.
    # ------------------------------------------------------------------

    if not _link(
        idir / "forcing",
        rdir / "forcing",
    ):
        print(
            f"  ERROR {ym}: forcing dir missing: "
            f"{idir / 'forcing'}"
        )
        return False

    # ------------------------------------------------------------------
    # Month 1 hotstart
    # ------------------------------------------------------------------

    if month_index == 0:
        if not _link(
            idir / "hotstart.nc",
            rdir / "hotstart.nc",
        ):
            print(
                f"  ERROR {ym}: month-1 hotstart missing: "
                f"{idir / 'hotstart.nc'}"
            )
            print(
                "    Run gen_hotstart (Phase 3) first."
            )
            return False

    # Later months receive hotstart.nc from the previous month
    # through auto_hotstart.py.

    # ------------------------------------------------------------------
    # Copy UFS-SCHISM executable
    # ------------------------------------------------------------------

    shutil.copy2(
        executable_path,
        rdir / executable_name,
    )

    # ------------------------------------------------------------------
    # outputs/
    # ------------------------------------------------------------------

    outdir = rdir / "outputs"

    outdir.mkdir(
        exist_ok=True
    )

    for name in OUTPUT_PLACEHOLDERS:
        f = outdir / name

        if not f.exists():
            f.touch()

    shutil.copy2(
        combine_path,
        outdir / combine_exe,
    )

    # ------------------------------------------------------------------
    # nhot_write from param.nml
    # ------------------------------------------------------------------

    nhot_write = _read_nml_int(
        idir / "param.nml",
        "nhot_write",
    )

    if nhot_write is None:
        print(
            f"  ERROR {ym}: could not read nhot_write from "
            f"{idir / 'param.nml'}"
        )
        return False

    # ------------------------------------------------------------------
    # run_test
    # ------------------------------------------------------------------

    run_jobname = (
        f"R{pid}_{month_index + 1:02d}"
    )

    run_test = _set_sbatch_jobname(
        (fix / "run_test").read_text(),
        run_jobname,
    )

    run_test = _set_sbatch_workdir(
        run_test,
        ".",
    )

    (rdir / "run_test").write_text(
        run_test
    )

    # ------------------------------------------------------------------
    # run_comb
    # ------------------------------------------------------------------

    comb_jobname = (
        f"C{pid}_{month_index + 1:02d}"
    )

    run_comb = _set_sbatch_jobname(
        (fix / "run_comb").read_text(),
        comb_jobname,
    )

    run_comb = _set_sbatch_workdir(
        run_comb,
        "./outputs",
    )

    run_comb = _set_combine_command(
        run_comb,
        combine_exe,
        nhot_write,
    )

    (rdir / "run_comb").write_text(
        run_comb
    )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    diag_enabled = bool(
        cfg.get("diag_run_plots", False)
    )

    diag_sbatch = ""
    diag_vars_manifest = ""
    diag_nvar = 0

    if diag_enabled:
        diag_sbatch = str(
            _render_diag_sbatch(
                cfg,
                mdir,
                rdir,
                config_dir,
            )
        )

        diag_vars_manifest = str(
            rdir / "diag_vars.manifest"
        )

        diag_nvar = max(
            len(cfg.get("diag_run_vars", [])),
            1,
        )

    # ------------------------------------------------------------------
    # auto_hotstart.py
    #
    # IMPORTANT:
    # This uses the same template and same substitutions as SCHISM.
    # ------------------------------------------------------------------

    next_rdir = (
        mdir / f"R{pid}" / f"R{pid}_{next_ym}"
        if next_ym
        else None
    )

    _render_auto_hotstart(
        rdir,
        {
            "RUNDIR": str(rdir),
            "NEXT_RUNDIR": (
                f'r"{next_rdir}"'
                if next_rdir
                else "None"
            ),
            "CHAIN_HOTSTART": bool(
                cfg.get(
                    "chain_hotstart",
                    True,
                )
            ),
            "IS_LAST_MONTH": bool(
                is_last
            ),
            "NHOT_WRITE": nhot_write,
            "MONTH": ym,
            "RUN_JOBNAME": run_jobname,
            "DIAG_ENABLED": diag_enabled,
            "DIAG_SBATCH": diag_sbatch,
            "DIAG_VARS_MANIFEST": diag_vars_manifest,
            "DIAG_NVAR": diag_nvar,
        },
    )

    # ------------------------------------------------------------------
    # Sentinel
    # ------------------------------------------------------------------

    (rdir / "setup_run.done").touch()

    print(
        f"  {ym}: run directory ready "
        f"(job {run_jobname}, combine step {nhot_write})."
    )

    return True


# =============================================================================
# Entry point
# =============================================================================

def run_setup_run(
    cfg: dict,
    config_dir=None,
):
    from pathlib import Path as _Path

    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    months = list_months(cfg)

    config_dir = (
        _Path(config_dir)
        if config_dir is not None
        else _Path(".")
    )

    print(f"\n{'=' * 60}")
    print(f"  setup_run for M{pid}")
    print(
        f"  {len(months)} month(s): "
        f"{months[0]} -> {months[-1]}"
    )
    print(
        f"  chain_hotstart: "
        f"{bool(cfg.get('chain_hotstart', True))}"
    )
    print(f"{'=' * 60}")

    # ------------------------------------------------------------------
    # Freshness check
    # ------------------------------------------------------------------

    all_stale = []

    for ym in months:
        warns = check_fix_freshness(
            cfg,
            mdir,
            ym,
        )

        for w in warns:
            all_stale.append(
                f"  [{ym}] {w.strip()}"
            )

    if all_stale:
        print(
            f"\n  {'!' * 58}"
        )

        print(
            "  WARNING: one or more files in fix/ are NEWER than "
            "their derived counterparts in I{ID}_YYYYMM/ or "
            "R{ID}_YYYYMM/."
        )

        print(
            "  This usually means fix/ was edited after preprocessing "
            "or a previous setup_run already ran."
        )

        print(
            "  Check carefully:"
        )

        for w in all_stale:
            print(w)

        print()
        print("  What to do:")

        print(
            "   param.nml changed -> delete "
            "I{ID}_YYYYMM/param.nml and rerun gen_param."
        )

        print(
            "   run_test/run_comb changed -> delete "
            "setup_run.done for affected months and rerun setup_run."
        )

        print(
            "   executable replaced in bin/ -> delete "
            "setup_run.done and rerun setup_run."
        )

        print(
            f"  {'!' * 58}\n"
        )

    # ------------------------------------------------------------------
    # Process months
    # ------------------------------------------------------------------

    failed = []

    for i, ym in enumerate(months):

        next_ym = (
            months[i + 1]
            if i + 1 < len(months)
            else None
        )

        is_last = (
            i + 1 == len(months)
        )

        ok = _setup_month(
            cfg,
            mdir,
            ym,
            i,
            next_ym,
            is_last,
            config_dir,
        )

        if not ok:
            failed.append(ym)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    print(f"\n{'=' * 60}")

    if not failed:
        print(
            "  setup_run complete. No failures."
        )
        print(
            "  Next: enable submit_run and run inside screen/tmux:"
        )
        print(
            "    stofs-ak --run --phase run "
            "--only submit_run --config <cfg>"
        )

    else:
        print(
            f"  setup_run finished with "
            f"{len(failed)} failure(s):"
        )

        for m in failed:
            print(
                f"    {m}"
            )

    print(f"{'=' * 60}\n")

    if failed:
        sys.exit(1)


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Set up UFS-SCHISM run directories."
        )
    )

    parser.add_argument(
        "--config",
        required=True,
        help="Path to the config directory.",
    )

    args = parser.parse_args()

    config_dir = Path(args.config)

    config = __import__(
        "workflow.core.config",
        fromlist=["load_config"],
    ).load_config(config_dir)

    run_setup_run(
        config,
        config_dir,
    )

