from pathlib import Path
from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter, write_manifest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slurm"

def submit_gen_datm(cfg: dict, config_dir: Path, after_jobid: str = "") -> str:
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    months = list_months(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    pending = []
    for ym in months:
        out = mdir / f"I{pid}" / f"I{pid}_{ym}" / "forcing" / f"datm_{ym}.nc"
        sentinel = out.parent / "gen_datm.done"
        if sentinel.exists() and out.exists() and out.stat().st_size > 0:
            print(f"  {ym}: gen_datm already complete, skipping.")
            continue
        pending.append(ym)

    if not pending:
        print("  gen_datm: nothing to submit.")
        return ""

    manifest = write_manifest(pending, logdir / "gen_datm_months.manifest")

    dependency = f"afterok:{after_jobid}" if after_jobid else None

    slurm = cfg.get("slurm", {})
    subs = {
        "ACCOUNT":   slurm.get("account",   "nos-surge"),
        "PARTITION": slurm.get("partition", "hercules-2"),
        "MAILUSER":  slurm.get("mail_user", "felicio.cassalho@noaa.gov"),
        "WORKDIR":   str(mdir),
        "JOBNAME":   f"gendatm_M{pid}",
        "NMONTHS":   str(len(pending)),
        "MEM":       slurm.get("gen_datm_mem", "8G"),
        "WALLTIME":  slurm.get("gen_datm_walltime", "00:30:00"),
        "LOGDIR":    str(logdir),
        "MANIFEST":  str(manifest),
        "PY":        env_python(cfg, "gen_datm", default="swf_main"),
        "SCRIPT":    "-m workflow.models.ufs_schism.preprocess.gen_datm",
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_datm: {len(pending)} month(s) ({pending[0]} -> {pending[-1]})")
    out = submitter.render_and_submit("gen_datm.sbatch", subs, logdir / "gen_datm.sbatch", dependency=dependency)
    return SlurmSubmitter.parse_jobid(out)