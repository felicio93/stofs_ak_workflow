"""
plot_datm.py — debug GIFs from DATM forcing files, one month per SLURM task.
Plots every 6 hours.  Uses shared plot_style for visual consistency.
"""

import argparse
import gc
import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import imageio.v2 as imageio

from workflow.core.config import load_config, model_dir
from workflow.core.plot_style import make_frame, read_mesh_boundaries

DPI     = 150
QUALITY = 85

VAR_SPECS = [
    # (varname, title,              cmap,       label)
    ("UGRD_10maboveground", "U Wind (10m)",      "RdBu_r",   "m/s"),
    ("VGRD_10maboveground", "V Wind (10m)",      "RdBu_r",   "m/s"),
    ("MSLMA_meansealevel",  "Sea Level Pressure","viridis",  "Pa"),
    ("TMP_2maboveground",   "2m Temperature",    "RdYlBu_r", "K"),
    ("SPFH_2maboveground",  "Specific Humidity", "YlGnBu",   "kg/kg"),
    ("PRATE_surface",       "Precipitation Rate","YlGnBu",   "kg/m²/s"),
    ("DLWRF_surface",       "Downward LW Radiation", "YlOrRd", "W/m²"),
    ("DSWRF_surface",       "Downward SW Radiation", "YlOrRd", "W/m²"),
]

def make_gif(frames, gif_path):
    if not frames:
        return
    images = [imageio.imread(f) for f in frames]
    imageio.mimsave(gif_path, images, duration=1.0/5, loop=0)
    for f in frames:
        Path(f).unlink(missing_ok=True)
    print(f"  -> {gif_path.name}")

def plot_datm_month(cfg: dict, ym: str):
    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    year  = int(ym[:4])
    month = int(ym[4:])

    datm_subdir = cfg.get("datm_subdir", "forcing")
    datm_filename_template = cfg.get("datm_filename_template", "datm_{YYYYMM}.nc")
    datm_filename = datm_filename_template.replace("{YYYYMM}", ym)

    datm_file   = mdir / f"I{pid}" / f"I{pid}_{ym}" / datm_subdir / datm_filename
    out_dir     = mdir / f"D{pid}" / f"D{pid}_{ym}"
    tmp_dir     = out_dir / "datm_frames_tmp"
    sentinel    = out_dir / "plot_datm.done"
    datm_ready = (mdir / f"I{pid}" / f"I{pid}_{ym}" / datm_subdir / "gen_datm.done")

    if sentinel.exists():
        print(f"  plot_datm: {ym} already complete. Skipping.")
        return

    if not datm_ready.exists():
        print(f"  plot_datm: gen_datm not yet complete for {ym}. Exiting.")
        sys.exit(0)

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    print(f"--- plot_datm {ym} -> {out_dir} ---")

    import netCDF4 as nc4

    if not datm_file.exists():
        print(f"  WARNING: {datm_file} not found, skipping.")
        return
    with nc4.Dataset(datm_file) as ds:
        lons_1d = ds.variables["longitude"][:].astype("float32")
        lats_1d = ds.variables["latitude"][:].astype("float32")
        lon2d, lat2d = np.meshgrid(lons_1d, lats_1d)
        time_var = ds.variables["time"]
        times = nc4.num2date(time_var[:], units=time_var.units, calendar=time_var.calendar)

    # Load mesh boundaries once (optional)
    hgrid_ll  = mdir / "fix" / "hgrid.ll"
    hgrid_gr3 = mdir / "fix" / "hgrid.gr3"
    boundaries = None
    for hpath in (hgrid_ll, hgrid_gr3):
        if hpath.exists():
            try:
                boundaries = read_mesh_boundaries(hpath)
                print(f"  Loaded mesh boundaries from {hpath.name}")
            except Exception as exc:
                print(f"  WARNING: could not read boundaries: {exc}")
            break

    for varname, title, cmap, label in VAR_SPECS:
        frames = []
        frame_idx = 0
        vmin = vmax = None

        with nc4.Dataset(datm_file) as ds:
            if varname not in ds.variables:
                break
            ntime = ds.variables["time"].shape[0]
            for t_idx in range(0, ntime, 6):
                dt_str = times[t_idx].strftime("%Y-%m-%d %HZ")
                vals = ds.variables[varname][t_idx, :, :].astype("float32")

                if vmin is None and frame_idx == 0:
                    finite = vals[np.isfinite(vals)]
                    if finite.size > 0:
                        vmin = float(np.percentile(finite, 2))
                        vmax = float(np.percentile(finite, 98))

                frame_path = tmp_dir / f"{varname}_{ym}_{frame_idx:04d}.jpg"
                make_frame(lon2d, lat2d, vals, title, dt_str,
                           cmap, vmin, vmax, label, frame_path,
                           dpi=DPI, quality=QUALITY,
                           boundaries=boundaries)
                frames.append(str(frame_path))
                frame_idx += 1

        if frames:
            make_gif(frames, out_dir / f"datm_{varname}_{ym}.gif")
        vmin = vmax = None
        gc.collect()

    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    gif_count = len(list(out_dir.glob("datm_*.gif")))
    if gif_count > 0:
        sentinel.touch()
        print(f"  {gif_count} GIF(s) written. Sentinel: {sentinel}")
    else:
        print(f"  WARNING: no GIFs produced for {ym}. Sentinel NOT written.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--month",  required=True)
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    plot_datm_month(cfg, args.month)
