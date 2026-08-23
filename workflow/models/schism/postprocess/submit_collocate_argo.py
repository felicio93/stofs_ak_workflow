"""
models/schism/postprocess/submit_collocate_argo.py
===================================================
Three-stage SLURM launcher for the parallel Argo collocation + plotting pipeline.

Stage 1 — SLURM array, one task per day (throttled):
    A manifest lists every day in the collocation window. Each array task
    collocates ALL configured variables (temperature + salinity) for its
    assigned day, reusing the 2.6 M-node KDTree built once per task.
    Per-day per-variable NetCDFs are written to:
        P{ID}/P{ID}_collocate_argo/daily/collocated_{var}_{YYYYMMDD}.nc

Stage 2 — serial merge (--dependency=afterok on Stage 1):
    Concatenates all daily files per variable into one combined NetCDF,
    writes the distance-filtered clean file, and touches collocate_argo.done.

Stage 3 — serial plot_argo (--dependency=afterok on Stage 2):
    Generates all four Argo diagnostic plots via the standard
    ``stofs-ak --run --phase postprocess --only plot_argo`` CLI, consistent
    with how compare_sst_gif and plot_outputs_gif are dispatched.
    Only submitted when ``plot_argo: true`` is set in steps.yaml.

Re-run logic:
    * collocate_argo.done exists + plot_argo.done exists → skip entirely.
    * collocate_argo.done exists + plot_argo.done missing → submit Stage 3 only.
    * .daily_done exists + collocate_argo.done missing → submit Stage 2 + 3.
    * Otherwise → submit full pipeline (Stage 1 + 2 + 3).
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


def _submit_stage3(cfg, slurm, common, submitter, logdir, jid2, pid) -> str:
    """Submit Stage 3 (plot_argo) with afterok dependency on jid2."""
    # Stage 3 calls argo_plots as a Python module, matching the pattern used
    # by compare_sst_gif (compare_sst assemble) and plot_outputs_gif.
    stage3_common = dict(common)
    stage3_common["SCRIPT"] = "-m workflow.models.schism.postprocess.argo_plots"

    stage3 = dict(stage3_common)
    stage3.update({
        "JOBNAME":  f"argo_plot_M{pid}",
        "MEM":      slurm.get("collocate_argo_plot_mem",      "16G"),
        "WALLTIME": slurm.get("collocate_argo_plot_walltime", "00:30:00"),
    })
    dep = f"afterok:{jid2}" if jid2 else None
    if dep:
        print(f"  Submitting plot_argo Stage 3 (afterok:{jid2})")
    else:
        print("  Submitting plot_argo Stage 3")
    out3 = submitter.render_and_submit(
        "collocate_argo_plot.sbatch", stage3,
        logdir / "collocate_argo_plot.sbatch",
        dependency=dep)
    return SlurmSubmitter.parse_jobid(out3)


def submit_collocate_argo(cfg: dict, config_dir: Path) -> str:
    """Submit the three-stage Argo collocation + plotting pipeline to SLURM.

    Returns the SLURM job ID of the last submitted job, or '' if nothing
    was submitted.
    """
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    out_dir = mdir / f"P{pid}" / f"P{pid}_collocate_argo"
    out_dir.mkdir(parents=True, exist_ok=True)

    done_all    = out_dir / "collocate_argo.done"
    done_daily  = out_dir / ".daily_done"
    done_plot   = out_dir / "plot_argo.done"

    # Determine whether Stage 3 should be included.
    do_plot = bool(cfg.get("plot_argo", False))

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

    # ---- Fully complete ----
    if done_all.exists() and (done_plot.exists() or not do_plot):
        print("  collocate_argo: already complete, skipping.")
        return ""

    # ---- collocate done, only plot missing ----
    if done_all.exists() and do_plot and not done_plot.exists():
        print("  collocate_argo: collocation done. Submitting plot_argo only.")
        return _submit_stage3(cfg, slurm, common, submitter, logdir, "", pid)

    days = _build_day_list(cfg)
    if not days:
        print("  collocate_argo: empty date range. Nothing to do.")
        return ""

    # ---- Stage 2 only: daily files done, merge missing ----
    if done_daily.exists():
        print("  collocate_argo: daily collocation done (.daily_done). "
              "Submitting merge + plot.")
        stage2 = dict(common)
        stage2.update({
            "JOBNAME":  f"argo_merge_M{pid}",
            "MEM":      slurm.get("collocate_argo_merge_mem",      "32G"),
            "WALLTIME": slurm.get("collocate_argo_merge_walltime", "01:00:00"),
        })
        out2 = submitter.render_and_submit(
            "collocate_argo_merge.sbatch", stage2,
            logdir / "collocate_argo_merge.sbatch")
        jid2 = SlurmSubmitter.parse_jobid(out2)
        if do_plot:
            return _submit_stage3(cfg, slurm, common, submitter, logdir, jid2, pid)
        return jid2

    # ---- Full pipeline: Stage 1 + 2 + (optionally) 3 ----
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

    if do_plot:
        last_jid = _submit_stage3(
            cfg, slurm, common, submitter, logdir, jid2, pid)
    else:
        last_jid = jid2

    print(f"  Monitor: squeue -u $USER | Logs: {logdir}/argo_*.out")
    return last_jid
