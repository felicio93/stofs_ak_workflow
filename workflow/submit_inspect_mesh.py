"""
submit_inspect_mesh.py
======================
Step 0 launcher — submit a single SLURM job that runs inspect_mesh.py
to generate diagnostic plots for all fix/ input files.

Uses the swf_plot conda environment (called by full path — no module loads
needed since matplotlib/cartopy/numpy are pure Python packages).
"""

import shutil
import subprocess
import sys
from pathlib import Path

from workflow.config import model_dir

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE  = REPO_ROOT / "templates" / "slurm" / "inspect_mesh.sbatch"


def submit_inspect_mesh(cfg: dict, config_dir: Path):
    if shutil.which("sbatch") is None:
        print("ERROR: sbatch not found. Run from a login node.")
        sys.exit(1)

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    out  = mdir / f"D{pid}" / f"D{pid}_fix"
    out.mkdir(parents=True, exist_ok=True)

    sentinel = out / "inspect_mesh.done"
    if sentinel.exists():
        print(f"  inspect_mesh already complete (sentinel found). Skipping.")
        return

    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    conda_base = cfg.get("conda_base", "")
    env_name   = cfg.get("conda_envs", {}).get("inspect_mesh", "swf_plot")
    py = (Path(conda_base) / "envs" / env_name / "bin" / "python"
          if env_name != "base"
          else Path(conda_base) / "bin" / "python")

    slurm = cfg.get("slurm", {})
    subs  = {
        "WORKDIR":    str(mdir),
        "JOBNAME":    f"inspect_M{pid}",
        "ACCOUNT":    slurm.get("account",          "nos-surge"),
        "PARTITION":  slurm.get("partition",         "hercules-2"),
        "MEM":        slurm.get("inspect_mem",       "16G"),
        "WALLTIME":   slurm.get("inspect_walltime",  "00:30:00"),
        "LOGDIR":     str(logdir),
        "PY":         str(py),
        "SCRIPT":     str(REPO_ROOT / "workflow" / "inspect_mesh.py"),
        "CONFIG_DIR": str(config_dir),
    }

    text = TEMPLATE.read_text()
    for key, val in subs.items():
        text = text.replace("{{" + key + "}}", str(val))

    rendered = logdir / "inspect_mesh.sbatch"
    rendered.write_text(text)

    print(f"  Rendered SLURM script: {rendered}")
    print(f"  Output directory:      {out}")
    print(f"  Interpreter:           {py}")
    print(f"  Submitting ...")

    result = subprocess.run(["sbatch", str(rendered)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: sbatch failed: {result.stderr.strip()}")
        sys.exit(1)
    print(f"  {result.stdout.strip()}")
    print(f"  Monitor: squeue -u $USER")
    print(f"  Log:     {logdir}/inspect_mesh.out")
