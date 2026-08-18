"""
models/schism/postprocess/submit_compare_sst.py
===============================================
Two-stage SLURM launcher for the model vs. satellite SST comparison.

Stage 1 — frame generation (SLURM array, one task per day):
    A manifest lists every day in [compare_sst_start, compare_sst_end]
    (default: the whole run). Each array task builds one two-panel
    model-vs-satellite frame for its day.

Stage 2 — GIF assembly (single serial job, --dependency=afterok):
    Stitches the daily frames into one GIF and keeps/deletes frames.
"""

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

    days = _date_range(cfg)
    if not days:
        print("  compare_sst: empty date range. Nothing to do.")
        return ""

    manifest = logdir / "compare_sst_days.manifest"
    manifest.write_text("\n".join(days) + "\n")

    slurm = cfg.get("slurm", {})
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

    # --- Stage 1: per-day frames ---
    stage1 = dict(common)
    stage1.update({
        "JOBNAME":        f"cmpsst_frm_M{pid}",
        "NTASKS":         str(len(days)),
        "ARRAY_THROTTLE": throttle,
        "MEM":            slurm.get("compare_sst_mem",      "32G"),
        "WALLTIME":       slurm.get("compare_sst_walltime", "00:30:00"),
        "MANIFEST":       str(manifest),
    })
    print(f"  Submitting compare_sst frames: {len(days)} day(s) "
          f"({days[0]} -> {days[-1]})")
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
