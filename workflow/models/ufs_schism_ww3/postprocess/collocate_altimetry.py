"""
models/ufs_schism_ww3/postprocess/collocate_altimetry.py
=========================================================
Phase 5 step "collocate_altimetry" for UFS-SCHISM+WW3.

Extends the SCHISM+WWM altimetry collocation by replacing the
SCHISM model class (which reads out2d_*.nc with sigWaveHeight)
with the WW3 model class (which reads *.out_grd.ww3.nc with HS).

Everything else (satellite download, spatial collocation logic,
merge/clean, submit_collocate_altimetry) is inherited unchanged
from the SCHISM+WWM implementation.

Key differences from SCHISM+WWM:
  - OCSTrack class:  WW3  (instead of SCHISM)
  - Model variable:  'HS' (instead of 'sigWaveHeight')
  - File pattern:    '*.out_grd.ww3.nc' in run dir root
                     (instead of 'out2d_*.nc' in outputs/)
  - rundir:          run directory root (WW3 writes there directly)
"""

import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from workflow.core.config import (
    load_config,
    list_groups,
    group_date_range,
    model_dir,
)
from workflow.core.environment import env_python

# Import everything from SCHISM+WWM — only override _collocate_one_day
from workflow.models.schism_wwm.postprocess.collocate_altimetry import (
    _out_dir,
    _daily_dir,
    _altimetry_obs_dir,
    _find_merged_sat_file,
    _window,
    _build_day_list,
    _to_180,
    _write_clean,
    run_merge,
)


# ---------------------------------------------------------------------------
# Override: collocate one day using WW3 class
# ---------------------------------------------------------------------------

def _collocate_one_day(cfg: dict,
                        day_str: str,
                        out_dir: Path):
    """Collocate satellite altimetry Hs against WW3 for one day.

    Uses OCSTrack's WW3 class which reads *.out_grd.ww3.nc files
    directly from the run directory root (not outputs/).
    """
    from ocstrack.Model.model import WW3
    from ocstrack.Observation.satellite import SatelliteData
    from ocstrack.Collocation.collocate import Collocate

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    d        = date(int(day_str[:4]),
                    int(day_str[4:6]),
                    int(day_str[6:8]))
    d_next   = d + timedelta(days=1)
    day_iso  = d.isoformat()
    dnxt_iso = d_next.isoformat()

    daily_dir_path = _daily_dir(out_dir)
    out_nc    = daily_dir_path / f"collocated_hs_{day_str}.nc"

    if out_nc.exists() and out_nc.stat().st_size > 0:
        print(f"  [{day_str}] already done, skipping.")
        return out_nc

    # ---- Find the merged satellite file ----
    obs_dir  = _altimetry_obs_dir(cfg)
    sat_file = _find_merged_sat_file(obs_dir)
    if sat_file is None:
        print(f"  [{day_str}] no merged satellite file found, skipping.")
        return None

    # ---- Find the group run directory for this day ----
    gid = None
    for group_id in list_groups(cfg):
        gstart, gend = group_date_range(cfg, group_id)
        if gstart <= d <= gend:
            gid = group_id
            break

    if gid is None:
        print(f"  [{day_str}] no group covers this day, skipping.")
        return None

    # WW3 writes field output to run directory ROOT (not outputs/)
    rundir = mdir / f"R{pid}" / f"R{pid}_{gid}"

    if not rundir.is_dir():
        print(f"  [{day_str}] run dir not found: {rundir}, skipping.")
        return None

    # Check WW3 field files exist in run dir root
    ww3_files = list(rundir.glob("*.out_grd.ww3.nc"))
    if not ww3_files:
        print(f"  [{day_str}] no *.out_grd.ww3.nc files in "
              f"{rundir}, skipping.")
        return None

    n_nearest  = int(cfg.get(
        "collocate_altimetry_n_nearest", 3))

    # ---- Load satellite data ----
    try:
        sat_data = SatelliteData(str(sat_file))
    except Exception as exc:
        print(f"  [{day_str}] SatelliteData load failed: "
              f"{type(exc).__name__}: {exc}")
        return None

    # ---- Load WW3 model ----
    model_dict = {
        "var":      "HS",
        "var_type": "2D",
    }
    try:
        model = WW3(
            rundir=str(rundir),
            model_dict=model_dict,
            start_date=np.datetime64(day_iso),
            end_date=np.datetime64(dnxt_iso),
        )
    except Exception as exc:
        print(f"  [{day_str}] WW3 init failed: "
              f"{type(exc).__name__}: {exc}")
        return None

    if not model.files:
        print(f"  [{day_str}] no WW3 files for this day, skipping.")
        return None

    # Convert mesh longitudes 0..360 -> -180..180 for OCSTrack
    model._mesh_x = _to_180(model._mesh_x)

    # ---- Collocate ----
    try:
        print(f"  [{day_str}] collocating against "
              f"{len(model.files)} WW3 file(s) "
              f"-> {out_nc.name}")
        coll = Collocate(
            model_run=model,
            observation=sat_data,
            n_nearest=n_nearest,
        )
        ds = coll.run(output_path=str(out_nc))
    except Exception as exc:
        print(f"  [{day_str}] collocation failed: "
              f"{type(exc).__name__}: {exc}")
        out_nc.unlink(missing_ok=True)
        return None

    if (ds is None
            or (hasattr(ds, "sizes")
                and ds.sizes.get("time", 0) == 0)):
        print(f"  [{day_str}] no collocated points.")
        out_nc.unlink(missing_ok=True)
        return None

    print(f"  [{day_str}] "
          f"{ds.sizes.get('time', '?')} "
          f"collocated point(s) -> {out_nc.name}")
    return out_nc


