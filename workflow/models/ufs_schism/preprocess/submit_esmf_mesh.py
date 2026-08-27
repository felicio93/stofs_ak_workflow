from pathlib import Path
from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter, write_manifest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slurm"

def submit_gen_esmf_mesh(cfg: dict, config_dir: Path, after_jobid: str = "") -> str:
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    months = list_months(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    pending = []
    for ym in months:
        datm_subdir = cfg.get("datm_subdir", "forcing")
        datm_dir = mdir / f"I{pid}" / f"I{pid}_{ym}" / datm_subdir
        sentinel = datm_dir / "gen_esmf_mesh.done"

        if sentinel.exists():
            print(f"  {ym}: gen_esmf_mesh already complete, skipping.")
            continue

        if not after_jobid:
            datm_ready = datm_dir / "gen_datm.done"
            if not datm_ready.exists():
                print(f"  {ym}: DATM file not ready (missing gen_datm.done), skipping.")
                continue
        
        pending.append(ym)

    if not pending:
        print("  gen_esmf_mesh: nothing to submit.")
        return ""

    manifest = write_manifest(pending, logdir / "gen_esmf_mesh_months.manifest")

    dependency = f"afterok:{after_jobid}" if after_jobid else None

    slurm = cfg.get("slurm", {})
    subs = {
        "ACCOUNT":   slurm.get("account",   "nos-surge"),
        "PARTITION": slurm.get("partition", "hercules-2"),
        "MAILUSER":  slurm.get("mail_user", "felicio.cassalho@noaa.gov"),
        "WORKDIR":   str(mdir),
        "JOBNAME":   f"genmesh_M{pid}",
        "NMONTHS":   str(len(pending)),
        "MEM":       slurm.get("gen_esmf_mesh_mem", "8G"),
        "WALLTIME":  slurm.get("gen_esmf_mesh_walltime", "00:10:00"),
        "LOGDIR":    str(logdir),
        "MANIFEST":  str(manifest),
        "PY":        env_python(cfg, "gen_esmf_mesh", default="swf_main"),
        "SCRIPT":    "-m workflow.models.ufs_schism.preprocess.gen_esmf_mesh",
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_esmf_mesh: {len(pending)} month(s) ({pending[0]} -> {pending[-1]})")
    out = submitter.render_and_submit("gen_esmf_mesh.sbatch", subs, logdir / "gen_esmf_mesh.sbatch", dependency=dependency)
    return SlurmSubmitter.parse_jobid(out)