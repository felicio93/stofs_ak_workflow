"""
diagnostics/submit_plots.py
===========================
Phase 2b launcher — submit a SLURM job array (one task per month) that runs
workflow.diagnostics.plot_hycom to generate HYCOM debug GIFs on compute nodes,
using the swf_plot conda environment (called by full interpreter path).
"""

from pathlib import Path

from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter, write_manifest

TEMPLATES_DIR = (Path(__file__).resolve().parent.parent
                 / "models" / "schism" / "templates" / "slurm")


def submit_plotting_jobs(cfg: dict, config_dir: Path) -> str:
    """Submit plotting_debug job array. Returns job ID or ''."""
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    ddir   = mdir / f"D{pid}"
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    months  = list_months(cfg)
    nmonths = len(months)
    manifest = write_manifest(months, ddir / "plot_months.manifest")

    py = env_python(cfg, "plotting_debug", default="swf_plot")

    slurm = cfg.get("slurm", {})
    subs = {
        "WORKDIR":      str(mdir),
        "JOBNAME":      f"plot_M{pid}",
        "ACCOUNT":      slurm.get("account", "nos-surge"),
        "PARTITION":    slurm.get("partition", "hercules-2"),
        "NMONTHS":      str(nmonths),
        "MEM_PER_TASK": slurm.get("plot_mem", "16G"),
        "WALLTIME":     slurm.get("plot_walltime", "00:30:00"),
        "LOGDIR":       str(logdir),
        "MAILUSER":     slurm.get("mail_user", "felicio.cassalho@noaa.gov"),
        "MANIFEST":     str(manifest),
        "PY":           py,
        "PLOT_SCRIPT":  "-m workflow.diagnostics.plot_hycom",
        "CONFIG_DIR":   str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Month manifest: {manifest}  ({nmonths} months)")
    print(f"  Interpreter:    {py}")
    print(f"  Submitting job array 1-{nmonths} ...")
    out = submitter.render_and_submit("plot_hycom.sbatch", subs,
                                      ddir / "plot_hycom.sbatch")
    print(f"  Monitor: squeue -u $USER | Logs: {logdir}")
    from workflow.core.slurm import SlurmSubmitter as _S
    return _S.parse_jobid(out)
