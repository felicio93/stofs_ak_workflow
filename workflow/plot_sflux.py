"""
plot_sflux.py
=============
SLURM worker — generates debug GIFs from sflux files for one month.

Plots every 6 hours (4 frames/day x ~30 days = ~120 frames per GIF).

Variables plotted:
    sflux_air: uwind, vwind, wind speed (sqrt(u²+v²)), prmsl, stmp, spfh
    sflux_prc: prate
    sflux_rad: dlwrf, dswrf

Output: D{ID}_YYYYMM/sflux_*.gif

Usage (called by SLURM via submit_era5.py):
    python plot_sflux.py --config <config_dir> --month YYYYMM
"""

import argparse
import gc
import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import imageio.v2 as imageio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow.config import load_config, model_dir

DPI = 150


def _plot_frame(lon2d, lat2d, values, title, date_str, cmap, vmin, vmax,
                cbar_label, out_path):
    lon_min, lon_max = lon2d.min(), lon2d.max()
    lat_min, lat_max = lat2d.min(), lat2d.max()
    asp = (lon_max - lon_min) / max(lat_max - lat_min, 1e-6)
    w   = float(np.clip(asp * 5.0, 4.0, 10.0))
    fig, ax = plt.subplots(figsize=(w + 1.2, 5.0), constrained_layout=True)
    pcm = ax.pcolormesh(lon2d, lat2d, values, cmap=cmap,
                        vmin=vmin, vmax=vmax, shading="auto")
    fig.colorbar(pcm, ax=ax, label=cbar_label, shrink=0.8)
    ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)
    ax.set_xlabel("Longitude (°E)"); ax.set_ylabel("Latitude (°N)")
    ax.set_title(f"{title}\n{date_str}", fontsize=11, fontweight="bold")
    ax.set_aspect("equal")
    fig.savefig(out_path, dpi=DPI, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": 85})
    plt.close(fig)


def make_gif(frames, gif_path):
    if not frames:
        return
    images = [imageio.imread(f) for f in frames]
    imageio.mimsave(gif_path, images, duration=1.0/5, loop=0)
    for f in frames:
        Path(f).unlink(missing_ok=True)
    print(f"  -> {gif_path.name}")


def plot_sflux_month(cfg: dict, ym: str):
    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    year  = int(ym[:4])
    month = int(ym[4:])
    ndays = monthrange(year, month)[1]

    sflux_dir   = mdir / f"I{pid}" / f"I{pid}_{ym}" / "sflux"
    out_dir     = mdir / f"D{pid}" / f"D{pid}_{ym}"
    tmp_dir     = out_dir / "sflux_frames_tmp"
    sentinel    = out_dir / "plot_sflux.done"
    sflux_ready = sflux_dir / "gen_sflux.done"

    if sentinel.exists():
        print(f"  plot_sflux: {ym} already complete. Skipping.")
        return

    # Wait for gen_sflux to finish — exit cleanly if not ready yet
    if not sflux_ready.exists():
        print(f"  plot_sflux: gen_sflux not yet complete for {ym} "
              f"(gen_sflux.done missing). Exiting without writing sentinel.")
        print(f"  Re-submit plot_sflux after gen_sflux finishes.")
        sys.exit(0)  # Exit 0 so SLURM marks the job as succeeded, not failed

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n--- plot_sflux {ym} -> {out_dir} ---")

    import netCDF4 as nc4

    # Collect variables to plot: (file_type, varname, title, cmap, vmin, vmax, label)
    var_specs = [
        ("air", "uwind",  "U Wind (10m)",        "RdBu_r", None, None, "m/s"),
        ("air", "vwind",  "V Wind (10m)",         "RdBu_r", None, None, "m/s"),
        ("air", "prmsl",  "Sea Level Pressure",   "viridis", None, None, "Pa"),
        ("air", "stmp",   "2m Temperature",       "RdYlBu_r", None, None, "K"),
        ("air", "spfh",   "Specific Humidity",    "Blues",   None, None, "kg/kg"),
        ("prc", "prate",  "Precipitation Rate",   "Blues",   None, None, "kg/m²/s"),
        ("rad", "dlwrf",  "Downward LW Radiation","inferno", None, None, "W/m²"),
        ("rad", "dswrf",  "Downward SW Radiation","inferno", None, None, "W/m²"),
    ]

    # Read lon/lat once from day 1
    air_file1 = sflux_dir / f"sflux_air_1.1.nc"
    if not air_file1.exists():
        print(f"  WARNING: {air_file1} not found, skipping.")
        return
    with nc4.Dataset(air_file1) as ds:
        lon2d = ds.variables["lon"][:].astype(np.float32)
        lat2d = ds.variables["lat"][:].astype(np.float32)

    for ftype, varname, title, cmap, vmin, vmax, label in var_specs:
        frames = []
        frame_idx = 0

        # Collect all 6-hourly frames across the month
        for iday in range(1, ndays + 1):
            stack    = str(iday)
            nc_path  = sflux_dir / f"sflux_{ftype}_1.{stack}.nc"
            if not nc_path.exists():
                continue
            day_date = date(year, month, iday)

            with nc4.Dataset(nc_path) as ds:
                if varname not in ds.variables:
                    break
                ntime = ds.variables["time"].shape[0]
                # Every 6 hours = timestep 0, 6, 12, 18 (and 24 only if present)
                for t_idx in range(0, ntime, 6):
                    dt_str = (day_date + timedelta(hours=t_idx)).strftime("%Y-%m-%d %HZ")
                    vals   = ds.variables[varname][t_idx, :, :].astype(np.float32)

                    # Auto-scale vmin/vmax from first frame
                    if vmin is None and frame_idx == 0:
                        finite = vals[np.isfinite(vals)]
                        if finite.size > 0:
                            vmin = float(np.percentile(finite, 2))
                            vmax = float(np.percentile(finite, 98))

                    frame_path = tmp_dir / f"{varname}_{ym}_{frame_idx:04d}.jpg"
                    _plot_frame(lon2d, lat2d, vals, title, dt_str,
                                cmap, vmin, vmax, label, frame_path)
                    frames.append(str(frame_path))
                    frame_idx += 1

        if frames:
            make_gif(frames, out_dir / f"sflux_{varname}_{ym}.gif")
        # Reset auto-scale for next variable
        vmin = None; vmax = None
        gc.collect()

    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    # Only write sentinel if at least one GIF was produced
    gif_count = len(list(out_dir.glob("sflux_*.gif")))
    if gif_count > 0:
        sentinel.touch()
        print(f"  {gif_count} GIF(s) written. Sentinel: {sentinel}")
    else:
        print(f"  WARNING: no GIFs were produced for {ym}. Sentinel NOT written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Plot sflux debug GIFs for one month")
    parser.add_argument("--config", required=True)
    parser.add_argument("--month",  required=True, help="YYYYMM")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    plot_sflux_month(cfg, args.month)
