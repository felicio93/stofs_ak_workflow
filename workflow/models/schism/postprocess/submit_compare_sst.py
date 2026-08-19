"""
models/schism/postprocess/submit_compare_sst.py
===============================================
Two-stage SLURM launcher for the model vs. satellite SST comparison.

Stage 1 — SLURM array, one task per day (throttled):
    A manifest lists every day in [compare_sst_start, compare_sst_end]
    (default: the whole run). Each array task renders one two-panel
    model-vs-satellite frame for its day. Using a throttled array
    (--array=1-N%K) avoids MPI entirely and stays within the QOS
    MaxSubmitJobsPerUser limit.

Stage 2 — GIF assembly (single serial job, --dependency=afterok):
    Stitches the daily frames into one GIF and keeps/deletes frames.
    Kept separate so the user can re-run with different parameters
    (fps, date range, etc.) without re-rendering frames.
"""

import math
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slurm"


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

    gif_dir     = mdir / f"P{pid}" / f"P{pid}_compare_sst"
    done_gif    = gif_dir / "compare_sst.done"
    done_frames = gif_dir / ".frames_done"

    # --- Skip entirely if GIFs are already assembled ---
    if done_gif.exists():
        print("  compare_sst: compare_sst.done exists — already complete, skipping.")
        return ""

    days = _date_range(cfg)
    if not days:
        print("  compare_sst: empty date range. Nothing to do.")
        return ""

    slurm = cfg.get("slurm", {})

    # --- If frames are done but GIF is missing, submit only GIF assembly ---
    if done_frames.exists():
        print("  compare_sst: frames already complete (.frames_done exists). "
              "Submitting GIF assembly only.")
        gif_dir.mkdir(parents=True, exist_ok=True)
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
        stage2 = dict(common)
        stage2.update({
            "JOBNAME":  f"cmpsst_gif_M{pid}",
            "MEM":      slurm.get("compare_sst_gif_mem",      "16G"),
            "WALLTIME": slurm.get("compare_sst_gif_walltime", "00:30:00"),
        })
        submitter = SlurmSubmitter(TEMPLATES_DIR)
        print("  Submitting compare_sst GIF assembly (frames already done)")
        out2 = submitter.render_and_submit(
            "compare_sst_gif.sbatch", stage2,
            logdir / "compare_sst_gif.sbatch")
        return SlurmSubmitter.parse_jobid(out2)

    # --- Full pipeline: SLURM array for frames + GIF assembly ---
    # Write day manifest for the array.
    manifest = logdir / "compare_sst_days.manifest"
    manifest.write_text("\n".join(days) + "\n")

    # Throttle keeps active tasks ≤ limit to stay within QOS cap (400).
    ntasks   = len(days)
    throttle = str(slurm.get("compare_sst_array_throttle", 50))

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

    # --- Stage 1: per-day array frames ---
    stage1 = dict(common)
    stage1.update({
        "JOBNAME":        f"cmpsst_frm_M{pid}",
        "NTASKS":         str(ntasks),
        "ARRAY_THROTTLE": throttle,
        "MEM":            slurm.get("compare_sst_mem",      "24G"),
        "WALLTIME":       slurm.get("compare_sst_walltime", "00:30:00"),
        "MANIFEST":       str(manifest),
    })
    print(f"  Submitting compare_sst frames: {ntasks} day(s) "
          f"({days[0]} -> {days[-1]})  throttle={throttle}")
    out1 = submitter.render_and_submit(
        "compare_sst_frames.sbatch", stage1,
        logdir / "compare_sst_frames.sbatch")
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
