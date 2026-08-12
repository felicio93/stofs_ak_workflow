"""
models/schism/postprocess/submit_plot_outputs.py
================================================
SLURM launcher for the plot_outputs step (Phase 5).

Submits one SLURM array task per pending month; each task calls
    python -m workflow.models.schism.postprocess.plot_outputs
           --config <cfg> --month YYYYMM
using the swf_plot conda environment (same as HYCOM debug plots).

Dependency chaining: if gen_sflux_jobid is provided (future use when
all phases are run together) plot_outputs waits on that job.
"""

from pathlib import Path

from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter, write_manifest

TEMPLATES_DIR = (Path(__file__).resolve().parent.parent
                 / "templates" / "slurm")


def submit_plot_outputs(cfg: dict, config_dir: Path,
                        after_jobid: str = "") -> str:
    """Submit plot_outputs array job. Returns sbatch job ID or ''."""
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    pending = []
    for ym in months:
        sentinel = mdir / f"P{pid}" / f"P{pid}_{ym}" / "plot_outputs.done"
        if sentinel.exists():
            print(f"  {ym}: plot_outputs already complete, skipping.")
        else:
            pending.append(ym)

    if not pending:
        print("  All months already complete. Nothing to submit.")
        return ""

    manifest = write_manifest(pending, logdir / "plot_outputs_months.manifest")
    dependency = f"afterok:{after_jobid}" if after_jobid else None

    slurm = cfg.get("slurm", {})
    subs  = {
        "ACCOUNT":    slurm.get("account",              "nos-surge"),
        "PARTITION":  slurm.get("partition",             "hercules-2"),
        "MAILUSER":   slurm.get("mail_user",             "felicio.cassalho@noaa.gov"),
        "JOBNAME":    f"plotout_M{pid}",
        "WORKDIR":    str(mdir),
        "NMONTHS":    str(len(pending)),
        "MEM":        slurm.get("plot_outputs_mem",      "32G"),
        "WALLTIME":   slurm.get("plot_outputs_walltime", "02:00:00"),
        "LOGDIR":     str(logdir),
        "MANIFEST":   str(manifest),
        "PY":         env_python(cfg, "plot_outputs", default="swf_plot"),
        "SCRIPT":     "-m workflow.models.schism.postprocess.plot_outputs",
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting plot_outputs: {len(pending)} month(s) "
          f"({pending[0]} -> {pending[-1]})")
    if dependency:
        print(f"  Will start after job {after_jobid}.")
    out = submitter.render_and_submit(
        "plot_outputs.sbatch", subs,
        logdir / "plot_outputs.sbatch",
        dependency=dependency,
    )
    print(f"  Monitor: squeue -u $USER | Logs: {logdir}/plot_outputs_*.out")
    from workflow.core.slurm import SlurmSubmitter as _S
    return _S.parse_jobid(out)
