"""
models/ufs_schism/run/setup_run.py
==================================

Phase 4, step "setup_run".

Populates each R{ID}_YYYYMM/ run directory so a UFS-SCHISM month can
be launched.

UFS-SCHISM-specific behavior:

  * forcing/ replaces the SCHISM sflux/ directory.
  * UFS-specific monthly inputs are linked from I{ID}_YYYYMM/:
      datm_in
      datm.streams
      fd_ufs.yaml
      noahmptable.tbl
      model_configure
      ufs.configure
  * The UFS-SCHISM executable is copied from bin/.
  * combine_hotstart7 is copied into outputs/.

The remainder intentionally follows the SCHISM setup_run behavior:

  * fix/ freshness checks
  * static fix/ symlinks
  * monthly input symlinks
  * month-1 hotstart handling
  * outputs/ placeholders
  * combine_hotstart7
  * run_test
  * run_comb
  * nhot_write handling
  * auto_hotstart.py rendering
  * diagnostic support
  * resume/sentinel behavior

Sentinel:

    R{ID}_YYYYMM/setup_run.done
"""

import re
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python


# =============================================================================
# Templates
# =============================================================================

# Reuse the existing SCHISM run templates because UFS-SCHISM uses the same
# auto_hotstart and diagnostic infrastructure.
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
# Required monthly files from I{ID}_YYYYMM/
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

    # UFS-SCHISM monthly inputs
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

# SCHISM opens staout files according to the configured tracer count.
# Creating 1..20 covers the common configurations and extra empty files are
# harmless.
OUTPUT_PLACEHOLDERS = (
    [f"staout_{i}" for i in range(1, 21)]
    + ["flux.out"]
)


# =============================================================================
# Freshness checks
# =============================================================================

# fix/ source -> generated/deployed counterpart
#
# param.nml:
#     fix/param.nml -> I{ID}_YYYYMM/param.nml
#
# run_test/run_comb:
#     fix/run_test -> R{ID}_YYYYMM/run_test
#     fix/run_comb -> R{ID}_YYYYMM/run_comb
#
# Executables are handled separately below.
_FIX_FRESHNESS_CHECKS = {
    "param.nml": ("idir", "param.nml"),
    "run_test": ("rdir", "run_test"),
    "run_comb": ("rdir", "run_comb"),
}


def _mtime(path: Path) -> float:
    """
    Return modification time, following symlinks.

    Missing/unstatable files return -inf.
    """
    try:
        return path.stat().st_mtime
    except OSError:
        return float("-inf")


def _fmt_mtime(path: Path) -> str:
    """Return a human-readable modification time."""
    t = _mtime(path)

    if t == float("-inf"):
        return "(missing)"

    return datetime.fromtimestamp(t).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def check_fix_freshness(
    cfg: dict,
    mdir: Path,
    ym: str,
) -> list:
    """
    Compare fix/ and bin/ source files against their deployed/generated
    counterparts for a month.

    Returns warning strings for files where the source is newer.

    Important:
      * param.nml freshness is informational but setup_run will refuse to
        proceed until the generated param.nml is regenerated.
      * run_test/run_comb and executables can be automatically redeployed.
    """

    pid = cfg["project_id"]

    fix = mdir / "fix"
    bind = mdir / "bin"

    idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
    rdir = mdir / f"R{pid}" / f"R{pid}_{ym}"

    warnings = []

    # ------------------------------------------------------------------
    # fix/ -> generated/deployed files
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # UFS-SCHISM executable
    # ------------------------------------------------------------------

    executables = cfg.get("executables", {})

    ufs_exe = executables.get("ufs_schism")

    if ufs_exe:
        src_exe = bind / ufs_exe
        dst_exe = rdir / ufs_exe

        if src_exe.exists() and dst_exe.exists():
            if _mtime(src_exe) > _mtime(dst_exe):
                warnings.append(
                    f"  bin/{ufs_exe} ({_fmt_mtime(src_exe)}) is NEWER "
                    f"than the copy in {rdir.name}/ "
                    f"({_fmt_mtime(dst_exe)})."
                )

    # ------------------------------------------------------------------
    # combine_hotstart executable
    # ------------------------------------------------------------------

    combine_exe = executables.get("combine_hotstart")

    if combine_exe:
        src_combine = bind / combine_exe
        dst_combine = rdir / "outputs" / combine_exe

        if src_combine.exists() and dst_combine.exists():
            if _mtime(src_combine) > _mtime(dst_combine):
                warnings.append(
                    f"  bin/{combine_exe} ({_fmt_mtime(src_combine)}) "
                    f"is NEWER than the copy in "
                    f"{rdir.name}/outputs/ "
                    f"({_fmt_mtime(dst_combine)})."
                )

    return warnings


