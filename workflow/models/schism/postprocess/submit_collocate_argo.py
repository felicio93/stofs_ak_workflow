"""
models/schism/postprocess/submit_collocate_argo.py
===================================================
Two-stage SLURM launcher for the parallel Argo collocation step.

Stage 1 — SLURM array, one task per day (throttled):
    A manifest lists every day in the collocation window. Each array task
    collocates ALL configured variables (temperature + salinity) for its
    assigned day, reusing the 2.6 M-node KDTree built once per task.
    Per-day per-variable NetCDFs are written to:
        P{ID}/P{ID}_collocate_argo/daily/collocated_{var}_{YYYYMMDD}.nc
    The throttle cap (--array=1-N%K) keeps active tasks within the QOS
    MaxSubmitJobsPerUser limit.

Stage 2 — serial merge (--dependency=afterok on Stage 1):
    Concatenates all daily files per variable into one combined NetCDF,
    writes the distance-filtered clean file, and touches collocate_argo.done.
    Kept separate so the user can re-run just the merge with different
    threshold settings without repeating the collocation.

Re-run logic:
    * If collocate_argo.done exists → skip entirely.
    * If .daily_done exists but collocate_argo.done is missing → submit only
      the merge job (Stage 2).
    * Otherwise → submit full pipeline (Stage 1 + Stage 2).
"""

from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slurm"


def _build_day_list(cfg) -> list:
    start = cfg.get("collocate_argo_start") or cfg["start_date"]
    end   = cfg.get("collocate_argo_end")   or cfg["end_date"]
    s = date.fromisoformat(str(start))
    e = date.fromisoformat(str(end))
    days = []
    d = s
    while d <= e:
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return days


def submit_collocate_argo(cfg: dict, config_dir: Path) -> str:
    """Submit the two-stage Argo collocation to SLURM.

    Returns the SLURM job ID of the last submitted job (merge job), or ''
    if nothing was submitted.
    """
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    out_dir    = mdir / f"P{pid}" / f"P{pid}_collocate_argo"
    out_dir.mkdir(parents=True, exist_ok=True)

    done_all    = out_dir / "collocate_argo.done"
    done_daily  = out_dir / ".daily_done"

    # Already fully complete.
    if done_all.exists():
        print("  collocate_argo: collocate_argo.done exists — already complete, "
              "skipping.")
        return ""

    days = _build_day_list(cfg)
    if not days:
        print("  collocate_argo: empty date range. Nothing to do.")
        return ""

    slurm = cfg.get("slurm", {})
    common = {
        "ACCOUNT":    slurm.get("account",   "nos-surge"),
        "PARTITION":  slurm.get("partition", "hercules-2"),
        "MAILUSER":   slurm.get("mail_user", "felicio.cassalho@noaa.gov"),
        "WORKDIR":    str(mdir),
        "LOGDIR":     str(logdir),
        "PY":         env_python(cfg, "collocate_argo", default="swf_plot"),
        "SCRIPT":     "-m workflow.models.schism.postprocess.collocate_argo",
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)

    # ---- Stage 2 only: daily files done, merge missing ----
    if done_daily.exists():
        print("  collocate_argo: daily collocation already complete "
              "(.daily_done exists). Submitting merge only.")
        stage2 = dict(common)
        stage2.update({
            "JOBNAME":  f"argo_merge_M{pid}",
            "MEM":      slurm.get("collocate_argo_merge_mem",      "32G"),
            "WALLTIME": slurm.get("collocate_argo_merge_walltime", "01:00:00"),
        })
        out2 = submitter.render_and_submit(
            "collocate_argo_merge.sbatch", stage2,
            logdir / "collocate_argo_merge.sbatch")
        return SlurmSubmitter.parse_jobid(out2)

    # ---- Full pipeline: Stage 1 array + Stage 2 merge ----
    ntasks   = len(days)
    throttle = str(slurm.get("collocate_argo_array_throttle", 50))

    manifest = logdir / "collocate_argo_days.manifest"
    manifest.write_text("\n".join(days) + "\n")

    stage1 = dict(common)
    stage1.update({
        "JOBNAME":        f"argo_day_M{pid}",
        "NTASKS":         str(ntasks),
        "ARRAY_THROTTLE": throttle,
        "MEM":            slurm.get("collocate_argo_mem",      "48G"),
        "WALLTIME":       slurm.get("collocate_argo_walltime", "01:00:00"),
        "MANIFEST":       str(manifest),
    })
    print(f"  Submitting collocate_argo Stage 1 array: {ntasks} day(s) "
          f"({days[0]} -> {days[-1]})  throttle={throttle}")
    out1 = submitter.render_and_submit(
        "collocate_argo_day.sbatch", stage1,
        logdir / "collocate_argo_day.sbatch")
    jid1 = SlurmSubmitter.parse_jobid(out1)

    stage2 = dict(common)
    stage2.update({
        "JOBNAME":  f"argo_merge_M{pid}",
        "MEM":      slurm.get("collocate_argo_merge_mem",      "32G"),
        "WALLTIME": slurm.get("collocate_argo_merge_walltime", "01:00:00"),
    })
    print(f"  Submitting collocate_argo Stage 2 merge (afterok:{jid1})")
    out2 = submitter.render_and_submit(
        "collocate_argo_merge.sbatch", stage2,
        logdir / "collocate_argo_merge.sbatch",
        dependency=f"afterok:{jid1}")
    jid2 = SlurmSubmitter.parse_jobid(out2)

    print(f"  Monitor: squeue -u $USER | Logs: {logdir}/argo_*.out")
    return jid2
