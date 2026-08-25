from pathlib import Path
from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter, write_manifest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slurm"

def submit_plot_datm(cfg: dict, config_dir: Path, after_jobid: str = "") -> str:
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    months = list_months(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    pending = []
    for ym in months:
        sentinel = mdir / f"D{pid}" / f"D{pid}_{ym}" / "plot_datm.done"
        if sentinel.exists():
            print(f"  {ym}: plot_datm already complete, skipping.")
            continue
        pending.append(ym)

    if not pending:
        print("  plot_datm: nothing to submit.")
        return ""

    manifest = write_manifest(pending, logdir / "plot_datm_months.manifest")

    dependency = f"afterok:{after_jobid}" if after_jobid else None

    slurm = cfg.get("slurm", {})
    subs = {
        "ACCOUNT":   slurm.get("account",   "nos-surge"),
        "PARTITION": slurm.get("partition", "hercules-2"),
        "MAILUSER":  slurm.get("mail_user", "felicio.cassalho@noaa.gov"),
        "WORKDIR":   str(mdir),
        "JOBNAME":   f"plotdatm_M{pid}",
        "NMONTHS":   str(len(pending)),
        "MEM":       slurm.get("plot_datm_mem", "8G"),
        "WALLTIME":  slurm.get("plot_datm_walltime", "00:30:00"),
        "LOGDIR":    str(logdir),
        "MANIFEST":  str(manifest),
        "PY":        env_python(cfg, "plot_datm", default="swf_plot"),
        "SCRIPT":    "-m workflow.diagnostics.plot_datm",
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting plot_datm: {len(pending)} month(s) ({pending[0]} -> {pending[-1]})")
    out = submitter.render_and_submit("plot_datm.sbatch", subs, logdir / "plot_datm.sbatch", dependency=dependency)
    return SlurmSubmitter.parse_jobid(out)