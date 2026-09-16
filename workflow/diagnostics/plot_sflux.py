"""
plot_sflux.py — debug GIFs from sflux files, one group per SLURM task.
Plots every 6 hours.  Uses shared plot_style for visual consistency.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).
"""

import argparse
import gc
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import imageio.v2 as imageio

from workflow.core.config import (
    load_config,
    model_dir,
    group_date_range,
    get_group_ndays,
)
from workflow.core.plot_style import make_frame, read_mesh_boundaries

DPI     = 150
QUALITY = 85

VAR_SPECS = [
    # (ftype, varname, title,              cmap,       label)
    ("air", "uwind",  "U Wind (10m)",      "RdBu_r",   "m/s"),
    ("air", "vwind",  "V Wind (10m)",      "RdBu_r",   "m/s"),
    ("air", "prmsl",  "Sea Level Pressure","viridis",   "Pa"),
    ("air", "stmp",   "2m Temperature",    "RdYlBu_r", "K"),
    ("air", "spfh",   "Specific Humidity", "YlGnBu",   "kg/kg"),
    ("prc", "prate",  "Precipitation Rate","YlGnBu",   "kg/m²/s"),
    ("rad", "dlwrf",  "Downward LW Radiation", "YlOrRd", "W/m²"),
    ("rad", "dswrf",  "Downward SW Radiation", "YlOrRd", "W/m²"),
]


def make_gif(frames, gif_path):
    if not frames:
        return
    images = [imageio.imread(f) for f in frames]
    imageio.mimsave(gif_path, images, duration=1.0/5, loop=0)
    for f in frames:
        Path(f).unlink(missing_ok=True)
    print(f"  -> {gif_path.name}")


def plot_sflux_month(cfg: dict, ym: str):
    """Plot sflux debug GIFs for one group.

    ym is a group ID: 'YYYYMM' for monthly grouping or
    'YYYYMMDD' for ndays grouping.
    """
    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)

    # Use group helpers — works for both YYYYMM and YYYYMMDD IDs
    gstart, _ = group_date_range(cfg, ym)
    ndays     = get_group_ndays(cfg, ym)

    sflux_dir   = mdir / f"I{pid}" / f"I{pid}_{ym}" / "sflux"
    out_dir     = mdir / f"D{pid}" / f"D{pid}_{ym}"
    tmp_dir     = out_dir / "sflux_frames_tmp"
    sentinel    = out_dir / "plot_sflux.done"
    sflux_ready = sflux_dir / "gen_sflux.done"

    if sentinel.exists():
        print(f"  plot_sflux: {ym} already complete. Skipping.")
        return

    if not sflux_ready.exists():
        print(f"  plot_sflux: gen_sflux not yet complete for "
              f"{ym}. Exiting.")
        sys.exit(0)

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n--- plot_sflux {ym}  "
          f"({gstart} + {ndays} days) -> {out_dir} ---")

    import netCDF4 as nc4

    air_file1 = sflux_dir / "sflux_air_1.1.nc"
    if not air_file1.exists():
        print(f"  WARNING: {air_file1} not found, skipping.")
        return
    with nc4.Dataset(air_file1) as ds:
        lon2d = ds.variables["lon"][:].astype("float32")
        lat2d = ds.variables["lat"][:].astype("float32")

    # Load mesh boundaries once (optional)
    hgrid_ll  = mdir / "fix" / "hgrid.ll"
    hgrid_gr3 = mdir / "fix" / "hgrid.gr3"
    boundaries = None
    for hpath in (hgrid_ll, hgrid_gr3):
        if hpath.exists():
            try:
                boundaries = read_mesh_boundaries(hpath)
                print(f"  Loaded mesh boundaries from "
                      f"{hpath.name}")
            except Exception as exc:
                print(f"  WARNING: could not read boundaries: "
                      f"{exc}")
            break

    for ftype, varname, title, cmap, label in VAR_SPECS:
        frames    = []
        frame_idx = 0
        vmin = vmax = None

        # Iterate over group days using 1-based stack indices
        for day_offset in range(ndays):
            iday     = day_offset + 1          # 1-based stack index
            day_date = gstart + timedelta(days=day_offset)
            nc_path  = sflux_dir / f"sflux_{ftype}_1.{iday}.nc"
            if not nc_path.exists():
                continue

            with nc4.Dataset(nc_path) as ds:
                if varname not in ds.variables:
                    break
                ntime = ds.variables["time"].shape[0]
                for t_idx in range(0, ntime, 6):
                    dt_str = (
                        day_date + timedelta(hours=t_idx)
                    ).strftime("%Y-%m-%d %HZ")
                    vals = ds.variables[varname][
                        t_idx, :, :].astype("float32")

                    if vmin is None and frame_idx == 0:
                        finite = vals[np.isfinite(vals)]
                        if finite.size > 0:
                            vmin = float(
                                np.percentile(finite, 2))
                            vmax = float(
                                np.percentile(finite, 98))

                    frame_path = (
                        tmp_dir
                        / f"{varname}_{ym}_{frame_idx:04d}.jpg"
                    )
                    make_frame(lon2d, lat2d, vals, title,
                               dt_str, cmap, vmin, vmax,
                               label, frame_path,
                               dpi=DPI, quality=QUALITY,
                               boundaries=boundaries)
                    frames.append(str(frame_path))
                    frame_idx += 1

        if frames:
            make_gif(frames,
                     out_dir / f"sflux_{varname}_{ym}.gif")
        vmin = vmax = None
        gc.collect()

    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    gif_count = len(list(out_dir.glob("sflux_*.gif")))
    if gif_count > 0:
        sentinel.touch()
        print(f"  {gif_count} GIF(s) written. "
              f"Sentinel: {sentinel}")
    else:
        print(f"  WARNING: no GIFs produced for {ym}. "
              f"Sentinel NOT written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Plot sflux debug GIFs for one group")
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--month", required=True,
        help="Group ID (YYYYMM for monthly or YYYYMMDD for ndays)")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    plot_sflux_month(cfg, args.month)
