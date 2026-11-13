"""
models/schism_mice/postprocess/submit_compare_seaice.py
=======================================================
Submit compare_seaice: Stage 1 a SLURM array over the days (each task
compares one day or a few consecutive days, so that at most
compare_seaice_max_tasks tasks are queued), Stage 2 one job (afterok) for
the GIF, the statistics CSV and the time series.

Skipped when compare_seaice.done exists (move it aside to redo).
"""

from pathlib import Path

from workflow.core.config import model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import (
    DEFAULT_ACCOUNT, DEFAULT_MAIL_USER, DEFAULT_PARTITION, SlurmSubmitter,
)
from workflow.models.schism_mice.postprocess.download_seaice import (
    date_range, local_file,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates" / "slurm"


def submit_compare_seaice(cfg: dict, config_dir: Path) -> str:
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    odir   = mdir / f"P{pid}" / f"P{pid}_compare_seaice"
    if (odir / "compare_seaice.done").exists():
        print("  compare_seaice: compare_seaice.done exists — already "
              "complete, skipping.")
        return ""

    days = [d for d in date_range(cfg) if local_file(cfg, d) is not None]
    if not days:
        print("  compare_seaice: no NSIDC files for the period; run "
              "download_seaice (DTN) first.")
        return ""
    odir.mkdir(parents=True, exist_ok=True)

    slurm     = cfg.get("slurm", {})
    max_tasks = max(1, int(slurm.get("compare_seaice_max_tasks", 200)))
    per       = -(-len(days) // max_tasks)
    names     = [d.strftime("%Y%m%d") for d in days]
    lines     = [" ".join(names[i:i + per]) for i in range(0, len(names), per)]
    manifest  = logdir / "compare_seaice_days.manifest"
    manifest.write_text("\n".join(lines) + "\n")
    walltime  = slurm.get("compare_seaice_walltime")
    if not walltime:
        mins = 20 + 10 * per
        walltime = f"{mins // 60:02d}:{mins % 60:02d}:00"

    common = {
        "ACCOUNT":    slurm.get("account",   DEFAULT_ACCOUNT),
        "PARTITION":  slurm.get("partition", DEFAULT_PARTITION),
        "MAILUSER":   slurm.get("mail_user", DEFAULT_MAIL_USER),
        "WORKDIR":    str(mdir),
        "LOGDIR":     str(logdir),
        "PY":         env_python(cfg, "compare_seaice", default="swf_plot"),
        "SCRIPT":     "-m workflow.models.schism_mice.postprocess.compare_seaice",
        "CONFIG_DIR": str(config_dir),
    }
    sub = SlurmSubmitter(TEMPLATES_DIR)

    stage1 = dict(common)
    stage1.update({
        "JOBNAME":        f"cmpice_frm_M{pid}",
        "NTASKS":         str(len(lines)),
        "ARRAY_THROTTLE": str(slurm.get("compare_seaice_array_throttle", 50)),
        "MEM":            slurm.get("compare_seaice_mem", "32G"),
        "WALLTIME":       walltime,
        "MANIFEST":       str(manifest),
    })
    print(f"  Submitting compare_seaice: {len(days)} day(s) ({days[0]} -> "
          f"{days[-1]}) in {len(lines)} task(s) of up to {per} day(s), "
          f"walltime {walltime}")
    jid1 = SlurmSubmitter.parse_jobid(sub.render_and_submit(
        "compare_seaice_frames.sbatch", stage1,
        logdir / "compare_seaice_frames.sbatch"))

    stage2 = dict(common)
    stage2.update({
        "JOBNAME":  f"cmpice_gif_M{pid}",
        "MEM":      slurm.get("compare_seaice_gif_mem",      "16G"),
        "WALLTIME": slurm.get("compare_seaice_gif_walltime", "00:30:00"),
    })
    print(f"  Submitting compare_seaice GIF/statistics (afterok:{jid1})")
    jid2 = SlurmSubmitter.parse_jobid(sub.render_and_submit(
        "compare_seaice_gif.sbatch", stage2,
        logdir / "compare_seaice_gif.sbatch", dependency=f"afterok:{jid1}"))
    print(f"  Monitor: squeue -u $USER | Logs: {logdir}/compare_seaice_*.out")
    return jid2
