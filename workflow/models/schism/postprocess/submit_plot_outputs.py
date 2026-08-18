"""
models/schism/postprocess/submit_plot_outputs.py
================================================
Two-stage SLURM launcher for the full-run field GIFs (plot_outputs).

Stage 1 — MPI parallel frame generation:
    A single multi-rank SLURM job (srun -N X -n Y) where each MPI rank
    processes a subset of the output stack files. One submission regardless
    of task count — no QOSMaxSubmitJobPerUserLimit issues. The number of
    ranks equals the number of output files so each rank handles exactly one
    file. Nodes are computed automatically: ceil(ntasks / cores_per_node).

Stage 2 — GIF assembly (single serial job, --dependency=afterok on Stage 1):
    Collects the frames per variable into one GIF spanning the requested
    date range. Kept separate so the user can re-run GIF assembly with
    different parameters (fps, start/end date, cadence) without re-rendering.
"""

import math
from pathlib import Path

from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter
from workflow.models.schism.postprocess import plot_common as pc

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slurm"

# Ranks per node for MPI plotting jobs.  Plotting is memory-intensive: each
# rank reads one ~6 GB output NetCDF plus builds mesh triangulation and renders
# matplotlib frames.  Using all 80 cores per node (512 GB / 80 = 6.4 GB each)
# causes OOM kills.  Limiting to 20 ranks/node gives ~25 GB headroom per rank.
_RANKS_PER_NODE = 20


def _unique_prefixes(cfg) -> list:
    seen = []
    for v in cfg.get("plot_outputs_vars", []):
        p = pc.var_spec(v)["file_prefix"]
        if p and p not in seen:
            seen.append(p)
    return seen


def submit_plot_outputs(cfg: dict, config_dir: Path) -> str:
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    if not cfg.get("plot_outputs_vars"):
        print("  plot_outputs: no plot_outputs_vars configured. Nothing to do.")
        return ""

    # Count the total number of output files to size the MPI job.
    prefixes = _unique_prefixes(cfg)
    ntasks = 0
    for ym in list_months(cfg):
        outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
        if not outputs.is_dir():
            continue
        for prefix in prefixes:
            ntasks += len(pc.list_output_stacks(outputs, prefix))

    if ntasks == 0:
        print("  plot_outputs: no output files found. Has the model run completed?")
        return ""

    # Cap ntasks at a user-configurable maximum (default: unlimited = ntasks).
    slurm = cfg.get("slurm", {})
    max_tasks = int(slurm.get("plot_outputs_max_ntasks", ntasks))
    ntasks = min(ntasks, max_tasks)
    nnodes = math.ceil(ntasks / _RANKS_PER_NODE)

    # plot_outputs uses windfall QOS by default — 55 nodes for 1092 files
    # exceeds the debug QOS node limit. Override with slurm.plot_outputs_qos.
    common = {
        "ACCOUNT":    slurm.get("account",   "nos-surge"),
        "PARTITION":  slurm.get("plot_outputs_partition",
                                slurm.get("partition", "hercules-2")),
        "QOS":        slurm.get("plot_outputs_qos", "windfall"),
        "MAILUSER":   slurm.get("mail_user", "felicio.cassalho@noaa.gov"),
        "WORKDIR":    str(mdir),
        "LOGDIR":     str(logdir),
        "PY":         env_python(cfg, "plot_outputs", default="swf_plot"),
        "SCRIPT":     "-m workflow.models.schism.postprocess.plot_outputs",
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)

    # --- Stage 1: MPI frame generation ---
    stage1 = dict(common)
    stage1.update({
        "JOBNAME":  f"plotout_frm_M{pid}",
        "NNODES":   str(nnodes),
        "NTASKS":   str(ntasks),
        "MEM":      slurm.get("plot_outputs_mem",      "16G"),
        "WALLTIME": slurm.get("plot_outputs_walltime", "02:00:00"),
    })
    print(f"  Submitting plot_outputs MPI frames: {ntasks} rank(s) on "
          f"{nnodes} node(s)  ({ntasks} output files)")
    out1 = submitter.render_and_submit(
        "plot_outputs_mpi.sbatch", stage1,
        logdir / "plot_outputs_mpi.sbatch")
    jid1 = SlurmSubmitter.parse_jobid(out1)

    # --- Stage 2: serial GIF assembly, afterok on Stage 1 ---
    stage2 = dict(common)
    stage2.update({
        "JOBNAME":  f"plotout_gif_M{pid}",
        "MEM":      slurm.get("plot_outputs_gif_mem",      "32G"),
        "WALLTIME": slurm.get("plot_outputs_gif_walltime", "00:30:00"),
    })
    print(f"  Submitting plot_outputs GIF assembly (afterok:{jid1})")
    out2 = submitter.render_and_submit(
        "plot_outputs_gif.sbatch", stage2,
        logdir / "plot_outputs_gif.sbatch",
        dependency=f"afterok:{jid1}")
    jid2 = SlurmSubmitter.parse_jobid(out2)

    print(f"  Monitor: squeue -u $USER | Logs: {logdir}/plot_outputs_*.out")
    return jid2
