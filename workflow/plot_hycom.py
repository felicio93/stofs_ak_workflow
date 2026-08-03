"""
plot_hycom.py
=============
Step 3 worker (runs inside a SLURM array task, in the swf_plot env).

Generates debug GIF animations from the aggregated monthly HYCOM stack files
for ONE month:

    D{ID}_YYYYMM/
      HYCOM_temperature_YYYYMM.gif   (surface + bottom panels)
      HYCOM_salinity_YYYYMM.gif      (surface + bottom panels)
      HYCOM_ssh_YYYYMM.gif           (single panel)

Design goals (fixes for the old Bering plotting script):
  * Figure size is derived from the domain aspect ratio, so panels fill the
    canvas with minimal whitespace for ANY domain.
  * Uses constrained_layout + a colorbar attached to the axes (no manual
    add_axes / bbox_inches='tight' conflict that caused big white gaps).
  * Color limits default to robust percentiles of the data (adaptive), and
    can be overridden per-variable via domain.yaml.
  * Domain extent and central_longitude come from config, not hardcoded.
  * Plots EVERY day (time record) in the month; also reports missing days by
    comparing the number of time records to the calendar length of the month.

Usage (invoked by the SLURM template):
    python plot_hycom.py --config <config_dir> --month YYYYMM
"""

import argparse
import gc
import sys
from calendar import monthrange
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import imageio.v2 as imageio

# Allow "python plot_hycom.py" to import the sibling config module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow.config import model_dir  # noqa: E402


def parse_range(value):
    """
    Interpret a config range like [auto, auto], [-2, 16], or None.
    Returns (vmin, vmax) where either may be None meaning 'auto'.
    """
    if value is None:
        return (None, None)
    lo, hi = value
    lo = None if (isinstance(lo, str) and lo.lower() == "auto") else lo
    hi = None if (isinstance(hi, str) and hi.lower() == "auto") else hi
    return (lo, hi)


def robust_limits(data, lo, hi):
    """Fill in None limits with 2nd/98th percentiles of finite data."""
    arr = np.asarray(data)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return (0.0, 1.0)
    if lo is None:
        lo = float(np.percentile(finite, 2))
    if hi is None:
        hi = float(np.percentile(finite, 98))
    if lo == hi:
        hi = lo + 1e-6
    return (lo, hi)


def figure_for_panels(n_panels, lon_min, lon_max, lat_min, lat_max):
    """
    Build a figure + axes sized to the domain aspect ratio to minimize
    whitespace. Panels are arranged in a single row.
    """
    lon_span = max(1e-6, lon_max - lon_min)
    lat_span = max(1e-6, lat_max - lat_min)
    aspect = lon_span / lat_span  # width per unit height of one panel

    panel_h = 4.5  # inches, per panel height (map area)
    panel_w = panel_h * aspect
    # Clamp panel width to keep figures reasonable for very wide domains
    panel_w = float(np.clip(panel_w, 3.0, 9.0))

    fig_w = panel_w * n_panels + 1.4  # room for shared colorbar
    fig_h = panel_h + 1.0             # room for titles

    central_lon = 0.5 * (lon_min + lon_max)
    proj = ccrs.PlateCarree(central_longitude=central_lon)
    data_proj = ccrs.PlateCarree()

    fig, axes = plt.subplots(
        1, n_panels,
        figsize=(fig_w, fig_h),
        subplot_kw={"projection": proj},
        constrained_layout=True,
    )
    if n_panels == 1:
        axes = [axes]
    for ax in axes:
        ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=data_proj)
        ax.add_feature(cfeature.LAND, zorder=100, edgecolor="k",
                       facecolor="lightgray")
        ax.add_feature(cfeature.COASTLINE, zorder=101)
    return fig, axes, data_proj


def make_gif(frames, gif_path: Path, fps=5):
    if not frames:
        print(f"  No frames generated for {gif_path.name}, skipping GIF.")
        return
    images = [imageio.imread(f) for f in frames]
    imageio.mimsave(gif_path, images, duration=1.0 / fps, loop=0)
    # Clean up the intermediate frame images
    for f in frames:
        Path(f).unlink(missing_ok=True)
    print(f"  Wrote {gif_path}")


