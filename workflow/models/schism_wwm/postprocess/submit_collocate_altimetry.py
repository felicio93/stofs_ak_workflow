"""
models/schism_wwm/postprocess/submit_collocate_altimetry.py
============================================================
Two-stage SLURM launcher for the satellite altimetry collocation pipeline.
Mirrors submit_collocate_argo.py exactly.

Stage 1 — SLURM array, one task per day (throttled):
    A manifest lists every day in the collocation window. Each array
    task collocates satellite altimetry Hs against SCHISM+WWM
    sigWaveHeight for its assigned day. Per-day NetCDFs written to:
        P{ID}/P{ID}_collocate_altimetry/daily/collocated_hs_{YYYYMMDD}.nc

Stage 2 — serial merge (--dependency=afterok on Stage 1):
    Concatenates all daily files into one combined NetCDF and writes
    the collocate_altimetry.done sentinel.

Re-run logic (mirrors collocate_argo):
    * collocate_altimetry.done exists -> skip entirely.
    * .daily_done exists + collocate_altimetry.done missing
      -> submit Stage 2 only.
    * Otherwise -> submit full pipeline Stage 1 + Stage 2.
"""

from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent
    / "templates" / "slurm"
)


# =============================================================================
# Helpers
# =============================================================================

def _build_day_list(cfg: dict) -> list:
    """Return every calendar day in the collocation window."""
    start = cfg.get("collocate_altimetry_start") or cfg["start_date"]
    end   = cfg.get("collocate_altimetry_end")   or cfg["end_date"]
    s = date.fromisoformat(str(start))
    e = date.fromisoformat(str(end))
    days = []
    d = s
    while d <= e:
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return days


def _out_dir(cfg: dict) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_collocate_altimetry"


# =============================================================================
# Launcher
# =============================================================================

def submit_collocate_altimetry(cfg: dict,
                                config_dir: Path) -> str:
    """Submit the two-stage altimetry collocation pipeline to SLURM.

    Returns the SLURM job ID of the last submitted job, or '' if
    nothing was submitted.
    """
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    out_dir    = _out_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)

    done_all   = out_dir / "collocate_altimetry.done"
    done_daily = out_dir / ".daily_done"

    slurm  = cfg.get("slurm", {})
    common = {
        "ACCOUNT":    slurm.get("account",   "nos-surge"),
        "PARTITION":  slurm.get("partition", "hercules-2"),
        "MAILUSER":   slurm.get("mail_user",
                                "felicio.cassalho@noaa.gov"),
        "WORKDIR":    str(mdir),
        "LOGDIR":     str(logdir),
        "PY":         env_python(cfg, "collocate_altimetry",
                                 default="swf_plot"),
        "SCRIPT":     ("-m workflow.models.schism_wwm"
                       ".postprocess.collocate_altimetry"),
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)

    # ---- Fully complete ----
    if done_all.exists():
        print("  collocate_altimetry: already complete, "
              "skipping.")
        return ""

    days = _build_day_list(cfg)
    if not days:
        print("  collocate_altimetry: empty date range. "
              "Nothing to do.")
        return ""

    # ---- Daily done, only merge missing ----
    if done_daily.exists():
        print("  collocate_altimetry: daily collocation done "
              "(.daily_done). Submitting merge only.")
        stage2 = dict(common)
        stage2.update({
            "JOBNAME":  f"alt_merge_M{pid}",
            "MEM":      slurm.get(
                "collocate_altimetry_merge_mem", "32G"),
            "WALLTIME": slurm.get(
                "collocate_altimetry_merge_walltime",
                "01:00:00"),
        })
        out2 = submitter.render_and_submit(
            "collocate_altimetry_merge.sbatch", stage2,
            logdir / "collocate_altimetry_merge.sbatch")
        return SlurmSubmitter.parse_jobid(out2)

    # ---- Full pipeline: Stage 1 + Stage 2 ----
    ntasks   = len(days)
    throttle = str(slurm.get(
        "collocate_altimetry_array_throttle", 50))

    manifest = logdir / "collocate_altimetry_days.manifest"
    manifest.write_text("\n".join(days) + "\n")

    stage1 = dict(common)
    stage1.update({
        "JOBNAME":        f"alt_day_M{pid}",
        "NTASKS":         str(ntasks),
        "ARRAY_THROTTLE": throttle,
        "MEM":            slurm.get(
            "collocate_altimetry_mem", "32G"),
        "WALLTIME":       slurm.get(
            "collocate_altimetry_walltime", "01:00:00"),
        "MANIFEST":       str(manifest),
    })
    print(f"  Submitting collocate_altimetry Stage 1 array: "
          f"{ntasks} day(s) ({days[0]} -> {days[-1]})  "
          f"throttle={throttle}")
    out1 = submitter.render_and_submit(
        "collocate_altimetry_day.sbatch", stage1,
        logdir / "collocate_altimetry_day.sbatch")
    jid1 = SlurmSubmitter.parse_jobid(out1)

    stage2 = dict(common)
    stage2.update({
        "JOBNAME":  f"alt_merge_M{pid}",
        "MEM":      slurm.get(
            "collocate_altimetry_merge_mem", "32G"),
        "WALLTIME": slurm.get(
            "collocate_altimetry_merge_walltime", "01:00:00"),
    })
    print(f"  Submitting collocate_altimetry Stage 2 merge "
          f"(afterok:{jid1})")
    out2 = submitter.render_and_submit(
        "collocate_altimetry_merge.sbatch", stage2,
        logdir / "collocate_altimetry_merge.sbatch",
        dependency=f"afterok:{jid1}")
    jid2 = SlurmSubmitter.parse_jobid(out2)

    print(f"  Monitor: squeue -u $USER | "
          f"Logs: {logdir}/alt_*.out")
    return jid2
