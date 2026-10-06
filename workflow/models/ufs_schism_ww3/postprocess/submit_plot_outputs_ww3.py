"""
models/ufs_schism_ww3/postprocess/submit_plot_outputs_ww3.py
=============================================================
Two-stage SLURM launcher for plot_outputs_ww3.

Stage 1 — SLURM array (one task per WW3 field file, throttled).
Stage 2 — serial GIF assembly (afterok on Stage 1).
"""

import math
from pathlib import Path

from workflow.core.config import list_groups, model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import SlurmSubmitter

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent
    / "templates" / "slurm"
)


def submit_plot_outputs_ww3(cfg: dict,
                             config_dir: Path) -> str:
    """Submit the two-stage WW3 field plot pipeline."""
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    gif_dir     = mdir / f"P{pid}" / f"P{pid}_plot_outputs_ww3"
    done_gif    = gif_dir / "plot_outputs_ww3.done"
    done_frames = gif_dir / ".ww3_frames_done"

    # Already complete
    if done_gif.exists():
        print("  plot_outputs_ww3: already complete, skipping.")
        return ""

    # Build manifest of all WW3 field files
    all_files = []
    for gid in list_groups(cfg):
        rdir = mdir / f"R{pid}" / f"R{pid}_{gid}"
        for f in sorted(rdir.glob("*.out_grd.ww3.nc")):
            all_files.append(str(f))

    if not all_files:
        print("  plot_outputs_ww3: no *.out_grd.ww3.nc files found.")
        return ""

    ntasks   = len(all_files)
    slurm    = cfg.get("slurm", {})
    throttle = str(slurm.get(
        "plot_outputs_ww3_array_throttle", 20))

    # Write manifest: one file path per line
    manifest = logdir / "plot_outputs_ww3.manifest"
    manifest.write_text("\n".join(all_files) + "\n")

    common = {
        "ACCOUNT":    slurm.get("account",   "nos-surge"),
        "PARTITION":  slurm.get(
            "plot_outputs_ww3_partition",
            slurm.get("partition", "hercules-2")),
        "QOS":        slurm.get("plot_outputs_ww3_qos", "windfall"),
        "MAILUSER":   slurm.get("mail_user",
                                "felicio.cassalho@noaa.gov"),
        "WORKDIR":    str(mdir),
        "LOGDIR":     str(logdir),
        "PY":         env_python(cfg, "plot_outputs_ww3",
                                 default="swf_plot"),
        "SCRIPT":     (
            "-m workflow.models.ufs_schism_ww3"
            ".postprocess.plot_outputs_ww3"),
        "CONFIG_DIR": str(config_dir),
    }

    submitter = SlurmSubmitter(TEMPLATES_DIR)

    # Frames already done — only submit GIF assembly
    if done_frames.exists():
        print("  plot_outputs_ww3: frames done. "
              "Submitting GIF assembly only.")
        stage2 = dict(common)
        stage2.update({
            "JOBNAME":  f"ww3gif_M{pid}",
            "MEM":      slurm.get(
                "plot_outputs_ww3_gif_mem", "32G"),
            "WALLTIME": slurm.get(
                "plot_outputs_ww3_gif_walltime", "00:30:00"),
        })
        out2 = submitter.render_and_submit(
            "plot_outputs_ww3_gif.sbatch", stage2,
            logdir / "plot_outputs_ww3_gif.sbatch")
        return SlurmSubmitter.parse_jobid(out2)

    # Stage 1: array frames
    stage1 = dict(common)
    stage1.update({
        "JOBNAME":        f"ww3frm_M{pid}",
        "NTASKS":         str(ntasks),
        "ARRAY_THROTTLE": throttle,
        "MEM":            slurm.get(
            "plot_outputs_ww3_mem", "32G"),
        "WALLTIME":       slurm.get(
            "plot_outputs_ww3_walltime", "00:30:00"),
        "MANIFEST":       str(manifest),
    })
    print(f"  Submitting plot_outputs_ww3 array: "
          f"{ntasks} file(s)  throttle={throttle}")
    out1 = submitter.render_and_submit(
        "plot_outputs_ww3_frames.sbatch", stage1,
        logdir / "plot_outputs_ww3_frames.sbatch")
    jid1 = SlurmSubmitter.parse_jobid(out1)

    # Stage 2: serial GIF assembly
    stage2 = dict(common)
    stage2.update({
        "JOBNAME":  f"ww3gif_M{pid}",
        "MEM":      slurm.get(
            "plot_outputs_ww3_gif_mem", "32G"),
        "WALLTIME": slurm.get(
            "plot_outputs_ww3_gif_walltime", "00:30:00"),
    })
    print(f"  Submitting plot_outputs_ww3 GIF assembly "
          f"(afterok:{jid1})")
    out2 = submitter.render_and_submit(
        "plot_outputs_ww3_gif.sbatch", stage2,
        logdir / "plot_outputs_ww3_gif.sbatch",
        dependency=f"afterok:{jid1}")
    jid2 = SlurmSubmitter.parse_jobid(out2)

    print(f"  Monitor: squeue -u $USER | "
          f"Logs: {logdir}/ww3*.out")
    return jid2
