"""
diagnostics/submit_plots.py
===========================
Phase 2b launcher — submit a SLURM job array (one task per group) that runs
workflow.diagnostics.plot_hycom to generate HYCOM debug GIFs on compute
nodes, using the swf_plot conda environment (called by full interpreter path).

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).
"""

from pathlib import Path

from workflow.core.config import list_groups, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter, write_manifest

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent
    / "models" / "schism" / "templates" / "slurm"
)


def submit_plotting_jobs(cfg: dict, config_dir: Path) -> str:
    """Submit plot_hycom job array. Returns job ID or ''."""
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    ddir   = mdir / f"D{pid}"
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    groups = list_groups(cfg)

    # Skip groups whose GIFs are already complete
    pending = []
    for group_id in groups:
        sentinel = (mdir / f"D{pid}" / f"D{pid}_{group_id}"
                    / "plot_hycom.done")
        if sentinel.exists():
            print(f"  {group_id}: plot_hycom already complete, "
                  f"skipping.")
        else:
            pending.append(group_id)

    if not pending:
        print("  plot_hycom: all groups already complete. "
              "Nothing to submit.")
        return ""

    ngroups  = len(pending)
    manifest = write_manifest(
        pending, ddir / "plot_groups.manifest")

    py = env_python(cfg, "plot_hycom", default="swf_plot")

    slurm = cfg.get("slurm", {})
    subs = {
        "WORKDIR":      str(mdir),
        "JOBNAME":      f"plot_M{pid}",
        "ACCOUNT":      slurm.get("account",    "nos-surge"),
        "PARTITION":    slurm.get("partition",  "hercules-2"),
        "NGROUPS":      str(ngroups),
        "MEM_PER_TASK": slurm.get("plot_mem",   "16G"),
        "WALLTIME":     slurm.get("plot_walltime", "00:30:00"),
        "LOGDIR":       str(logdir),
        "MAILUSER":     slurm.get("mail_user",
                                  "felicio.cassalho@noaa.gov"),
        "MANIFEST":     str(manifest),
        "PY":           py,
        "PLOT_SCRIPT":  "-m workflow.diagnostics.plot_hycom",
        "CONFIG_DIR":   str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Group manifest: {manifest}  ({ngroups} groups)")
    print(f"  Interpreter:    {py}")
    print(f"  Submitting job array 1-{ngroups} ...")
    out = submitter.render_and_submit(
        "plot_hycom.sbatch", subs,
        ddir / "plot_hycom.sbatch")
    print(f"  Monitor: squeue -u $USER | Logs: {logdir}")
    return SlurmSubmitter.parse_jobid(out)
