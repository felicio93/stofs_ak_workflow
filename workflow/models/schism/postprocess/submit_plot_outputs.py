"""
models/schism/postprocess/submit_plot_outputs.py
================================================
Two-stage SLURM launcher for the full-run field GIFs (plot_outputs).

Stage 1 — frame generation (SLURM array, one task per output stack file):
    A manifest lists every (month, prefix, stack) triple needed by the
    configured variables, across all run months. Each array task renders
    the frames for one output file.

Stage 2 — GIF assembly (single serial job, --dependency=afterok on Stage 1):
    Collects the frames per variable into one GIF spanning the requested
    date range, then keeps or deletes the frames.
"""

from pathlib import Path

from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter
from workflow.models.schism.postprocess import plot_common as pc

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slurm"


def _unique_prefixes(cfg) -> list:
    """Distinct output-file prefixes referenced by the configured variables."""
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

    prefixes = _unique_prefixes(cfg)

    # Build the Stage-1 manifest: every existing (month, prefix, stack).
    tasks = []
    for ym in list_months(cfg):
        outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
        if not outputs.is_dir():
            continue
        for prefix in prefixes:
            for nc in pc.list_output_stacks(outputs, prefix):
                tasks.append(f"{ym} {prefix} {pc.stack_number(nc)}")

    if not tasks:
        print("  plot_outputs: no output files found in any R{ID}_YYYYMM/outputs/. "
              "Has the model run completed?")
        return ""

    manifest = logdir / "plot_outputs_frames.manifest"
    manifest.write_text("\n".join(tasks) + "\n")

    slurm = cfg.get("slurm", {})
    # Throttle: limit how many array tasks run concurrently to stay under the
    # QOS MaxSubmitJobsPerUser limit. The QOS cap on Hercules is 400 submitted
    # jobs; a 1000+ task array with no throttle exceeds this. %K in the SLURM
    # array spec means "at most K tasks active (queued+running) at once", so the
    # total submission stays small. Default 50 is well within the 400 cap.
    throttle = str(slurm.get("plot_outputs_array_throttle", 50))
    common = {
        "ACCOUNT":    slurm.get("account",   "nos-surge"),
        "PARTITION":  slurm.get("partition", "hercules-2"),
        "MAILUSER":   slurm.get("mail_user", "felicio.cassalho@noaa.gov"),
        "WORKDIR":    str(mdir),
        "LOGDIR":     str(logdir),
        "PY":         env_python(cfg, "plot_outputs", default="swf_plot"),
        "SCRIPT":     "-m workflow.models.schism.postprocess.plot_outputs",
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)

    # --- Stage 1: frame array ---
    stage1 = dict(common)
    stage1.update({
        "JOBNAME":        f"plotout_frm_M{pid}",
        "NTASKS":         str(len(tasks)),
        "ARRAY_THROTTLE": throttle,
        "MEM":            slurm.get("plot_outputs_mem",      "32G"),
        "WALLTIME":       slurm.get("plot_outputs_walltime", "00:30:00"),
        "MANIFEST":       str(manifest),
    })
    print(f"  Submitting plot_outputs frames: {len(tasks)} output file(s)")
    out1 = submitter.render_and_submit(
        "plot_outputs_frames.sbatch", stage1,
        logdir / "plot_outputs_frames.sbatch")
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
