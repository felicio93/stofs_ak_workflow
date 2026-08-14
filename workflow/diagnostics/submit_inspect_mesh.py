"""
diagnostics/submit_inspect_mesh.py
==================================
Phase 0 launcher — submit a single SLURM job that runs
workflow.diagnostics.inspect_mesh to generate diagnostic plots for all fix/
input files.

Uses the swf_plot conda environment (called by full interpreter path).
"""

from pathlib import Path

from workflow.core.config import model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter

# SLURM templates for SCHISM live under the schism model package.
TEMPLATES_DIR = (Path(__file__).resolve().parent.parent
                 / "models" / "schism" / "templates" / "slurm")


def submit_inspect_mesh(cfg: dict, config_dir: Path) -> str:
    """Submit inspect_mesh job. Returns job ID or '' if skipped."""
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    out  = mdir / f"D{pid}" / f"D{pid}_fix"
    out.mkdir(parents=True, exist_ok=True)

    sentinel = out / "inspect_mesh.done"
    if sentinel.exists():
        print("  inspect_mesh already complete (sentinel found). Skipping.")
        return ""

    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    py = env_python(cfg, "inspect_mesh", default="swf_plot")

    slurm = cfg.get("slurm", {})
    subs  = {
        "WORKDIR":    str(mdir),
        "JOBNAME":    f"inspect_M{pid}",
        "ACCOUNT":    slurm.get("account",          "nos-surge"),
        "PARTITION":  slurm.get("partition",         "hercules-2"),
        "MEM":        slurm.get("inspect_mem",       "16G"),
        "WALLTIME":   slurm.get("inspect_walltime",  "00:30:00"),
        "LOGDIR":     str(logdir),
        "PY":         py,
        "SCRIPT":     "-m workflow.diagnostics.inspect_mesh",
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Output directory: {out}")
    print(f"  Interpreter:      {py}")
    out_str = submitter.render_and_submit("inspect_mesh.sbatch", subs,
                                          logdir / "inspect_mesh.sbatch")
    print(f"  Monitor: squeue -u $USER")
    print(f"  Log:     {logdir}/inspect_mesh.out")
    return SlurmSubmitter.parse_jobid(out_str)
