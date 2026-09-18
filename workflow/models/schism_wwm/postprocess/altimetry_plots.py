"""
models/schism_wwm/postprocess/altimetry_plots.py
=================================================
Phase 5 step "plot_altimetry" (interactive; runs in the swf_plot env).

Produces seven diagnostic plots from the collocated altimetry NetCDF
written by the ``collocate_altimetry`` step:

  Plot 1 — Satellite track map by source (altimetry_tracks_by_source.jpg)
      Scatter of all observation locations coloured by satellite source
      (categorical). Mesh boundaries overlaid from fix/hgrid.gr3.

  Plot 2 — Satellite track map by time (altimetry_tracks_by_time.jpg)
      Same scatter coloured by observation time (turbo colormap).
      Horizontal colorbar showing date range.

  Scatter Plot 1 — Coloured by time delta (altimetry_scatter_time_delta.jpg)
  Scatter Plot 2 — Coloured by distance delta (altimetry_scatter_dist_delta.jpg)
  Scatter Plot 3 — Coloured by model depth (altimetry_scatter_depth.jpg)
  Scatter Plot 4 — Coloured by distance to coast (altimetry_scatter_coast_dist.jpg)
  Scatter Plot 5 — Coloured by satellite source (altimetry_scatter_source.jpg)

All scatter plots:
  - X axis: obs_swh_adjusted (bias-corrected satellite Hs, m)
  - Y axis: model_sigWaveHeight_weighted (model Hs, m)
  - Quality filter: obs_swh_quality_level >= altimetry_plot_min_quality (default 3)
  - Dashed 1:1 line (black)
  - Solid OLS regression line (red)
  - Upper-left stats box: R², RMSE, Bias, regression equation

Inputs
------
  P{ID}/P{ID}_collocate_altimetry/collocated_hs.nc
  fix/hgrid.gr3  (for mesh boundaries)

Outputs
-------
  P{ID}/P{ID}_collocate_altimetry/altimetry_tracks_by_source.jpg
  P{ID}/P{ID}_collocate_altimetry/altimetry_tracks_by_time.jpg
  P{ID}/P{ID}_collocate_altimetry/altimetry_scatter_time_delta.jpg
  P{ID}/P{ID}_collocate_altimetry/altimetry_scatter_dist_delta.jpg
  P{ID}/P{ID}_collocate_altimetry/altimetry_scatter_depth.jpg
  P{ID}/P{ID}_collocate_altimetry/altimetry_scatter_coast_dist.jpg
  P{ID}/P{ID}_collocate_altimetry/altimetry_scatter_source.jpg
  P{ID}/P{ID}_collocate_altimetry/plot_altimetry.done

Config keys (postprocess.yaml) — all optional
----------------------------------------------
  altimetry_plot_dpi                 150
  altimetry_plot_min_quality         3      (obs_swh_quality_level threshold)
  altimetry_plot_max_dist_km         null   (optional distance filter)
  altimetry_scatter_vmax_hs          null   (null = 98th percentile)
  altimetry_scatter_time_delta_vmax  null   (minutes, null = auto)
  altimetry_scatter_dist_delta_vmax  null   (km, null = auto)
"""

import argparse
import warnings
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.cm as mcm
import matplotlib.colors as mcolors
from matplotlib.lines import Line2D

warnings.filterwarnings("ignore", category=RuntimeWarning)

from workflow.core.config import load_config, model_dir
from workflow.core.plot_style import (
    read_mesh_boundaries,
    _draw_boundaries,
    _aspect_figsize,
    TITLE_FS, LABEL_FS, TICK_FS, CBAR_FS,
    PADDING_LON, PADDING_LAT,
)


# =============================================================================
# Path helpers
# =============================================================================

def _out_dir(cfg: dict) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_collocate_altimetry"


def _load_boundaries(cfg: dict):
    mdir = model_dir(cfg)
    for hp in (mdir / "fix" / "hgrid.gr3",
               mdir / "fix" / "hgrid.ll"):
        if hp.exists():
            try:
                return read_mesh_boundaries(hp)
            except Exception as exc:
                print(f"  [plot_altimetry] could not load "
                      f"boundaries: {exc}")
    print("  [plot_altimetry] hgrid.gr3/.ll not found — "
          "plotting without boundaries.")
    return None


