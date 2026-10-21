"""
diagnostics/submit_plots.py
===========================
Phase 2b launcher — submit SLURM job arrays (one task per group) that run
workflow.diagnostics.plot_hycom on compute nodes, using the swf_plot conda
environment (called by full interpreter path):

  submit_plotting_jobs        HYCOM debug GIFs from the aggregated stacks
  submit_plot_hycom_outputs   verification + figures of the inputs built from
                              them (gen_hotstart / gen_3Dth / gen_nudge)

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).
"""

from pathlib import Path

from workflow.core.config import list_groups, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter, write_manifest
from workflow.core.slurm import (
    DEFAULT_ACCOUNT,
    DEFAULT_PARTITION,
    DEFAULT_MAIL_USER,
)

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent
    / "models" / "schism" / "templates" / "slurm"
)


def _submit_array(cfg: dict, config_dir: Path, *, step: str, sentinel: str,
                  jobname: str, logname: str, manifest_name: str,
                  script_args: str = "", dependency: str = None) -> str:
    """Submit a plot_hycom job array for the groups without `sentinel`.

    Returns the job ID, or '' if every group is already complete.
    """
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    ddir   = mdir / f"D{pid}"
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    groups = list_groups(cfg)

    # Skip groups whose outputs are already complete
    pending = []
    for group_id in groups:
        done = mdir / f"D{pid}" / f"D{pid}_{group_id}" / sentinel
        if done.exists():
            print(f"  {group_id}: {step} already complete, skipping.")
        else:
            pending.append(group_id)

    if not pending:
        print(f"  {step}: all groups already complete. Nothing to submit.")
        return ""

    ngroups  = len(pending)
    manifest = write_manifest(pending, ddir / manifest_name)

    py = env_python(cfg, step, default="swf_plot")

    slurm = cfg.get("slurm", {})
    subs = {
        "WORKDIR":      str(mdir),
        "JOBNAME":      f"{jobname}_M{pid}",
        "ACCOUNT":      slurm.get("account",    DEFAULT_ACCOUNT),
        "PARTITION":    slurm.get("partition",  DEFAULT_PARTITION),
        "NGROUPS":      str(ngroups),
        "MEM_PER_TASK": slurm.get("plot_mem",   "16G"),
        "WALLTIME":     slurm.get("plot_walltime", "00:30:00"),
        "LOGDIR":       str(logdir),
        "LOGNAME":      logname,
        "MAILUSER":     slurm.get("mail_user",
                                  DEFAULT_MAIL_USER),
        "MANIFEST":     str(manifest),
        "PY":           py,
        "PLOT_SCRIPT":  f"-m workflow.diagnostics.plot_hycom {script_args}".strip(),
        "CONFIG_DIR":   str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Group manifest: {manifest}  ({ngroups} groups)")
    print(f"  Interpreter:    {py}")
    if dependency:
        print(f"  Dependency:     {dependency}")
    print(f"  Submitting job array 1-{ngroups} ...")
    out = submitter.render_and_submit(
        "plot_hycom.sbatch", subs,
        ddir / f"{logname}.sbatch", dependency=dependency)
    print(f"  Monitor: squeue -u $USER | Logs: {logdir}")
    return SlurmSubmitter.parse_jobid(out)


def submit_plotting_jobs(cfg: dict, config_dir: Path) -> str:
    """Submit plot_hycom job array. Returns job ID or ''."""
    return _submit_array(
        cfg, config_dir, step="plot_hycom", sentinel="plot_hycom.done",
        jobname="plot", logname="plot_hycom",
        manifest_name="plot_groups.manifest")


def submit_plot_hycom_outputs(cfg: dict, config_dir: Path,
                              after_jobids=()) -> str:
    """Submit the verification of the HYCOM-derived inputs (job array).

    after_jobids: gen_hotstart / gen_3Dth / gen_nudge job IDs submitted in
        this invocation. The array starts once they have all ended, whatever
        their final state (afterany), so that a failed gen_* job is reported
        by the verification instead of leaving this array pending forever.
    Returns the job ID or ''.
    """
    ids = [str(j) for j in after_jobids if j]
    dependency = "afterany:" + ":".join(ids) if ids else None
    return _submit_array(
        cfg, config_dir, step="plot_hycom_outputs",
        sentinel="plot_hycom_outputs.done",
        jobname="hycomchk", logname="plot_hycom_outputs",
        manifest_name="plot_hycom_outputs_groups.manifest",
        script_args="--outputs", dependency=dependency)
