import re
import shutil
import stat
import sys
from pathlib import Path

from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "schism" / "templates"
AUTO_HOTSTART_TEMPLATE = TEMPLATE_DIR / "auto_hotstart.py"

FIX_LINKS = [
    "hgrid.gr3", "hgrid.ll", "vgrid.in", "partition.prop", "tvd.prop",
    "albedo.gr3", "diffmin.gr3", "diffmax.gr3", "watertype.gr3",
    "shapiro.gr3", "windrot_geo2proj.gr3", "rough.gr3",
    "estuary.gr3", "TEM_nudge.gr3", "SAL_nudge.gr3", "station.in",
]

INPUT_LINKS = [
    "bctides.in", "param.nml", "source.nc",
    "TEM_3D.th.nc", "SAL_3D.th.nc", "elev2D.th.nc", "uv3D.th.nc",
    "TEM_nu.nc", "SAL_nu.nc",
    "datm_in", "datm.streams", "fd_ufs.yaml", "noahmptable.tbl",
    "model_configure", "ufs.configure",
]

OUTPUT_PLACEHOLDERS = [f"staout_{i}" for i in range(1, 21)] + ["flux.out"]

def _link(src: Path, dst: Path):
    """Create/refresh a symlink dst -> src. Returns True if src existed."""
    if not src.exists():
        return False
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    dst.symlink_to(src)
    return True

def _set_sbatch_jobname(text: str, jobname: str) -> str:
    return re.sub(r'(#SBATCH\s+-J\s+)(\S+)', rf'\g<1>{jobname}', text)

def _set_sbatch_workdir(text: str, workdir: str) -> str:
    return re.sub(r'(#SBATCH\s+-D\s+)(\S+)', rf'\g<1>{workdir}', text)

def _render_auto_hotstart(run_dir: Path, subs: dict):
    text = AUTO_HOTSTART_TEMPLATE.read_text()
    for key, val in subs.items():
        text = text.replace("{{" + key + "}}", str(val))
    out = run_dir / "auto_hotstart.py"
    out.write_text(text)
    out.chmod(out.stat().st_mode | stat.S_IXUSR)

def run_setup_run(cfg: dict, config_dir: Path):
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)

    print(f"
{'='*60}")
    print(f"  setup_run for M{pid}")
    print(f"  {len(months)} month(s): {months[0]} -> {months[-1]}")
    print(f"{'='*60}")

    for i, ym in enumerate(months):
        rdir   = mdir / f"R{pid}" / f"R{pid}_{ym}"
        idir   = mdir / f"I{pid}" / f"I{pid}_{ym}"
        fix    = mdir / "fix"
        bind   = mdir / "bin"

        print(f"
--- setup_run {ym}  ({rdir}) ---")

        if (rdir / "setup_run.done").exists():
            print(f"  {ym}: already set up, skipping.")
            continue

        if not idir.is_dir():
            print(f"  ERROR {ym}: input dir not found: {idir}")
            return

        rdir.mkdir(parents=True, exist_ok=True)

        executable_name = cfg["executable"]
        executable_path = bind / executable_name
        if not executable_path.exists():
            print(f"  ERROR: Executable not found: {executable_path}")
            return

        for name in FIX_LINKS:
            if not _link(fix / name, rdir / name):
                print(f"  NOTE: fix/{name} not found, skipped.")

        for name in INPUT_LINKS:
            if not _link(idir / name, rdir / name):
                print(f"  ERROR {ym}: required input missing: {idir / name}")
                return

        if not _link(idir / "forcing", rdir / "forcing"):
            print(f"  ERROR {ym}: forcing dir missing: {idir / 'forcing'}")
            return

        shutil.copy2(executable_path, rdir / executable_name)

        outdir = rdir / "outputs"
        outdir.mkdir(exist_ok=True)
        for name in OUTPUT_PLACEHOLDERS:
            f = outdir / name
            if not f.exists():
                f.touch()

        run_jobname = f"R{pid}_{i + 1:02d}"
        run_test = (fix / "run_test").read_text()
        run_test = _set_sbatch_jobname(run_test, run_jobname)
        run_test = _set_sbatch_workdir(run_test, ".")
        (rdir / "run_test").write_text(run_test)

        _render_auto_hotstart(rdir, {
            "RUNDIR":              str(rdir),
            "NEXT_RUNDIR":         "None",
            "CHAIN_HOTSTART":      "False",
            "IS_LAST_MONTH":       "True",
            "NHOT_WRITE":          0,
            "MONTH":               ym,
            "RUN_JOBNAME":         run_jobname,
            "DIAG_ENABLED":        "False",
            "DIAG_SBATCH":         "",
            "DIAG_VARS_MANIFEST":  "",
            "DIAG_NVAR":           0,
        })

        (rdir / "setup_run.done").touch()
        print(f"  {ym}: run directory ready  (job {run_jobname}).")