def _load_collocated(out_dir: Path):
    import xarray as xr
    nc = out_dir / "collocated_hs.nc"
    if not (nc.exists() and nc.stat().st_size > 0):
        print(f"  [plot_altimetry] {nc.name} not found.")
        return None
    ds = xr.open_dataset(str(nc), engine="netcdf4")
    print(f"  [plot_altimetry] loaded {nc.name}  "
          f"({ds.sizes.get('time', '?')} observations)")
    return ds


# =============================================================================
# Config helpers
# =============================================================================

def _pct_vmax(arr, pct=98, round_to=0.5):
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return 1.0
    raw = float(np.percentile(finite, pct))
    return max(round_to, np.ceil(raw / round_to) * round_to)


def _domain_extent(boundaries):
    """Return (lon_min, lon_max, lat_min, lat_max) for map plots."""
    if boundaries and "mesh_extent" in boundaries:
        ext = boundaries["mesh_extent"]
        return (ext[0] - PADDING_LON,
                ext[1] + PADDING_LON,
                ext[2] - PADDING_LAT,
                ext[3] + PADDING_LAT)
    return None


# =============================================================================
# Quality + distance filter
# =============================================================================

def _apply_filters(ds, cfg: dict):
    """Apply quality and optional distance filters. Returns filtered arrays."""
    import xarray as xr

    min_quality = int(cfg.get(
        "altimetry_plot_min_quality", 3))
    max_dist_km = cfg.get("altimetry_plot_max_dist_km")

    quality = np.array(ds["obs_swh_quality_level"])
    mask    = quality >= min_quality

    if max_dist_km is not None:
        dist_m  = np.array(
            ds["dist_deltas"]).min(axis=1)
        mask &= dist_m < float(max_dist_km) * 1000.0

    # Also require finite obs and model values
    obs_swh = np.array(ds["obs_swh_adjusted"])
    mod_swh = np.array(
        ds["model_sigWaveHeight_weighted"])
    mask &= np.isfinite(obs_swh)
    mask &= np.isfinite(mod_swh)

    n_total = len(mask)
    n_keep  = int(mask.sum())
    print(f"  [plot_altimetry] quality filter "
          f"(>={min_quality}): keeping "
          f"{n_keep}/{n_total} observations")

    return mask


# =============================================================================
# Skill metrics
# =============================================================================

def _compute_skill(obs: np.ndarray,
                   mod: np.ndarray) -> dict:
    """Compute OLS regression + skill metrics."""
    finite = np.isfinite(obs) & np.isfinite(mod)
    o = obs[finite]
    m = mod[finite]
    if len(o) < 2:
        return None

    bias = float(np.mean(m - o))
    rmse = float(np.sqrt(np.mean((m - o) ** 2)))
    r2   = (float(np.corrcoef(o, m)[0, 1] ** 2)
            if (np.var(o) > 1e-8
                and np.var(m) > 1e-8)
            else np.nan)

    # OLS: m = a*o + b
    A    = np.vstack([o, np.ones(len(o))]).T
    a, b = np.linalg.lstsq(A, m, rcond=None)[0]

    return {"r2": r2, "rmse": rmse,
            "bias": bias, "a": a, "b": b,
            "n": len(o)}


def _stats_box(ax, skill: dict, unit: str = "m"):
    """Add a stats box to the upper-left of an axes."""
    sign_b = "+" if skill["b"] >= 0 else "-"
    txt = (
        f"$R^2$  = {skill['r2']:.2f}\n"
        f"RMSE = {skill['rmse']:.2f} {unit}\n"
        f"Bias  = {skill['bias']:.2f} {unit}\n"
        f"$y$ = {skill['a']:.2f}$x$ "
        f"{sign_b} {abs(skill['b']):.2f}"
    )
    ax.text(
        0.03, 0.97, txt,
        transform=ax.transAxes,
        va="top", ha="left", fontsize=TICK_FS,
        bbox=dict(facecolor="white", alpha=0.85,
                  edgecolor="0.7",
                  boxstyle="round,pad=0.4"))