def bottom_layer(block):
    """
    Return the deepest VALID (non-NaN) value at each (ylat, xlon) point.

    HYCOM native depth ordering has depth[0] = surface and increasing index =
    deeper. The "bottom" is the deepest level that still has valid data before
    hitting land/fill values. Implemented with numpy (no bottleneck dependency,
    unlike xarray's ffill).
    """
    # block dims: (depth, ylat, xlon)
    arr = block.values  # numpy array, NaN where invalid
    depth_axis = block.get_axis_num("depth")
    valid = np.isfinite(arr)
    # Index of the deepest valid level along depth for each column.
    # np.where(valid) then take max index; do it vectorized:
    depth_idx = np.arange(arr.shape[depth_axis])
    shape = [1, 1, 1]
    shape[depth_axis] = arr.shape[depth_axis]
    depth_idx = depth_idx.reshape(shape)
    # Set invalid levels to -1 so they never win the argmax-of-index
    masked_idx = np.where(valid, depth_idx, -1)
    deepest = masked_idx.max(axis=depth_axis)  # (ylat, xlon), -1 = all invalid
    deepest_safe = np.clip(deepest, 0, arr.shape[depth_axis] - 1)
    bottom_vals = np.take_along_axis(
        arr, np.expand_dims(deepest_safe, axis=depth_axis), axis=depth_axis
    ).squeeze(axis=depth_axis)
    # Columns with no valid data at all -> NaN
    bottom_vals = np.where(deepest >= 0, bottom_vals, np.nan)
    # Wrap back into a DataArray using the surface slice's coords
    surface = block.isel(depth=0)
    return surface.copy(data=bottom_vals)


def plot_two_layer_var(ds, varname, label, cmap, vlim, out_dir: Path,
                       ym, lon_min, lon_max, lat_min, lat_max, tmp_dir: Path):
    """Plot surface + bottom panels for a 3D variable (temperature/salinity)."""
    lo, hi = robust_limits(ds[varname].values, *vlim)
    frames = []
    ntime = ds.sizes["time"]
    print(f"  {varname}: rendering {ntime} frames...")
    for i in range(ntime):
        t = pd.Timestamp(ds.time.values[i]).strftime("%Y-%m-%d")
        block = ds[varname].isel(time=i)
        # HYCOM native ordering: depth[0] = surface; bottom = deepest valid.
        surface = block.isel(depth=0)
        bottom = bottom_layer(block)

        fig, axes, dp = figure_for_panels(2, lon_min, lon_max, lat_min, lat_max)
        im = axes[0].pcolormesh(surface.xlon, surface.ylat, surface,
                                transform=dp, cmap=cmap, vmin=lo, vmax=hi)
        axes[0].set_title(f"Surface {label}\n{t}")
        axes[1].pcolormesh(bottom.xlon, bottom.ylat, bottom,
                           transform=dp, cmap=cmap, vmin=lo, vmax=hi)
        axes[1].set_title(f"Bottom {label}\n{t}")
        fig.colorbar(im, ax=axes, shrink=0.85, label=label)

        frame = tmp_dir / f"{varname}_{ym}_{i:03d}.png"
        fig.savefig(frame, dpi=120)
        plt.close(fig)
        frames.append(str(frame))
        print(f"    frame {i+1:>3}/{ntime}  ({t})", flush=True)
    make_gif(frames, out_dir / f"HYCOM_{varname}_{ym}.gif")


