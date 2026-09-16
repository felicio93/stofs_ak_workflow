"""
models/ufs_schism/preprocess/submit_esmf_mesh.py
================================================
SLURM launcher for gen_esmf_mesh (DATM grid -> ESMF mesh NetCDF).
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


def submit_gen_esmf_mesh(cfg: dict, config_dir: Path,
                         after_jobid: str = "") -> str:
    """Submit gen_esmf_mesh array job.

    after_jobid: if non-empty, submitted with
        --dependency=afterok:<after_jobid>
    Returns the sbatch job ID string, or '' if nothing to submit.
    """
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    groups = list_groups(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    subdir = str(cfg.get("datm_subdir", "forcing"))

    pending = []
    for group_id in groups:
        datm_dir = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                    / subdir)
        sentinel = datm_dir / "gen_esmf_mesh.done"

        if sentinel.exists():
            print(f"  {group_id}: gen_esmf_mesh already "
                  f"complete, skipping.")
            continue

        # If no dependency job provided, check that DATM is ready
        if not after_jobid:
            datm_ready = datm_dir / "gen_datm.done"
            if not datm_ready.exists():
                print(f"  {group_id}: DATM file not ready "
                      f"(missing gen_datm.done), skipping.")
                continue

        pending.append(group_id)

    if not pending:
        print("  gen_esmf_mesh: nothing to submit.")
        return ""

    manifest = write_manifest(
        pending, logdir / "gen_esmf_mesh_groups.manifest")

    dependency = (f"afterok:{after_jobid}"
                  if after_jobid else None)
    if dependency:
        print(f"  gen_esmf_mesh will start after job "
              f"{after_jobid} completes.")

    slurm = cfg.get("slurm", {})
    subs  = {
        "ACCOUNT":    slurm.get("account",   "nos-surge"),
        "PARTITION":  slurm.get("partition", "hercules-2"),
        "MAILUSER":   slurm.get("mail_user",
                                "felicio.cassalho@noaa.gov"),
        "WORKDIR":    str(mdir),
        "JOBNAME":    f"genmesh_M{pid}",
        "NGROUPS":    str(len(pending)),
        "MEM":        slurm.get("gen_esmf_mesh_mem",
                                "8G"),
        "WALLTIME":   slurm.get("gen_esmf_mesh_walltime",
                                "00:10:00"),
        "LOGDIR":     str(logdir),
        "MANIFEST":   str(manifest),
        "PY":         env_python(cfg, "gen_esmf_mesh",
                                 default="swf_main"),
        "SCRIPT":     ("-m workflow.models.ufs_schism"
                       ".preprocess.gen_esmf_mesh"),
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_esmf_mesh: {len(pending)} "
          f"group(s) ({pending[0]} -> {pending[-1]})")
    out = submitter.render_and_submit(
        "gen_esmf_mesh.sbatch", subs,
        logdir / "gen_esmf_mesh.sbatch",
        dependency=dependency)
    print(f"  Monitor: squeue -u $USER | "
          f"Logs: {logdir}/gen_esmf_mesh_*.out")
    return SlurmSubmitter.parse_jobid(out)