def _param_is_stale(
    cfg: dict,
    mdir: Path,
    ym: str,
) -> bool:
    """
    Return True when fix/param.nml is newer than the generated monthly
    I{ID}_YYYYMM/param.nml.

    This situation requires gen_param to be rerun rather than allowing
    setup_run to silently deploy stale generated input.
    """

    pid = cfg["project_id"]

    src = mdir / "fix" / "param.nml"
    dst = (
        mdir
        / f"I{pid}"
        / f"I{pid}_{ym}"
        / "param.nml"
    )

    if not src.exists() or not dst.exists():
        return False

    return _mtime(src) > _mtime(dst)


def _deployment_is_stale(
    cfg: dict,
    mdir: Path,
    ym: str,
) -> bool:
    """
    Return True if an existing run directory needs setup_run to redeploy
    files.

    These are safe for setup_run to regenerate automatically:

      * run_test
      * run_comb
      * UFS executable
      * combine executable
    """

    pid = cfg["project_id"]

    fix = mdir / "fix"
    bind = mdir / "bin"

    rdir = (
        mdir
        / f"R{pid}"
        / f"R{pid}_{ym}"
    )

    # run_test
    src = fix / "run_test"
    dst = rdir / "run_test"

    if src.exists() and dst.exists():
        if _mtime(src) > _mtime(dst):
            return True

    # run_comb
    src = fix / "run_comb"
    dst = rdir / "run_comb"

    if src.exists() and dst.exists():
        if _mtime(src) > _mtime(dst):
            return True

    executables = cfg.get("executables", {})

    # UFS executable
    ufs_exe = executables.get("ufs_schism")

    if ufs_exe:
        src = bind / ufs_exe
        dst = rdir / ufs_exe

        if src.exists() and dst.exists():
            if _mtime(src) > _mtime(dst):
                return True

    # combine executable
    combine_exe = executables.get("combine_hotstart")

    if combine_exe:
        src = bind / combine_exe
        dst = rdir / "outputs" / combine_exe

        if src.exists() and dst.exists():
            if _mtime(src) > _mtime(dst):
                return True

    return False


# =============================================================================
# Namelist helper
# =============================================================================

def _read_nml_int(
    nml_path: Path,
    param: str,
):
    """
    Read an integer-valued namelist parameter.

    Supports values such as:

        nhot_write = 168480
        nhot_write = 168480 ! comment
        nhot_write = 168480.0
    """

    text = nml_path.read_text()

    match = re.search(
        r"^\s*"
        + re.escape(param)
        + r"\s*=\s*([^\s!]+)",
        text,
        re.IGNORECASE | re.MULTILINE,
    )

    if not match:
        return None

    try:
        return int(float(match.group(1)))
    except ValueError:
        return None


# =============================================================================
# Job-card helpers
# =============================================================================

def _set_sbatch_jobname(
    text: str,
    jobname: str,
) -> str:
    """Replace #SBATCH -J value."""
    return re.sub(
        r"(#SBATCH\s+-J\s+)(\S+)",
        rf"\g<1>{jobname}",
        text,
    )


