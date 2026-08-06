"""
plot_hycom.py — debug GIFs from HYCOM SCHISM stacks, one month per SLURM task.
Plots every day.  Uses shared plot_style for visual consistency.
"""

import argparse
import gc
import sys
from calendar import monthrange
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import imageio.v2 as imageio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow.config import model_dir
from workflow.plot_style import make_frame, read_mesh_boundaries


DPI     = 120
QUALITY = 85


def parse_range(value):
    if value is None:
        return (None, None)
    lo, hi = value
    lo = None if (isinstance(lo, str) and lo.lower() == "auto") else lo
    hi = None if (isinstance(hi, str) and hi.lower() == "auto") else hi
    return (lo, hi)


def robust_limits(data, lo, hi):
    arr = np.asarray(data)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return (0.0, 1.0)
    if lo is None: lo = float(np.percentile(finite, 2))
    if hi is None: hi = float(np.percentile(finite, 98))
    if lo == hi:   hi = lo + 1e-6
    return (lo, hi)


def bottom_layer(block):
    arr        = block.values
    depth_axis = block.get_axis_num("depth")
    valid      = np.isfinite(arr)
    shape = [1, 1, 1]; shape[depth_axis] = arr.shape[depth_axis]
    depth_idx  = np.arange(arr.shape[depth_axis]).reshape(shape)
    masked_idx = np.where(valid, depth_idx, -1)
    deepest    = masked_idx.max(axis=depth_axis)
    deepest_s  = np.clip(deepest, 0, arr.shape[depth_axis] - 1)
    bottom_v   = np.take_along_axis(
        arr, np.expand_dims(deepest_s, axis=depth_axis), axis=depth_axis
    ).squeeze(axis=depth_axis)
    bottom_v   = np.where(deepest >= 0, bottom_v, np.nan)
    return block.isel(depth=0).copy(data=bottom_v)


def make_gif(frames, gif_path, fps=5):
    if not frames:
        print(f"  No frames for {gif_path.name}, skipping.")
        return
    images = [imageio.imread(f) for f in frames]
    imageio.mimsave(gif_path, images, duration=1.0/fps, loop=0)
    for f in frames:
        Path(f).unlink(missing_ok=True)
    print(f"  Wrote {gif_path}")


def plot_two_layer_var(ds, varname, label, cmap, vlim,
                       out_dir, ym, tmp_dir, boundaries=None):
    lo, hi = robust_limits(ds[varname].values, *vlim)
    frames = []
    ntime  = ds.sizes["time"]
    print(f"  {varname}: rendering {ntime} frames...")

    lon2d = ds["xlon"].values
    lat2d = ds["ylat"].values

    for i in range(ntime):
        t       = pd.Timestamp(ds.time.values[i]).strftime("%Y-%m-%d")
        block   = ds[varname].isel(time=i)
        surface = block.isel(depth=0).values
        bottom  = bottom_layer(block).values

        # Surface
        fp = tmp_dir / f"{varname}_surf_{ym}_{i:03d}.jpg"
        make_frame(lon2d, lat2d, surface, f"Surface {label}", t,
                   cmap, lo, hi, label, fp, dpi=DPI, quality=QUALITY,
                   boundaries=boundaries)
        frames.append(str(fp))

        # Bottom
        fp2 = tmp_dir / f"{varname}_bot_{ym}_{i:03d}.jpg"
        make_frame(lon2d, lat2d, bottom, f"Bottom {label}", t,
                   cmap, lo, hi, label, fp2, dpi=DPI, quality=QUALITY,
                   boundaries=boundaries)
        frames.append(str(fp2))

        print(f"    frame {i+1:>3}/{ntime}  ({t})", flush=True)

    surf_frames = frames[0::2]
    bot_frames  = frames[1::2]
    make_gif(surf_frames, out_dir / f"HYCOM_{varname}_surface_{ym}.gif")
    make_gif(bot_frames,  out_dir / f"HYCOM_{varname}_bottom_{ym}.gif")


def plot_ssh(ds, cmap, vlim, out_dir, ym, tmp_dir, boundaries=None):
    lo, hi = robust_limits(ds["surf_el"].values, *vlim)
    frames = []
    ntime  = ds.sizes["time"]
    print(f"  ssh: rendering {ntime} frames...")

    lon2d = ds["xlon"].values
    lat2d = ds["ylat"].values

    for i in range(ntime):
        t    = pd.Timestamp(ds.time.values[i]).strftime("%Y-%m-%d")
        vals = ds["surf_el"].isel(time=i).values
        fp   = tmp_dir / f"ssh_{ym}_{i:03d}.jpg"
        make_frame(lon2d, lat2d, vals, "Sea Surface Height", t,
                   cmap, lo, hi, "SSH (m)", fp, dpi=DPI, quality=QUALITY,
                   boundaries=boundaries)
        frames.append(str(fp))
        print(f"    frame {i+1:>3}/{ntime}  ({t})", flush=True)
    make_gif(frames, out_dir / f"HYCOM_ssh_{ym}.gif")


def check_missing_days(ntime, ym, tag):
    year, month = int(ym[:4]), int(ym[4:])
    ndays = monthrange(year, month)[1]
    if ntime < ndays:
        print(f"  WARNING [{tag} {ym}]: {ntime} records, month has {ndays} days.")


def plot_month(cfg: dict, ym: str):
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
    ddir = mdir / f"D{pid}" / f"D{pid}_{ym}"
    ddir.mkdir(parents=True, exist_ok=True)
    tmp  = ddir / "frames_tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    cmap     = cfg.get("plot_cmap", "jet")
    temp_lim = parse_range(cfg.get("plot_temp_range"))
    salt_lim = parse_range(cfg.get("plot_salt_range"))
    ssh_lim  = parse_range(cfg.get("plot_ssh_range"))

    # Load mesh boundaries once (optional; skip if hgrid.ll not found)
    hgrid_ll = mdir / "fix" / "hgrid.ll"
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

    ts_file  = idir / "TS_1.nc"
    ssh_file = idir / "SSH_1.nc"

    print(f"\n--- Plotting {ym} -> {ddir} ---")

    if ts_file.exists():
        ds = xr.open_dataset(ts_file, decode_times=True)
        check_missing_days(ds.sizes["time"], ym, "TS")
        if "temperature" in ds:
            plot_two_layer_var(ds, "temperature",
                               "Potential Temp (°C)", cmap, temp_lim,
                               ddir, ym, tmp, boundaries=boundaries)
        ds.close(); del ds; gc.collect()

        ds = xr.open_dataset(ts_file, decode_times=True)
        if "salinity" in ds:
            plot_two_layer_var(ds, "salinity",
                               "Salinity (psu)", cmap, salt_lim,
                               ddir, ym, tmp, boundaries=boundaries)
        ds.close(); del ds; gc.collect()
    else:
        print(f"  WARNING: {ts_file} not found.")

    if ssh_file.exists():
        ds = xr.open_dataset(ssh_file, decode_times=True)
        check_missing_days(ds.sizes["time"], ym, "SSH")
        plot_ssh(ds, cmap, ssh_lim, ddir, ym, tmp,
                 boundaries=boundaries)
        ds.close(); del ds; gc.collect()
    else:
        print(f"  WARNING: {ssh_file} not found.")

    try:
        tmp.rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    from workflow.config import load_config
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--month",  required=True)
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    plot_month(cfg, args.month)
