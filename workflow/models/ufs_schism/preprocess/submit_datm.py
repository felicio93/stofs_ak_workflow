"""
models/ufs_schism/preprocess/submit_datm.py
===========================================
SLURM launcher for gen_datm (sflux -> CDEPS DATM forcing NetCDF).
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


def submit_gen_datm(cfg: dict, config_dir: Path,
                    after_jobid: str = "") -> str:
    """Submit gen_datm array job.

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
    tmpl   = str(cfg.get("datm_filename_template",
                          "datm_{YYYYMM}.nc"))

    pending = []
    for group_id in groups:
        name     = tmpl.replace("{YYYYMM}", group_id)
        out      = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                    / subdir / name)
        sentinel = out.parent / "gen_datm.done"

        if (sentinel.exists()
                and out.exists()
                and out.stat().st_size > 0):
            print(f"  {group_id}: gen_datm already complete, "
                  f"skipping.")
            continue
        pending.append(group_id)

    if not pending:
        print("  gen_datm: nothing to submit.")
        return ""

    manifest = write_manifest(
        pending, logdir / "gen_datm_groups.manifest")

    dependency = (f"afterok:{after_jobid}"
                  if after_jobid else None)
    if dependency:
        print(f"  gen_datm will start after job "
              f"{after_jobid} completes.")

    slurm = cfg.get("slurm", {})
    subs  = {
        "ACCOUNT":    slurm.get("account",   "nos-surge"),
        "PARTITION":  slurm.get("partition", "hercules-2"),
        "MAILUSER":   slurm.get("mail_user",
                                "felicio.cassalho@noaa.gov"),
        "WORKDIR":    str(mdir),
        "JOBNAME":    f"gendatm_M{pid}",
        "NGROUPS":    str(len(pending)),
        "MEM":        slurm.get("gen_datm_mem",      "8G"),
        "WALLTIME":   slurm.get("gen_datm_walltime", "00:30:00"),
        "LOGDIR":     str(logdir),
        "MANIFEST":   str(manifest),
        "PY":         env_python(cfg, "gen_datm",
                                 default="swf_main"),
        "SCRIPT":     ("-m workflow.models.ufs_schism"
                       ".preprocess.gen_datm"),
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_datm: {len(pending)} group(s) "
          f"({pending[0]} -> {pending[-1]})")
    out = submitter.render_and_submit(
        "gen_datm.sbatch", subs,
        logdir / "gen_datm.sbatch",
        dependency=dependency)
    print(f"  Monitor: squeue -u $USER | "
          f"Logs: {logdir}/gen_datm_*.out")
    return SlurmSubmitter.parse_jobid(out)