# =============================================================================
# Map plot helpers
# =============================================================================

def _save_map(fig, out_path: Path, dpi: int):
    fig.savefig(str(out_path), dpi=dpi,
                format="jpeg", bbox_inches="tight",
                pil_kwargs={"quality": 90})
    plt.close(fig)
    print(f"  [plot_altimetry] -> {out_path.name}")


def _make_map_axes(boundaries):
    """Create a figure + axes sized to the mesh domain."""
    ext = _domain_extent(boundaries)
    if ext:
        lon_min, lon_max, lat_min, lat_max = ext
    else:
        lon_min, lon_max = 150, 230
        lat_min, lat_max = 45, 78
    fw, fh = _aspect_figsize(
        lon_min, lon_max, lat_min, lat_max)
    fig, ax = plt.subplots(
        figsize=(fw, fh + 0.8),
        constrained_layout=True)
    return fig, ax, lon_min, lon_max, lat_min, lat_max


def _style_map_ax(ax, lon_min, lon_max,
                  lat_min, lat_max, title):
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_xlabel("Longitude (°E)",
                  fontsize=LABEL_FS)
    ax.set_ylabel("Latitude (°N)",
                  fontsize=LABEL_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=TITLE_FS,
                 fontweight="bold", pad=10)
    ax.grid(True, linestyle="--",
            alpha=0.3, zorder=0)


# =============================================================================
# Plot 1 — Tracks by source
# =============================================================================

def plot_tracks_by_source(cfg: dict, ds,
                           out_dir: Path,
                           boundaries, dpi: int):
    lons    = np.array(ds["lon_obs"])
    lats    = np.array(ds["lat_obs"])
    sources = np.array(ds["source_obs"],
                       dtype=str)

    unique_sources = sorted(set(sources))
    n_src          = len(unique_sources)
    cmap_cat       = mcm.get_cmap("tab20", n_src)
    src_to_idx     = {s: i
                      for i, s in
                      enumerate(unique_sources)}

    fig, ax, lon_min, lon_max, lat_min, lat_max = \
        _make_map_axes(boundaries)

    _draw_boundaries(ax, boundaries)

    s_size = float(cfg.get(
        "collocate_argo_plot_location_s", 14))

    for src in unique_sources:
        mask = sources == src
        ax.scatter(
            lons[mask], lats[mask],
            color=cmap_cat(src_to_idx[src]),
            s=s_size, linewidths=0,
            zorder=5, label=src)

    ax.legend(
        loc="lower right",
        fontsize=max(TICK_FS - 1, 7),
        framealpha=0.85,
        markerscale=1.5,
        title="Satellite",
        title_fontsize=TICK_FS)

    start = cfg["start_date"]
    end   = cfg["end_date"]
    _style_map_ax(
        ax, lon_min, lon_max,
        lat_min, lat_max,
        f"Satellite Altimetry Tracks by Source  |  "
        f"n = {len(lons):,}  |  "
        f"{start} to {end}")

    out_path = (out_dir
                / "altimetry_tracks_by_source.jpg")
    _save_map(fig, out_path, dpi)


# =============================================================================
# Plot 2 — Tracks by time
# =============================================================================

def plot_tracks_by_time(cfg: dict, ds,
                         out_dir: Path,
                         boundaries, dpi: int):
    import pandas as pd

    lons  = np.array(ds["lon_obs"])
    lats  = np.array(ds["lat_obs"])
    times = ds["time_obs"].values

    time_nums = mdates.date2num(
        pd.to_datetime(times)
        .to_pydatetime())

    vmin_t = mdates.date2num(
        pd.Timestamp(cfg["start_date"])
        .to_pydatetime())
    vmax_t = mdates.date2num(
        pd.Timestamp(cfg["end_date"])
        .to_pydatetime())

    fig, ax, lon_min, lon_max, lat_min, lat_max = \
        _make_map_axes(boundaries)

    _draw_boundaries(ax, boundaries)

    s_size = float(cfg.get(
        "collocate_argo_plot_location_s", 14))

    sc = ax.scatter(
        lons, lats,
        c=time_nums,
        cmap="turbo",
        vmin=vmin_t, vmax=vmax_t,
        s=s_size, linewidths=0,
        zorder=5)

    cbar = fig.colorbar(
        sc, ax=ax,
        orientation="horizontal",
        pad=0.06, shrink=0.85, aspect=40)
    cbar.set_label("Observation Date",
                   fontsize=CBAR_FS)
    cbar.ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%Y-%m"))
    cbar.ax.tick_params(
        labelsize=TICK_FS, rotation=30)

    start = cfg["start_date"]
    end   = cfg["end_date"]
    _style_map_ax(
        ax, lon_min, lon_max,
        lat_min, lat_max,
        f"Satellite Altimetry Tracks by Time  |  "
        f"n = {len(lons):,}  |  "
        f"{start} to {end}")

    out_path = (out_dir
                / "altimetry_tracks_by_time.jpg")
    _save_map(fig, out_path, dpi)