def _set_sbatch_workdir(
    text: str,
    workdir: str,
) -> str:
    """Replace #SBATCH -D value."""
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
    Normalize the combine_hotstart invocation.

    combine_hotstart7 reads its per-rank files from the current working
    directory and writes the combined hotstart there.

    run_comb is therefore configured to run with:

        #SBATCH -D ./outputs

    and invoke:

        ./<combine_exe> -i <step>

    The template may contain either the historical combine_hotstart7 name
    or the configured executable name.
    """

    configured_name = Path(combine_exe).name

    possible_names = {
        "combine_hotstart7",
        "combine_hotstart7.exe",
        configured_name,
    }

    escaped_names = "|".join(
        re.escape(name)
        for name in sorted(
            possible_names,
            key=len,
            reverse=True,
        )
    )

    pattern = re.compile(
        rf"(?:\S*/)?(?:{escaped_names})"
        rf"\s+-i\s+\d+"
    )

    replacement = (
        f"./{configured_name} -i {step}"
    )

    new_text, count = pattern.subn(
        replacement,
        text,
    )

    if count == 0:
        print(
            "  WARNING: no combine-hotstart '-i' command found in "
            "run_comb. Check fix/run_comb."
        )

    return new_text


# =============================================================================
# auto_hotstart.py
# =============================================================================

def _render_auto_hotstart(
    run_dir: Path,
    substitutions: dict,
):
    """
    Render the shared SCHISM/UFS-SCHISM auto_hotstart.py template.
    """

    if not AUTO_HOTSTART_TEMPLATE.is_file():
        raise FileNotFoundError(
            f"auto_hotstart template not found: "
            f"{AUTO_HOTSTART_TEMPLATE}"
        )

    text = AUTO_HOTSTART_TEMPLATE.read_text()

    for key, value in substitutions.items():
        text = text.replace(
            "{{" + key + "}}",
            str(value),
        )

    output = run_dir / "auto_hotstart.py"

    output.write_text(text)

    # Make it executable for interactive/manual use, although submit_run
    # invokes it explicitly with sys.executable.
    output.chmod(
        output.stat().st_mode | stat.S_IXUSR
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
    """
    Render diag_run.sbatch into the run directory.

    UFS-SCHISM uses the existing SCHISM diagnostic processor because the
    diagnostic processing operates on the SCHISM output stack produced by
    UFS-SCHISM.
    """

    if not DIAG_SBATCH_TEMPLATE.is_file():
        raise FileNotFoundError(
            f"Diagnostic sbatch template not found: "
            f"{DIAG_SBATCH_TEMPLATE}"
        )

    slurm = cfg.get("slurm", {})
    var_cfgs = cfg.get("diag_run_vars", [])

    varnames = [
        v["var_name"] if isinstance(v, dict) else v
        for v in var_cfgs
    ]

    manifest_path = (
        rdir / "diag_vars.manifest"
    )

    manifest_path.write_text(
        "\n".join(varnames) + "\n"
    )

    nvar = max(
        len(varnames),
        1,
    )

    substitutions = {
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

    for key, value in substitutions.items():
        text = text.replace(
            "{{" + key + "}}",
            str(value),
        )

    output = (
        rdir / "diag_run.sbatch"
    )

    output.write_text(text)

    return output


# =============================================================================
# Symlink helper
# =============================================================================

def _link(
    src: Path,
    dst: Path,
):
    """
    Create/refresh a symlink dst -> src.

    Returns False when src does not exist.

    A real existing directory is never deleted. This protects against
    accidentally destroying a populated run directory.
    """

    if not src.exists():
        return False

    if dst.is_symlink() or dst.is_file():
        dst.unlink()

    elif dst.exists():
        raise RuntimeError(
            f"Refusing to replace existing directory or other "
            f"non-symlink path: {dst}"
        )

    dst.symlink_to(src)

    return True


# =============================================================================
# Executable validation
# =============================================================================

def _check_executable(
    path: Path,
    label: str,
) -> bool:
    """
    Verify that an executable exists and has user execute permission.
    """

    if not path.is_file():
        print(
            f"  ERROR: {label} not found: {path}"
        )
        return False

    if not (
        path.stat().st_mode & stat.S_IXUSR
    ):
        print(
            f"  ERROR: {label} is not executable: {path}"
        )
        print(
            f"    Run: chmod u+x {path}"
        )
        return False

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

    idir = (
        mdir
        / f"I{pid}"
        / f"I{pid}_{ym}"
    )

    rdir = (
        mdir
        / f"R{pid}"
        / f"R{pid}_{ym}"
    )

    print(
        f"\n--- setup_run {ym}  ({rdir}) ---"
    )

    # ------------------------------------------------------------------
    # Existing setup
    #
    # If setup_run.done exists:
    #
    #   * no stale deployment -> skip
    #   * stale param.nml -> stop and require gen_param
    #   * stale deployment files/executables -> rebuild automatically
    # ------------------------------------------------------------------

    sentinel = (
        rdir / "setup_run.done"
    )

    if sentinel.exists():

        if _param_is_stale(
            cfg,
            mdir,
            ym,
        ):
            print(
                f"  ERROR {ym}: fix/param.nml is newer than "
                f"{idir / 'param.nml'}."
            )
            print(
                "    The generated monthly param.nml is stale."
            )
            print(
                "    Rerun gen_param before setup_run:"
            )
            print(
                "      stofs-ak --run --only gen_param "
                "--config <cfg>"
            )
            return False

        if _deployment_is_stale(
            cfg,
            mdir,
            ym,
        ):
            print(
                f"  {ym}: existing setup is stale; "
                "refreshing deployed run files."
            )

            # Remove the sentinel now. It will only be recreated after the
            # complete setup succeeds. This prevents a failed refresh from
            # leaving a misleading setup_run.done behind.
            sentinel.unlink()

        else:
            print(
                f"  {ym}: already set up, skipping."
            )
            return True

    # ------------------------------------------------------------------
    # Input directory
    # ------------------------------------------------------------------

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
    #
    # Expected project.yaml configuration:
    #
    # executables:
    #   ufs_schism: <name>
    #   combine_hotstart: <name>
    # ------------------------------------------------------------------

    executables = cfg.get(
        "executables",
        {},
    )

    ufs_exe = executables.get(
        "ufs_schism"
    )

    combine_exe = executables.get(
        "combine_hotstart"
    )

    if not ufs_exe:
        print(
            "  ERROR: executables.ufs_schism must be set "
            "in project.yaml"
        )
        return False

    if not combine_exe:
        print(
            "  ERROR: executables.combine_hotstart must be set "
            "in project.yaml"
        )
        return False

    ufs_exe_path = (
        bind / ufs_exe
    )

    combine_exe_path = (
        bind / combine_exe
    )

    if not _check_executable(
        ufs_exe_path,
        "UFS-SCHISM executable",
    ):
        return False

    if not _check_executable(
        combine_exe_path,
        "combine_hotstart executable",
    ):
        return False

    # ------------------------------------------------------------------
    # Required preprocessing sentinels
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
                "    Either the SLURM job is still running, "
                "or it failed."
            )

            print(
                f"    Re-run: stofs-ak --run --only {step} "
                "--config <cfg>"
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

    forcing_sentinel = (
        idir
        / "forcing"
        / "gen_forcing.done"
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
    # Verify generated param.nml is not stale
    # ------------------------------------------------------------------

    if _param_is_stale(
        cfg,
        mdir,
        ym,
    ):
        print(
            f"  ERROR {ym}: fix/param.nml is newer than "
            f"{idir / 'param.nml'}."
        )
        print(
            "    setup_run will not deploy a stale generated param.nml."
        )
        print(
            "    Rerun gen_param first:"
        )
        print(
            "      stofs-ak --run --only gen_param "
            "--config <cfg>"
        )
        return False

    # ------------------------------------------------------------------
    # Static fix/ files
    # ------------------------------------------------------------------

    for name in FIX_LINKS:

        try:
            linked = _link(
                fix / name,
                rdir / name,
            )
        except RuntimeError as exc:
            print(
                f"  ERROR {ym}: {exc}"
            )
            return False

        if not linked:
            print(
                f"  NOTE: fix/{name} not found, skipped."
            )

    # ------------------------------------------------------------------
    # Required monthly files
    # ------------------------------------------------------------------

    for name in INPUT_LINKS:

        src = idir / name
        dst = rdir / name

        try:
            linked = _link(
                src,
                dst,
            )
        except RuntimeError as exc:
            print(
                f"  ERROR {ym}: {exc}"
            )
            return False

        if not linked:
            print(
                f"  ERROR {ym}: required input missing: {src}"
            )
            return False

    # ------------------------------------------------------------------
    # UFS forcing directory
    # ------------------------------------------------------------------

    forcing_src = (
        idir / "forcing"
    )

    forcing_dst = (
        rdir / "forcing"
    )

    try:
        linked = _link(
            forcing_src,
            forcing_dst,
        )
    except RuntimeError as exc:
        print(
            f"  ERROR {ym}: {exc}"
        )
        return False

    if not linked:
        print(
            f"  ERROR {ym}: forcing dir missing: "
            f"{forcing_src}"
        )
        return False

    # ------------------------------------------------------------------
    # Month-1 hotstart
    #
    # Later months receive hotstart.nc through auto_hotstart chaining.
    # ------------------------------------------------------------------

    if month_index == 0:

        hotstart_src = (
            idir / "hotstart.nc"
        )

        hotstart_dst = (
            rdir / "hotstart.nc"
        )

        try:
            linked = _link(
                hotstart_src,
                hotstart_dst,
            )
        except RuntimeError as exc:
            print(
                f"  ERROR {ym}: {exc}"
            )
            return False

        if not linked:
            print(
                f"  ERROR {ym}: month-1 hotstart missing: "
                f"{hotstart_src}"
            )
            print(
                "    Run gen_hotstart (Phase 3) first."
            )
            return False

    # ------------------------------------------------------------------
    # Copy UFS-SCHISM executable
    # ------------------------------------------------------------------

    try:
        shutil.copy2(
            ufs_exe_path,
            rdir / Path(ufs_exe).name,
        )
    except OSError as exc:
        print(
            f"  ERROR {ym}: failed to copy UFS-SCHISM executable: "
            f"{exc}"
        )
        return False

    # ------------------------------------------------------------------
    # outputs/
    # ------------------------------------------------------------------

    outdir = (
        rdir / "outputs"
    )

    try:
        outdir.mkdir(
            exist_ok=True
        )
    except OSError as exc:
        print(
            f"  ERROR {ym}: could not create {outdir}: {exc}"
        )
        return False

    for name in OUTPUT_PLACEHOLDERS:

        placeholder = (
            outdir / name
        )

        if not placeholder.exists():
            try:
                placeholder.touch()
            except OSError as exc:
                print(
                    f"  ERROR {ym}: could not create "
                    f"{placeholder}: {exc}"
                )
                return False

    # Copy combine executable into outputs/.
    try:
        shutil.copy2(
            combine_exe_path,
            outdir / Path(combine_exe).name,
        )
    except OSError as exc:
        print(
            f"  ERROR {ym}: failed to copy combine executable: "
            f"{exc}"
        )
        return False

    # ------------------------------------------------------------------
    # nhot_write
    # ------------------------------------------------------------------

    param_path = (
        idir / "param.nml"
    )

    nhot_write = _read_nml_int(
        param_path,
        "nhot_write",
    )

    if nhot_write is None:
        print(
            f"  ERROR {ym}: could not read nhot_write from "
            f"{param_path}"
        )
        return False

    # ------------------------------------------------------------------
    # run_test
    # ------------------------------------------------------------------

    run_jobname = (
        f"R{pid}_{month_index + 1:02d}"
    )

    run_test_template = (
        fix / "run_test"
    )

    if not run_test_template.is_file():
        print(
            f"  ERROR {ym}: run_test not found: "
            f"{run_test_template}"
        )
        return False

    run_test = _set_sbatch_jobname(
        run_test_template.read_text(),
        run_jobname,
    )

    run_test = _set_sbatch_workdir(
        run_test,
        ".",
    )

    try:
        (
            rdir / "run_test"
        ).write_text(run_test)
    except OSError as exc:
        print(
            f"  ERROR {ym}: could not write run_test: {exc}"
        )
        return False

    # ------------------------------------------------------------------
    # run_comb
    # ------------------------------------------------------------------

    comb_jobname = (
        f"C{pid}_{month_index + 1:02d}"
    )

    run_comb_template = (
        fix / "run_comb"
    )

    if not run_comb_template.is_file():
        print(
            f"  ERROR {ym}: run_comb not found: "
            f"{run_comb_template}"
        )
        return False

    run_comb = _set_sbatch_jobname(
        run_comb_template.read_text(),
        comb_jobname,
    )

    run_comb = _set_sbatch_workdir(
        run_comb,
        "./outputs",
    )

    run_comb = _set_combine_command(
        run_comb,
        Path(combine_exe).name,
        nhot_write,
    )

    try:
        (
            rdir / "run_comb"
        ).write_text(run_comb)
    except OSError as exc:
        print(
            f"  ERROR {ym}: could not write run_comb: {exc}"
        )
        return False

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    diag_enabled = bool(
        cfg.get(
            "diag_run_plots",
            False,
        )
    )

    diag_sbatch = ""
    diag_vars_manifest = ""
    diag_nvar = 0

    if diag_enabled:

        try:
            diag_path = _render_diag_sbatch(
                cfg,
                mdir,
                rdir,
                config_dir,
            )
        except OSError as exc:
            print(
                f"  ERROR {ym}: could not render diagnostic sbatch: "
                f"{exc}"
            )
            return False

        diag_sbatch = str(
            diag_path
        )

        diag_vars_manifest = str(
            rdir / "diag_vars.manifest"
        )

        diag_nvar = max(
            len(
                cfg.get(
                    "diag_run_vars",
                    [],
                )
            ),
            1,
        )

    # ------------------------------------------------------------------
    # auto_hotstart.py
    # ------------------------------------------------------------------

    next_rdir = (
        mdir
        / f"R{pid}"
        / f"R{pid}_{next_ym}"
        if next_ym
        else None
    )

    try:
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

                "DIAG_VARS_MANIFEST": (
                    diag_vars_manifest
                ),

                "DIAG_NVAR": diag_nvar,
            },
        )

    except OSError as exc:
        print(
            f"  ERROR {ym}: could not render auto_hotstart.py: "
            f"{exc}"
        )
        return False

    # ------------------------------------------------------------------
    # setup_run sentinel
    #
    # Only create this after EVERYTHING above succeeds.
    # ------------------------------------------------------------------

    try:
        sentinel.touch()
    except OSError as exc:
        print(
            f"  ERROR {ym}: could not create "
            f"{sentinel}: {exc}"
        )
        return False

    print(
        f"  {ym}: run directory ready "
        f"(job {run_jobname}, "
        f"combine step {nhot_write})."
    )

    return True


# =============================================================================
# Entry point
# =============================================================================

def run_setup_run(
    cfg: dict,
    config_dir=None,
):
    """
    Run setup_run for every configured month.
    """

    from pathlib import Path as _Path

    pid = cfg["project_id"]

    mdir = model_dir(cfg)

    months = list_months(cfg)

    if not months:
        print(
            "ERROR: no months configured."
        )
        sys.exit(1)

    config_dir = (
        _Path(config_dir)
        if config_dir is not None
        else _Path(".")
    )

    print(
        f"\n{'=' * 60}"
    )

    print(
        f"  setup_run for M{pid}"
    )

    print(
        f"  {len(months)} month(s): "
        f"{months[0]} -> {months[-1]}"
    )

    print(
        f"  chain_hotstart: "
        f"{bool(cfg.get('chain_hotstart', True))}"
    )

    print(
        f"{'=' * 60}"
    )

    # ------------------------------------------------------------------
    # Freshness summary
    # ------------------------------------------------------------------

    all_stale = []

    for ym in months:

        warnings = check_fix_freshness(
            cfg,
            mdir,
            ym,
        )

        for warning in warnings:
            all_stale.append(
                f"  [{ym}] {warning.strip()}"
            )

    if all_stale:

        print(
            f"\n  {'!' * 58}"
        )

        print(
            "  WARNING: one or more source files are newer than "
            "their generated/deployed counterparts."
        )

        print(
            "  Details:"
        )

        for warning in all_stale:
            print(
                warning
            )

        print()

        print(
            "  Handling:"
        )

        print(
            "   param.nml -> setup_run will STOP for the affected "
            "month. Rerun gen_param first."
        )

        print(
            "   run_test/run_comb -> setup_run will automatically "
            "regenerate the deployed job card."
        )

        print(
            "   UFS executable -> setup_run will automatically "
            "recopy it into R{ID}_YYYYMM/."
        )

        print(
            "   combine executable -> setup_run will automatically "
            "recopy it into R{ID}_YYYYMM/outputs/."
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
            failed.append(
                ym
            )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    print(
        f"\n{'=' * 60}"
    )

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

        for month in failed:
            print(
                f"    {month}"
            )

    print(
        f"{'=' * 60}\n"
    )

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

    config_dir = Path(
        args.config
    )

    config = __import__(
        "workflow.core.config",
        fromlist=["load_config"],
    ).load_config(
        config_dir
    )

    run_setup_run(
        config,
        config_dir,
    )

