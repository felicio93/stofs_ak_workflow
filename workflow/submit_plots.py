"""
submit_plots.py
===============
Step 3 launcher (runs on the interactive/login node in swf_main).

Renders the SLURM job-array template and submits it. Each array task plots one
month's HYCOM debug GIFs on a compute node (no internet needed), using the
swf_plot conda environment (called by full interpreter path).

Design: one array task per month, 1 core each (plotting is serial). Jobs are
independent and individually re-submittable.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from workflow.config import list_months, model_dir


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "templates" / "slurm" / "plot_hycom.sbatch"


def conda_python(cfg: dict, step: str) -> str:
    """
    Resolve the full path to the Python interpreter for a given step's
    configured conda env. Special-cases the base env.
    """
    conda_base = Path(cfg["conda_base"])
    env = cfg.get("conda_envs", {}).get(step)
    if not env:
        print(f"ERROR: no conda_envs entry for '{step}' in envs.yaml")
        sys.exit(1)
    if env == "base":
        return str(conda_base / "bin" / "python")
    return str(conda_base / "envs" / env / "bin" / "python")


def submit_plotting_jobs(cfg: dict, config_dir: Path):
    if shutil.which("sbatch") is None:
        print("ERROR: sbatch not found. Submit plotting jobs from a node with SLURM.")
        sys.exit(1)

    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    ddir = mdir / f"D{pid}"
    logdir = ddir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    months = list_months(cfg)
    nmonths = len(months)

    # Write the month manifest (array index -> month), one per line.
    manifest = ddir / "plot_months.manifest"
    manifest.write_text("\n".join(months) + "\n")

    py = conda_python(cfg, "plotting_debug")
    plot_script = REPO_ROOT / "workflow" / "plot_hycom.py"

    slurm = cfg.get("slurm", {})
    subs = {
        "WORKDIR":     str(mdir),
        "JOBNAME":     f"plot_M{pid}",
        "ACCOUNT":     slurm.get("account", "nos-surge"),
        "PARTITION":   slurm.get("partition", "hercules-2"),
        "NMONTHS":     str(nmonths),
        "WALLTIME":    slurm.get("plot_walltime", "00:30:00"),
        "LOGDIR":      str(logdir),
        "MAILUSER":    slurm.get("mail_user", "felicio.cassalho@noaa.gov"),
        "MANIFEST":    str(manifest),
        "PY":          py,
        "PLOT_SCRIPT": str(plot_script),
        "CONFIG_DIR":  str(config_dir),
    }

    template = TEMPLATE.read_text()
    for key, val in subs.items():
        template = template.replace("{{" + key + "}}", val)

    rendered = ddir / "plot_hycom.sbatch"
    rendered.write_text(template)

    print(f"  Rendered SLURM script: {rendered}")
    print(f"  Month manifest:        {manifest}  ({nmonths} months)")
    print(f"  Interpreter:           {py}")
    print(f"  Submitting job array 1-{nmonths} ...")

    result = subprocess.run(["sbatch", str(rendered)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: sbatch failed: {result.stderr.strip()}")
        sys.exit(1)
    print(f"  {result.stdout.strip()}")
    print(f"  Monitor with: squeue -u $USER")
    print(f"  Logs in: {logdir}")
