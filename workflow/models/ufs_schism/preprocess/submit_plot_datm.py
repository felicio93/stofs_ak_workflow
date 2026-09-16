"""
models/ufs_schism/preprocess/submit_plot_datm.py
================================================
SLURM launcher for plot_datm (DATM forcing debug GIFs).
One array task per group.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).
"""

from pathlib import Path

from workflow.core.config import (
    list_groups,
    model_dir,
)
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter, write_manifest

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent
    / "templates" / "slurm"
)


def submit_plot_datm(cfg: dict, config_dir: Path,
                     after_jobid: str = "") -> str:
    """Submit plot_datm array job.

    after_jobid: if non-empty, submitted with
        --dependency=afterok:<after_jobid>
    Returns the sbatch job ID string, or '' if nothing to submit.
    """
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    groups = list_groups(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    pending = []
    for group_id in groups:
        sentinel = (mdir / f"D{pid}" / f"D{pid}_{group_id}"
                    / "plot_datm.done")
        if sentinel.exists():
            print(f"  {group_id}: plot_datm already complete, "
                  f"skipping.")
            continue
        pending.append(group_id)

    if not pending:
        print("  plot_datm: nothing to submit.")
        return ""

    manifest = write_manifest(
        pending, logdir / "plot_datm_groups.manifest")

    dependency = (f"afterok:{after_jobid}"
                  if after_jobid else None)
    if dependency:
        print(f"  plot_datm will start after job "
              f"{after_jobid} completes.")

    slurm = cfg.get("slurm", {})
    subs  = {
        "ACCOUNT":    slurm.get("account",   "nos-surge"),
        "PARTITION":  slurm.get("partition", "hercules-2"),
        "MAILUSER":   slurm.get("mail_user",
                                "felicio.cassalho@noaa.gov"),
        "WORKDIR":    str(mdir),
        "JOBNAME":    f"plotdatm_M{pid}",
        "NGROUPS":    str(len(pending)),
        "MEM":        slurm.get("plot_datm_mem",      "8G"),
        "WALLTIME":   slurm.get("plot_datm_walltime", "00:30:00"),
        "LOGDIR":     str(logdir),
        "MANIFEST":   str(manifest),
        "PY":         env_python(cfg, "plot_datm",
                                 default="swf_plot"),
        "SCRIPT":     "-m workflow.diagnostics.plot_datm",
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting plot_datm: {len(pending)} group(s) "
          f"({pending[0]} -> {pending[-1]})")
    out = submitter.render_and_submit(
        "plot_datm.sbatch", subs,
        logdir / "plot_datm.sbatch",
        dependency=dependency)
    print(f"  Monitor: squeue -u $USER | "
          f"Logs: {logdir}/plot_datm_*.out")
    return SlurmSubmitter.parse_jobid(out)