# =============================================================================
# Scatter plot base builder
# =============================================================================

def _make_scatter(obs: np.ndarray,
                  mod: np.ndarray,
                  color_vals,
                  cmap,
                  cbar_label: str,
                  title: str,
                  out_path: Path,
                  cfg: dict,
                  dpi: int,
                  vmin=None, vmax=None,
                  categorical_legend=None):
    """
    Build one scatter plot.

    categorical_legend: list of (label, color) tuples for
        categorical coloring (source). If provided, color_vals
        is treated as an array of integer indices.
    """
    skill = _compute_skill(obs, mod)
    if skill is None:
        print(f"  [plot_altimetry] insufficient data "
              f"for {out_path.name}, skipping.")
        return

    # Axis limits: square, based on combined range
    vmax_hs = cfg.get("altimetry_scatter_vmax_hs")
    if vmax_hs is not None:
        ax_max = float(vmax_hs)
    else:
        ax_max = _pct_vmax(
            np.concatenate([obs, mod]),
            pct=98, round_to=0.5)
    ax_max = max(ax_max, 0.5)

    # OLS regression line
    x_line = np.array([0, ax_max])
    y_line = skill["a"] * x_line + skill["b"]

    fig, ax = plt.subplots(
        figsize=(6, 6),
        constrained_layout=True)

    if categorical_legend is not None:
        # Categorical scatter (source)
        unique_vals = np.unique(color_vals)
        for idx in unique_vals:
            mask  = color_vals == idx
            label, color = categorical_legend[int(idx)]
            ax.scatter(
                obs[mask], mod[mask],
                c=[color],
                s=4, alpha=0.4,
                linewidths=0,
                label=label, zorder=4)
        ax.legend(
            loc="lower right",
            fontsize=max(TICK_FS - 1, 7),
            framealpha=0.85,
            markerscale=2,
            title="Satellite",
            title_fontsize=TICK_FS)
        # dummy mappable for consistency
        sc = None
    else:
        sc = ax.scatter(
            obs, mod,
            c=color_vals,
            cmap=cmap,
            vmin=vmin, vmax=vmax,
            s=4, alpha=0.4,
            linewidths=0, zorder=4)
        cbar = fig.colorbar(
            sc, ax=ax,
            location="right",
            pad=0.02,
            fraction=0.046,
            shrink=0.9)
        cbar.set_label(cbar_label,
                       fontsize=CBAR_FS)
        cbar.ax.tick_params(
            labelsize=TICK_FS)

    # 1:1 line
    ax.plot(x_line, x_line,
            color="black",
            linestyle="--",
            linewidth=1.2,
            zorder=5,
            label="1:1")

    # Regression line
    ax.plot(x_line, y_line,
            color="red",
            linestyle="-",
            linewidth=1.5,
            zorder=6,
            label="Regression")

    _stats_box(ax, skill)

    ax.set_xlim(0, ax_max)
    ax.set_ylim(0, ax_max)
    ax.set_xlabel("Observed $H_s$ (m)",
                  fontsize=LABEL_FS)
    ax.set_ylabel("Model $H_s$ (m)",
                  fontsize=LABEL_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=TITLE_FS,
                 fontweight="bold", pad=10)
    ax.grid(True, linestyle="--",
            alpha=0.3, zorder=0)

    fig.savefig(
        str(out_path), dpi=dpi,
        format="jpeg", bbox_inches="tight",
        pil_kwargs={"quality": 90})
    plt.close(fig)
    print(f"  [plot_altimetry] -> {out_path.name}")


