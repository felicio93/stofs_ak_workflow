"""
submit_era5.py
==============
SLURM launchers for ERA5-related compute steps:
  - gen_sflux:  ERA5 raw -> SCHISM sflux files (SLURM array, swf_main)
  - plot_sflux: sflux debug GIFs (SLURM array, swf_plot)
"""

import shutil
import subprocess
import sys
from pathlib import Path

from workflow.config import list_months, model_dir

REPO_ROOT = Path(__file__).resolve().parent.parent


def _render(template_path: Path, subs: dict) -> str:
    text = template_path.read_text()
    for k, v in subs.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def _sbatch(script_path: Path) -> str:
    result = subprocess.run(["sbatch", str(script_path)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR: sbatch failed: {result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip()


def _env_python(cfg: dict, step: str) -> str:
    conda_base = Path(cfg["conda_base"])
    env = cfg.get("conda_envs", {}).get(step, "swf_main")
    if env == "base":
        return str(conda_base / "bin" / "python")
    return str(conda_base / "envs" / env / "bin" / "python")


def _common(cfg: dict, mdir: Path) -> dict:
    slurm = cfg.get("slurm", {})
    return {
        "ACCOUNT":   slurm.get("account",   "nos-surge"),
        "PARTITION": slurm.get("partition",  "hercules-2"),
        "MAILUSER":  slurm.get("mail_user",  "felicio.cassalho@noaa.gov"),
    }


# =============================================================================
# gen_sflux
# =============================================================================

def submit_gen_sflux(cfg: dict, config_dir: Path):
    if shutil.which("sbatch") is None:
        print("ERROR: sbatch not found. Run from a login node.")
        sys.exit(1)

    pid     = cfg["project_id"]
    mdir    = model_dir(cfg)
    months  = list_months(cfg)
    logdir  = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    # Filter pending months
    pending = []
    for ym in months:
        sentinel = mdir / f"I{pid}" / f"I{pid}_{ym}" / "sflux" / "gen_sflux.done"
        if sentinel.exists():
            print(f"  {ym}: gen_sflux already complete, skipping.")
        else:
            # Check raw file exists
            raw = mdir / "raw" / "era5" / ym[:4] / f"era5_{ym}.nc"
            if not (raw.exists() and raw.stat().st_size > 0):
                print(f"  {ym}: raw ERA5 file missing ({raw.name}), skipping.")
            else:
                pending.append(ym)

    if not pending:
        print("  All months already complete. Nothing to submit.")
        return

    manifest = logdir / "gen_sflux_months.manifest"
    manifest.write_text("\n".join(pending) + "\n")

    slurm  = cfg.get("slurm", {})
    subs   = _common(cfg, mdir)
    subs.update({
        "JOBNAME":    f"gensflux_M{pid}",
        "WORKDIR":    str(mdir),
        "NMONTHS":    str(len(pending)),
        "MEM":        slurm.get("gen_sflux_mem",      "8G"),
        "WALLTIME":   slurm.get("gen_sflux_walltime",  "01:00:00"),
        "LOGDIR":     str(logdir),
        "MANIFEST":   str(manifest),
        "PY":         _env_python(cfg, "gen_sflux"),
        "SCRIPT":     str(REPO_ROOT / "workflow" / "gen_sflux.py"),
        "CONFIG_DIR": str(config_dir),
        "PID":        pid,
        "IBASEDIR":   str(mdir / f"I{pid}"),
    })

    template      = REPO_ROOT / "templates" / "slurm" / "gen_sflux.sbatch"
    rendered_path = logdir / "gen_sflux.sbatch"
    rendered_path.write_text(_render(template, subs))

    print(f"  Submitting gen_sflux: {len(pending)} month(s) ({pending[0]} -> {pending[-1]})")
    print(f"  Rendered:  {rendered_path}")
    out = _sbatch(rendered_path)
    print(f"  {out}")
    print(f"  Monitor: squeue -u $USER | Logs: {logdir}/gen_sflux_*.out")


# =============================================================================
# plot_sflux
# =============================================================================

def submit_plot_sflux(cfg: dict, config_dir: Path):
    if shutil.which("sbatch") is None:
        print("ERROR: sbatch not found. Run from a login node.")
        sys.exit(1)

    pid     = cfg["project_id"]
    mdir    = model_dir(cfg)
    months  = list_months(cfg)
    logdir  = mdir / "logs"
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

    manifest = logdir / "plot_sflux_months.manifest"
    manifest.write_text("\n".join(pending) + "\n")

    slurm = cfg.get("slurm", {})
    subs  = _common(cfg, mdir)
    subs.update({
        "JOBNAME":    f"plotsflux_M{pid}",
        "WORKDIR":    str(mdir),
        "NMONTHS":    str(len(pending)),
        "MEM":        slurm.get("plot_sflux_mem",      "16G"),
        "WALLTIME":   slurm.get("plot_sflux_walltime",  "00:30:00"),
        "LOGDIR":     str(logdir),
        "MANIFEST":   str(manifest),
        "PY":         _env_python(cfg, "plot_sflux"),
        "SCRIPT":     str(REPO_ROOT / "workflow" / "plot_sflux.py"),
        "CONFIG_DIR": str(config_dir),
    })

    template      = REPO_ROOT / "templates" / "slurm" / "plot_sflux.sbatch"
    rendered_path = logdir / "plot_sflux.sbatch"
    rendered_path.write_text(_render(template, subs))

    print(f"  Submitting plot_sflux: {len(pending)} month(s)")
    print(f"  Rendered:  {rendered_path}")
    out = _sbatch(rendered_path)
    print(f"  {out}")
    print(f"  Monitor: squeue -u $USER | Logs: {logdir}/plot_sflux_*.out")