# ---------------------------------------------------------------------------
# Serial fallback — uses WW3 _collocate_one_day
# ---------------------------------------------------------------------------

def _run_serial(cfg: dict):
    """Serial fallback using WW3 collocation."""
    # FIX: compute obs_dir first, then pass to _find_merged_sat_file
    obs_dir  = _altimetry_obs_dir(cfg)
    sat_file = _find_merged_sat_file(obs_dir)

    if sat_file is None:
        source = str(cfg.get("altimetry_source", "cci")).lower()
        print(f"ERROR: no merged satellite file found in "
              f"obs/altimetry/{source}/.")
        print("  Run download_altimetry first.")
        return

    days    = _build_day_list(cfg)
    out_dir = _out_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)

    sentinel = out_dir / "collocate_altimetry.done"
    if sentinel.exists():
        print("  collocate_altimetry: already complete, skipping.")
        return

    dist_thresh_cfg = cfg.get(
        "collocate_altimetry_dist_threshold_km")
    dist_thresh_km  = (
        float(dist_thresh_cfg)
        if dist_thresh_cfg is not None
        else None)

    print(f"\n{'='*60}")
    print(f"  Altimetry collocation (serial, WW3)")
    print(f"  Satellite file : {sat_file.name}")
    print(f"  {len(days)} day(s)")
    if dist_thresh_km:
        print(f"  Distance threshold: {dist_thresh_km} km")
    print(f"{'='*60}\n")

    for day_str in days:
        try:
            _collocate_one_day(cfg, day_str, out_dir)
        except Exception as exc:
            print(f"  [{day_str}] ERROR: "
                  f"{type(exc).__name__}: {exc}")

    run_merge(cfg)


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def run_collocate_altimetry(cfg: dict,
                             config_dir=None):
    """Dispatcher: submit SLURM pipeline or run serial fallback."""
    import shutil

    allow_serial = (
        os.environ.get("ALLOW_NON_SLURM") == "1")

    if allow_serial or shutil.which("sbatch") is None:
        if shutil.which("sbatch") is None:
            print("  [collocate_altimetry] sbatch not found "
                  "— running serial fallback.")
        _run_serial(cfg)
        return

    from pathlib import Path as _Path
    config_dir = _Path(config_dir) if config_dir else None

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    out_dir = _out_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)
    done_all   = out_dir / "collocate_altimetry.done"
    done_daily = out_dir / ".daily_done"

    if done_all.exists():
        print("  collocate_altimetry: already complete, skipping.")
        return ""

    # FIX: compute obs_dir first, then pass to _find_merged_sat_file
    obs_dir  = _altimetry_obs_dir(cfg)
    sat_file = _find_merged_sat_file(obs_dir)

    if sat_file is None:
        source = str(cfg.get("altimetry_source", "cci")).lower()
        print(f"ERROR: no merged satellite file found in "
              f"obs/altimetry/{source}/.")
        print("  Run download_altimetry first.")
        return ""

    print(f"  Satellite file : {sat_file.name}")

    days = _build_day_list(cfg)
    if not days:
        print("  collocate_altimetry: empty date range.")
        return ""

    slurm = cfg.get("slurm", {})

    TEMPLATES_DIR = (
        Path(__file__).resolve().parent.parent.parent
        / "schism_wwm" / "templates" / "slurm"
    )

    common = {
        "ACCOUNT":    slurm.get("account",   "nos-surge"),
        "PARTITION":  slurm.get("partition", "hercules-2"),
        "MAILUSER":   slurm.get("mail_user",
                                "felicio.cassalho@noaa.gov"),
        "WORKDIR":    str(mdir),
        "LOGDIR":     str(logdir),
        "PY":         env_python(cfg, "collocate_altimetry",
                                 default="swf_plot"),
        # Use THIS module (ufs_schism_ww3) not schism_wwm
        "SCRIPT":     (
            "-m workflow.models.ufs_schism_ww3"
            ".postprocess.collocate_altimetry"),
        "CONFIG_DIR": str(config_dir),
    }

    from workflow.core.slurm import SlurmSubmitter

    submitter = SlurmSubmitter(TEMPLATES_DIR)

    # ---- daily done, only merge missing ----
    if done_daily.exists():
        print("  collocate_altimetry: daily done. Submitting merge.")
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

    # ---- Full pipeline: Stage 1 array + Stage 2 merge ----
    ntasks   = len(days)
    throttle = str(slurm.get(
        "collocate_altimetry_array_throttle", 50))

    manifest = (logdir
                / "collocate_altimetry_days.manifest")
    manifest.write_text("\n".join(days) + "\n")

    stage1 = dict(common)
    stage1.update({
        "JOBNAME":        f"alt_day_M{pid}",
        "NTASKS":         str(ntasks),
        "ARRAY_THROTTLE": throttle,
        "MEM":            slurm.get(
            "collocate_altimetry_mem", "16G"),
        "WALLTIME":       slurm.get(
            "collocate_altimetry_walltime", "00:30:00"),
        "MANIFEST":       str(manifest),
    })
    print(f"  Submitting collocate_altimetry (WW3) Stage 1 "
          f"array: {ntasks} day(s)  throttle={throttle}")
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
            "collocate_altimetry_merge_walltime",
            "01:00:00"),
    })
    print(f"  Submitting merge (afterok:{jid1})")
    out2 = submitter.render_and_submit(
        "collocate_altimetry_merge.sbatch", stage2,
        logdir / "collocate_altimetry_merge.sbatch",
        dependency=f"afterok:{jid1}")
    jid2 = SlurmSubmitter.parse_jobid(out2)

    print(f"  Monitor: squeue -u $USER | "
          f"Logs: {logdir}/alt_*.out")
    return jid2


# ---------------------------------------------------------------------------
# CLI — needed for SLURM array tasks
# ---------------------------------------------------------------------------

def main():
    import argparse
    ap  = argparse.ArgumentParser(
        description="WW3 altimetry collocation CLI")
    sub = ap.add_subparsers(dest="stage", required=True)

    pd_p = sub.add_parser("day")
    pd_p.add_argument("--date", required=True)

    sub.add_parser("merge")

    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg  = load_config(Path(args.config))

    if args.stage == "day":
        out_dir = _out_dir(cfg)
        out_dir.mkdir(parents=True, exist_ok=True)
        _collocate_one_day(cfg, args.date, out_dir)
    elif args.stage == "merge":
        run_merge(cfg)


if __name__ == "__main__":
    main()
