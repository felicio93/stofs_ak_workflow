"""
models/schism/preprocess/submit_era5.py
=======================================
SLURM launchers for ERA5-derived compute steps:
  - gen_sflux:  ERA5 raw -> SCHISM sflux files (SLURM array, swf_main)
  - plot_sflux: sflux debug GIFs             (SLURM array, swf_plot)
"""

from pathlib import Path

from workflow.core.config import list_months, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter, write_manifest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "slurm"


def _common(cfg: dict) -> dict:
    slurm = cfg.get("slurm", {})
    return {
        "ACCOUNT":   slurm.get("account",   "nos-surge"),
        "PARTITION": slurm.get("partition", "hercules-2"),
        "MAILUSER":  slurm.get("mail_user", "felicio.cassalho@noaa.gov"),
    }


# =============================================================================
# gen_sflux
# =============================================================================

def submit_gen_sflux(cfg: dict, config_dir: Path):
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    pending = []
    for ym in months:
        sentinel = mdir / f"I{pid}" / f"I{pid}_{ym}" / "sflux" / "gen_sflux.done"
        if sentinel.exists():
            print(f"  {ym}: gen_sflux already complete, skipping.")
            continue
        raw = mdir / "raw" / "era5" / ym[:4] / f"era5_{ym}.nc"
        if not (raw.exists() and raw.stat().st_size > 0):
            print(f"  {ym}: raw ERA5 file missing ({raw.name}), skipping.")
            continue
        pending.append(ym)

    if not pending:
        print("  All months already complete. Nothing to submit.")
        return

    manifest = write_manifest(pending, logdir / "gen_sflux_months.manifest")

    slurm = cfg.get("slurm", {})
    subs  = _common(cfg)
    subs.update({
        "JOBNAME":    f"gensflux_M{pid}",
        "WORKDIR":    str(mdir),
        "NMONTHS":    str(len(pending)),
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
    print(f"  Submitting gen_sflux: {len(pending)} month(s) "
          f"({pending[0]} -> {pending[-1]})")
    submitter.render_and_submit("gen_sflux.sbatch", subs,
                                logdir / "gen_sflux.sbatch")
    print(f"  Monitor: squeue -u $USER | Logs: {logdir}/gen_sflux_*.out")


# =============================================================================
# plot_sflux
# =============================================================================

def submit_plot_sflux(cfg: dict, config_dir: Path):
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    months = list_months(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    pending = []
    for ym in months:
        sentinel = mdir / f"D{pid}" / f"D{pid}_{ym}" / "plot_sflux.done"
        if sentinel.exists():
            print(f"  {ym}: plot_sflux already complete, skipping.")
        else:
            pending.append(ym)

    if not pending:
        print("  All months already complete. Nothing to submit.")
        return

    manifest = write_manifest(pending, logdir / "plot_sflux_months.manifest")

    slurm = cfg.get("slurm", {})
    subs  = _common(cfg)
    subs.update({
        "JOBNAME":    f"plotsflux_M{pid}",
        "WORKDIR":    str(mdir),
        "NMONTHS":    str(len(pending)),
        "MEM":        slurm.get("plot_sflux_mem",      "16G"),
        "WALLTIME":   slurm.get("plot_sflux_walltime", "00:30:00"),
        "LOGDIR":     str(logdir),
        "MANIFEST":   str(manifest),
        "PY":         env_python(cfg, "plot_sflux", default="swf_plot"),
        "SCRIPT":     "-m workflow.diagnostics.plot_sflux",
        "CONFIG_DIR": str(config_dir),
    })

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting plot_sflux: {len(pending)} month(s)")
    submitter.render_and_submit("plot_sflux.sbatch", subs,
                                logdir / "plot_sflux.sbatch")
    print(f"  Monitor: squeue -u $USER | Logs: {logdir}/plot_sflux_*.out")
