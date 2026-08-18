"""
models/schism/postprocess/submit_compare_sst.py
===============================================
Two-stage SLURM launcher for the model vs. satellite SST comparison.

Stage 1 — MPI parallel frame generation:
    A single multi-rank SLURM job (srun -N X -n Y) where each MPI rank
    processes a subset of the comparison days. One rank per day; nodes
    computed automatically: ceil(ndays / cores_per_node).

Stage 2 — GIF assembly (single serial job, --dependency=afterok):
    Stitches the daily frames into one GIF. Kept separate so the user can
    re-run with different parameters without re-rendering frames.
"""

import math
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slurm"

# Ranks per node for MPI plotting jobs — limited to 20 to give each rank
# sufficient memory headroom (~25 GB on a 512 GB node).
_RANKS_PER_NODE = 20


def _date_range(cfg):
    start = cfg.get("compare_sst_start") or cfg["start_date"]
    end   = cfg.get("compare_sst_end")   or cfg["end_date"]
    s = date.fromisoformat(str(start))
    e = date.fromisoformat(str(end))
    out = []
    d = s
    while d <= e:
        out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def submit_compare_sst(cfg: dict, config_dir: Path) -> str:
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    days = _date_range(cfg)
    if not days:
        print("  compare_sst: empty date range. Nothing to do.")
        return ""

    slurm = cfg.get("slurm", {})
    ntasks = len(days)
    max_tasks = int(slurm.get("compare_sst_max_ntasks", ntasks))
    ntasks = min(ntasks, max_tasks)
    nnodes = math.ceil(ntasks / _RANKS_PER_NODE)

    common = {
        "ACCOUNT":    slurm.get("account",   "nos-surge"),
        "PARTITION":  slurm.get("partition", "hercules-2"),
        "MAILUSER":   slurm.get("mail_user", "felicio.cassalho@noaa.gov"),
        "WORKDIR":    str(mdir),
        "LOGDIR":     str(logdir),
        "PY":         env_python(cfg, "compare_sst", default="swf_plot"),
        "SCRIPT":     "-m workflow.models.schism.postprocess.compare_sst",
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)

    # --- Stage 1: MPI frame generation ---
    stage1 = dict(common)
    stage1.update({
        "JOBNAME":  f"cmpsst_frm_M{pid}",
        "NNODES":   str(nnodes),
        "NTASKS":   str(ntasks),
        "MEM":      slurm.get("compare_sst_mem",      "8G"),
        "WALLTIME": slurm.get("compare_sst_walltime", "01:00:00"),
    })
    print(f"  Submitting compare_sst MPI frames: {ntasks} rank(s) on "
          f"{nnodes} node(s)  ({len(days)} day(s): {days[0]} -> {days[-1]})")
    out1 = submitter.render_and_submit(
        "compare_sst_mpi.sbatch", stage1,
        logdir / "compare_sst_mpi.sbatch")
    jid1 = SlurmSubmitter.parse_jobid(out1)

    # --- Stage 2: serial GIF assembly ---
    stage2 = dict(common)
    stage2.update({
        "JOBNAME":  f"cmpsst_gif_M{pid}",
        "MEM":      slurm.get("compare_sst_gif_mem",      "16G"),
        "WALLTIME": slurm.get("compare_sst_gif_walltime", "00:30:00"),
    })
    print(f"  Submitting compare_sst GIF assembly (afterok:{jid1})")
    out2 = submitter.render_and_submit(
        "compare_sst_gif.sbatch", stage2,
        logdir / "compare_sst_gif.sbatch",
        dependency=f"afterok:{jid1}")
    jid2 = SlurmSubmitter.parse_jobid(out2)

    print(f"  Monitor: squeue -u $USER | Logs: {logdir}/compare_sst_*.out")
    return jid2