# =============================================================================
# Scatter Plot 1 — Time delta
# =============================================================================

def plot_scatter_time_delta(cfg: dict, ds,
                             mask: np.ndarray,
                             out_dir: Path,
                             dpi: int):
    obs = np.array(
        ds["obs_swh_adjusted"])[mask]
    mod = np.array(
        ds["model_sigWaveHeight_weighted"])[mask]

    # time_deltas in nanoseconds -> minutes
    td_ns  = np.array(
        ds["time_deltas"])[mask]
    td_min = np.abs(td_ns) / 1e9 / 60.0

    vmax_cfg = cfg.get(
        "altimetry_scatter_time_delta_vmax")
    vmax = (float(vmax_cfg)
            if vmax_cfg is not None
            else _pct_vmax(td_min, 98, 10.0))

    _make_scatter(
        obs, mod,
        color_vals=td_min,
        cmap="RdYlGn_r",
        cbar_label="Time delta (min)",
        title="Model vs Observed $H_s$ — "
              "coloured by time delta",
        out_path=(out_dir
                  / "altimetry_scatter_time_delta"
                  ".jpg"),
        cfg=cfg, dpi=dpi,
        vmin=0, vmax=vmax)


# =============================================================================
# Scatter Plot 2 — Distance delta
# =============================================================================

def plot_scatter_dist_delta(cfg: dict, ds,
                             mask: np.ndarray,
                             out_dir: Path,
                             dpi: int):
    obs = np.array(
        ds["obs_swh_adjusted"])[mask]
    mod = np.array(
        ds["model_sigWaveHeight_weighted"])[mask]

    # dist_deltas (time, nearest_nodes) -> min -> km
    dist_m  = np.array(
        ds["dist_deltas"])[mask].min(axis=1)
    dist_km = dist_m / 1000.0

    vmax_cfg = cfg.get(
        "altimetry_scatter_dist_delta_vmax")
    vmax = (float(vmax_cfg)
            if vmax_cfg is not None
            else _pct_vmax(dist_km, 98, 1.0))

    _make_scatter(
        obs, mod,
        color_vals=dist_km,
        cmap="plasma",
        cbar_label="Distance delta (km)",
        title="Model vs Observed $H_s$ — "
              "coloured by distance delta",
        out_path=(out_dir
                  / "altimetry_scatter_dist_delta"
                  ".jpg"),
        cfg=cfg, dpi=dpi,
        vmin=0, vmax=vmax)


# =============================================================================
# Scatter Plot 3 — Model depth
# =============================================================================

def plot_scatter_depth(cfg: dict, ds,
                        mask: np.ndarray,
                        out_dir: Path,
                        dpi: int):
    obs = np.array(
        ds["obs_swh_adjusted"])[mask]
    mod = np.array(
        ds["model_sigWaveHeight_weighted"])[mask]

    # model_dpt (time, nearest_nodes) -> mean -> m
    depth = np.array(
        ds["model_dpt"])[mask].mean(axis=1)

    vmax = _pct_vmax(depth, 98, 100.0)

    _make_scatter(
        obs, mod,
        color_vals=depth,
        cmap="viridis",
        cbar_label="Model depth (m)",
        title="Model vs Observed $H_s$ — "
              "coloured by model depth",
        out_path=(out_dir
                  / "altimetry_scatter_depth.jpg"),
        cfg=cfg, dpi=dpi,
        vmin=0, vmax=vmax)


# =============================================================================
# Scatter Plot 4 — Distance to coast
# =============================================================================