def plot_ssh(ds, cmap, vlim, out_dir: Path, ym,
             lon_min, lon_max, lat_min, lat_max, tmp_dir: Path):
    lo, hi = robust_limits(ds["surf_el"].values, *vlim)
    frames = []
    ntime = ds.sizes["time"]
    print(f"  ssh: rendering {ntime} frames...")
    for i in range(ntime):
        t = pd.Timestamp(ds.time.values[i]).strftime("%Y-%m-%d")
        field = ds["surf_el"].isel(time=i)
        fig, axes, dp = figure_for_panels(1, lon_min, lon_max, lat_min, lat_max)
        im = axes[0].pcolormesh(field.xlon, field.ylat, field,
                                transform=dp, cmap=cmap, vmin=lo, vmax=hi)
        axes[0].set_title(f"Sea Surface Height\n{t}")
        fig.colorbar(im, ax=axes, shrink=0.85, label="SSH (m)")
        frame = tmp_dir / f"ssh_{ym}_{i:03d}.png"
        fig.savefig(frame, dpi=120)
        plt.close(fig)
        frames.append(str(frame))
        print(f"    frame {i+1:>3}/{ntime}  ({t})", flush=True)
    make_gif(frames, out_dir / f"HYCOM_ssh_{ym}.gif")


def check_missing_days(ntime, ym, tag):
    year, month = int(ym[:4]), int(ym[4:])
    ndays = monthrange(year, month)[1]
    if ntime < ndays:
        print(f"  WARNING [{tag} {ym}]: {ntime} time records but month has "
              f"{ndays} days -> {ndays - ntime} day(s) missing.")
    elif ntime > ndays:
        print(f"  NOTE [{tag} {ym}]: {ntime} time records (> {ndays} days).")


def plot_month(cfg: dict, ym: str):
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
    ddir = mdir / f"D{pid}" / f"D{pid}_{ym}"
    ddir.mkdir(parents=True, exist_ok=True)
    tmp_dir = ddir / "frames_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    lon_min = float(cfg["lon_min"]); lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"]); lat_max = float(cfg["lat_max"])
    cmap = cfg.get("plot_cmap", "jet")
    temp_lim = parse_range(cfg.get("plot_temp_range"))
    salt_lim = parse_range(cfg.get("plot_salt_range"))
    ssh_lim  = parse_range(cfg.get("plot_ssh_range"))

    ts_file  = idir / "TS_1.nc"
    ssh_file = idir / "SSH_1.nc"

    print(f"\n--- Plotting {ym} -> {ddir} ---")

    if ts_file.exists():
        # --- temperature ---
        ds = xr.open_dataset(ts_file, decode_times=True)
        check_missing_days(ds.sizes["time"], ym, "TS")
        if "temperature" in ds:
            plot_two_layer_var(ds, "temperature", "Potential Temp (degC)",
                               cmap, temp_lim, ddir, ym,
                               lon_min, lon_max, lat_min, lat_max, tmp_dir)
        ds.close()
        del ds
        gc.collect()

        # --- salinity (re-open so temperature arrays are freed first) ---
        ds = xr.open_dataset(ts_file, decode_times=True)
        if "salinity" in ds:
            plot_two_layer_var(ds, "salinity", "Salinity (psu)",
                               cmap, salt_lim, ddir, ym,
                               lon_min, lon_max, lat_min, lat_max, tmp_dir)
        ds.close()
        del ds
        gc.collect()
    else:
        print(f"  WARNING: {ts_file} not found, skipping T/S plots.")

    if ssh_file.exists():
        ds = xr.open_dataset(ssh_file, decode_times=True)
        check_missing_days(ds.sizes["time"], ym, "SSH")
        plot_ssh(ds, cmap, ssh_lim, ddir, ym,
                 lon_min, lon_max, lat_min, lat_max, tmp_dir)
        ds.close()
        del ds
        gc.collect()
    else:
        print(f"  WARNING: {ssh_file} not found, skipping SSH plot.")

    # Remove the temporary frame directory if empty
    try:
        tmp_dir.rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    from workflow.config import load_config
    parser = argparse.ArgumentParser(description="Plot HYCOM debug GIFs for one month")
    parser.add_argument("--config", required=True, help="Path to config/ dir")
    parser.add_argument("--month", required=True, help="Month as YYYYMM")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    plot_month(cfg, args.month)
