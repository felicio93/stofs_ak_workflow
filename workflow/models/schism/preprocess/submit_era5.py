"""
models/schism/preprocess/submit_era5.py
=======================================
SLURM launchers for ERA5-derived compute steps:
  - gen_sflux:  ERA5 raw -> SCHISM sflux files (SLURM array, swf_main)
  - plot_sflux: sflux debug GIFs             (SLURM array, swf_plot)

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

plot_sflux is submitted with --dependency=afterok:<gen_sflux_jobid> so
that it only runs after all gen_sflux array tasks have completed.
"""

from pathlib import Path

from workflow.core.config import list_groups, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter, write_manifest

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent / "templates" / "slurm"
)


def _common(cfg: dict) -> dict:
    slurm = cfg.get("slurm", {})
    return {
        "ACCOUNT":   slurm.get("account",   "nos-surge"),
        "PARTITION": slurm.get("partition", "hercules-2"),
        "MAILUSER":  slurm.get("mail_user",
                               "felicio.cassalho@noaa.gov"),
    }


# =============================================================================
# gen_sflux
# =============================================================================

def submit_gen_sflux(cfg: dict, config_dir: Path) -> str:
    """Submit gen_sflux array job.

    Returns the sbatch job ID string, or '' if nothing was submitted
    because all groups are already complete.
    """
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    groups = list_groups(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    pending = []
    for group_id in groups:
        sentinel = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                    / "sflux" / "gen_sflux.done")
        if sentinel.exists():
            print(f"  {group_id}: gen_sflux already complete, skipping.")
            continue

        # Check that the ERA5 raw file exists for the group start date
        from workflow.core.config import group_date_range
        gstart, _ = group_date_range(cfg, group_id)
        raw = (mdir / "raw" / "era5"
               / str(gstart.year)
               / f"era5_{gstart.year}{gstart.month:02d}.nc")
        if not (raw.exists() and raw.stat().st_size > 0):
            print(f"  {group_id}: raw ERA5 file missing "
                  f"({raw.name}), skipping.")
            continue

        pending.append(group_id)

    if not pending:
        print("  gen_sflux: all groups already complete. "
              "Nothing to submit.")
        return ""

    manifest = write_manifest(
        pending, logdir / "gen_sflux_groups.manifest")

    slurm = cfg.get("slurm", {})
    subs  = _common(cfg)
    subs.update({
        "JOBNAME":    f"gensflux_M{pid}",
        "WORKDIR":    str(mdir),
        "NGROUPS":    str(len(pending)),
        "MEM":        slurm.get("gen_sflux_mem",      "8G"),
        "WALLTIME":   slurm.get("gen_sflux_walltime", "01:00:00"),
        "LOGDIR":     str(logdir),
        "MANIFEST":   str(manifest),
        "PY":         env_python(cfg, "gen_sflux"),
        "SCRIPT":     "-m workflow.models.schism.preprocess.gen_sflux",
        "CONFIG_DIR": str(config_dir),
        "PID":        pid,
        "IBASEDIR":   str(mdir / f"I{pid}"),
    })

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_sflux: {len(pending)} group(s) "
          f"({pending[0]} -> {pending[-1]})")
    out = submitter.render_and_submit(
        "gen_sflux.sbatch", subs,
        logdir / "gen_sflux.sbatch")
    print(f"  Monitor: squeue -u $USER | "
          f"Logs: {logdir}/gen_sflux_*.out")
    return SlurmSubmitter.parse_jobid(out)


# =============================================================================
# plot_sflux
# =============================================================================

def submit_plot_sflux(cfg: dict, config_dir: Path,
                      after_jobid: str = "") -> str:
    """Submit plot_sflux array job.

    after_jobid: if non-empty, submitted with
        --dependency=afterok:<after_jobid>
    Returns the sbatch job ID string, or '' if nothing to submit.
    """
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    groups = list_groups(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    pending = []
    for group_id in groups:
        sentinel = (mdir / f"D{pid}" / f"D{pid}_{group_id}"
                    / "plot_sflux.done")
        if sentinel.exists():
            print(f"  {group_id}: plot_sflux already complete, "
                  f"skipping.")
        else:
            pending.append(group_id)

    if not pending:
        print("  plot_sflux: all groups already complete. "
              "Nothing to submit.")
        return ""

    manifest = write_manifest(
        pending, logdir / "plot_sflux_groups.manifest")

    dependency = f"afterok:{after_jobid}" if after_jobid else None
    if dependency:
        print(f"  plot_sflux will start after gen_sflux job "
              f"{after_jobid} completes.")

    slurm = cfg.get("slurm", {})
    subs  = _common(cfg)
    subs.update({
        "JOBNAME":    f"plotsflux_M{pid}",
        "WORKDIR":    str(mdir),
        "NGROUPS":    str(len(pending)),
        "MEM":        slurm.get("plot_sflux_mem",      "16G"),
        "WALLTIME":   slurm.get("plot_sflux_walltime", "00:30:00"),
        "LOGDIR":     str(logdir),
        "MANIFEST":   str(manifest),
        "PY":         env_python(cfg, "plot_sflux",
                                 default="swf_plot"),
        "SCRIPT":     "-m workflow.diagnostics.plot_sflux",
        "CONFIG_DIR": str(config_dir),
    })

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting plot_sflux: {len(pending)} group(s)")
    out = submitter.render_and_submit(
        "plot_sflux.sbatch", subs,
        logdir / "plot_sflux.sbatch",
        dependency=dependency)
    print(f"  Monitor: squeue -u $USER | "
          f"Logs: {logdir}/plot_sflux_*.out")
    return SlurmSubmitter.parse_jobid(out)