def plot_scatter_coast_dist(cfg: dict, ds,
                             mask: np.ndarray,
                             out_dir: Path,
                             dpi: int):
    obs = np.array(
        ds["obs_swh_adjusted"])[mask]
    mod = np.array(
        ds["model_sigWaveHeight_weighted"])[mask]

    coast_m  = np.array(
        ds["obs_distance_to_coast"])[mask]
    coast_km = coast_m / 1000.0

    vmax = _pct_vmax(coast_km, 98, 50.0)

    _make_scatter(
        obs, mod,
        color_vals=coast_km,
        cmap="YlOrRd_r",
        cbar_label="Distance to coast (km)",
        title="Model vs Observed $H_s$ — "
              "coloured by distance to coast",
        out_path=(out_dir
                  / "altimetry_scatter_coast_dist"
                  ".jpg"),
        cfg=cfg, dpi=dpi,
        vmin=0, vmax=vmax)


# =============================================================================
# Scatter Plot 5 — Satellite source
# =============================================================================

def plot_scatter_source(cfg: dict, ds,
                         mask: np.ndarray,
                         out_dir: Path,
                         dpi: int):
    obs = np.array(
        ds["obs_swh_adjusted"])[mask]
    mod = np.array(
        ds["model_sigWaveHeight_weighted"])[mask]

    sources = np.array(
        ds["source_obs"], dtype=str)[mask]

    unique_sources = sorted(set(sources))
    n_src          = len(unique_sources)
    cmap_cat       = mcm.get_cmap("tab20", n_src)
    src_to_idx     = {s: i
                      for i, s in
                      enumerate(unique_sources)}

    color_idx = np.array(
        [src_to_idx[s] for s in sources],
        dtype=int)

    categorical_legend = [
        (s, cmap_cat(src_to_idx[s]))
        for s in unique_sources]

    _make_scatter(
        obs, mod,
        color_vals=color_idx,
        cmap=None,
        cbar_label="Satellite",
        title="Model vs Observed $H_s$ — "
              "coloured by satellite source",
        out_path=(out_dir
                  / "altimetry_scatter_source.jpg"),
        cfg=cfg, dpi=dpi,
        categorical_legend=categorical_legend)


# =============================================================================
# Top-level entry point
# =============================================================================

def run_plot_altimetry(cfg: dict,
                        config_dir=None):
    out_dir = _out_dir(cfg)
    if not out_dir.is_dir():
        print(f"ERROR: collocation output dir not "
              f"found: {out_dir}")
        return

    nc_path = out_dir / "collocated_hs.nc"
    if not (nc_path.exists()
            and nc_path.stat().st_size > 0):
        print(f"ERROR: {nc_path.name} not found. "
              f"Run collocate_altimetry first.")
        return

    dpi = int(cfg.get(
        "altimetry_plot_dpi", 150))

    print(f"\n{'='*60}")
    print(f"  Altimetry diagnostic plots")
    print(f"  DPI: {dpi}")
    print(f"  Output: {out_dir}")
    print(f"{'='*60}\n")

    boundaries = _load_boundaries(cfg)
    ds         = _load_collocated(out_dir)
    if ds is None:
        return

    # ---- Map plots (all observations, no filter) ----
    plot_tracks_by_source(
        cfg, ds, out_dir, boundaries, dpi)
    plot_tracks_by_time(
        cfg, ds, out_dir, boundaries, dpi)

    # ---- Quality + distance filter for scatter ----
    mask = _apply_filters(ds, cfg)
    if not mask.any():
        print("  [plot_altimetry] no observations "
              "pass the quality filter. "
              "Scatter plots skipped.")
        ds.close()
        return

    n = int(mask.sum())
    print(f"  [plot_altimetry] {n} observations "
          f"pass filters -> scatter plots")

    # ---- Scatter plots ----
    plot_scatter_time_delta(
        cfg, ds, mask, out_dir, dpi)
    plot_scatter_dist_delta(
        cfg, ds, mask, out_dir, dpi)
    plot_scatter_depth(
        cfg, ds, mask, out_dir, dpi)
    plot_scatter_coast_dist(
        cfg, ds, mask, out_dir, dpi)
    plot_scatter_source(
        cfg, ds, mask, out_dir, dpi)

    ds.close()

    (out_dir / "plot_altimetry.done").touch()
    print(f"\n{'='*60}")
    print(f"  Altimetry plots complete. "
          f"All figures in {out_dir}")
    print(f"{'='*60}\n")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Altimetry diagnostic plots")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    run_plot_altimetry(
        load_config(Path(args.config)),
        args.config)
