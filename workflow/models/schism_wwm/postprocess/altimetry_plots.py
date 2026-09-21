"""
models/schism_wwm/postprocess/altimetry_plots.py
=================================================
Phase 5 step "plot_altimetry" (interactive; runs in the swf_plot env).

Produces seven diagnostic plots from the collocated altimetry NetCDF
written by the ``collocate_altimetry`` step:

  Plot 1 — Satellite track map by source (altimetry_tracks_by_source.jpg)
  Plot 2 — Satellite track map by time (altimetry_tracks_by_time.jpg)
  Scatter Plot 1 — Coloured by time delta (altimetry_scatter_time_delta.jpg)
  Scatter Plot 2 — Coloured by distance delta (altimetry_scatter_dist_delta.jpg)
  Scatter Plot 3 — Coloured by model depth (altimetry_scatter_depth.jpg)
  Scatter Plot 4 — Coloured by distance to coast (altimetry_scatter_coast_dist.jpg)
  Scatter Plot 5 — Coloured by satellite source (altimetry_scatter_source.jpg)

Input file preference:
    1. collocated_hs_clean.nc  (distance-filtered, written by run_merge
                                when collocate_altimetry_dist_threshold_km
                                is set)
    2. collocated_hs.nc        (all collocated points, fallback)

All scatter plots apply additional plot-time filters:
    obs_swh_quality_level >= altimetry_plot_min_quality (default 3)
    |time_delta| <= altimetry_plot_max_time_delta_s (default 1800 s)
    dist_delta <= altimetry_plot_max_dist_km (default null = no filter)

Map plots show ALL observations (no filters) with lon_obs converted
from -180..180 to 0..360 for correct display on the Alaska domain.

Inputs
------
  P{ID}/P{ID}_collocate_altimetry/collocated_hs_clean.nc  (preferred)
  P{ID}/P{ID}_collocate_altimetry/collocated_hs.nc        (fallback)
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
  altimetry_plot_dpi                  150
  altimetry_plot_min_quality          3
  altimetry_plot_max_time_delta_s     1800   seconds
  altimetry_plot_max_dist_km          null   km, null = no filter
  altimetry_plot_location_s           7      map marker size
  altimetry_scatter_vmax_hs           null   null = 98th percentile
  altimetry_scatter_time_delta_vmax   1800   seconds
  altimetry_scatter_dist_delta_vmax   null   km, null = auto
"""

import argparse
import warnings
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

warnings.filterwarnings("ignore",
                         category=RuntimeWarning)

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
    return (model_dir(cfg)
            / f"P{pid}"
            / f"P{pid}_collocate_altimetry")


def _load_boundaries(cfg: dict):
    mdir = model_dir(cfg)
    for hp in (mdir / "fix" / "hgrid.gr3",
               mdir / "fix" / "hgrid.ll"):
        if hp.exists():
            try:
                return read_mesh_boundaries(hp)
            except Exception as exc:
                print(f"  [plot_altimetry] could "
                      f"not load boundaries: {exc}")
    print("  [plot_altimetry] hgrid.gr3/.ll not "
          "found — plotting without boundaries.")
    return None


def _load_collocated(out_dir: Path):
    """Load collocated_hs_clean.nc if it exists,
    otherwise fall back to collocated_hs.nc.

    Mirrors the same preference pattern used in
    argo_plots.py for collocated Argo data.
    """
    import xarray as xr

    clean_nc = out_dir / "collocated_hs_clean.nc"
    full_nc  = out_dir / "collocated_hs.nc"

    if (clean_nc.exists()
            and clean_nc.stat().st_size > 0):
        nc = clean_nc
        print(f"  [plot_altimetry] loading "
              f"{nc.name} (distance-filtered)")
    elif (full_nc.exists()
            and full_nc.stat().st_size > 0):
        nc = full_nc
        print(f"  [plot_altimetry] loading "
              f"{nc.name} (all points — "
              f"collocated_hs_clean.nc not found; "
              f"set collocate_altimetry_dist_"
              f"threshold_km in postprocess.yaml "
              f"to generate a clean file)")
    else:
        print(f"  [plot_altimetry] neither "
              f"collocated_hs_clean.nc nor "
              f"collocated_hs.nc found in "
              f"{out_dir}.")
        return None

    ds = xr.open_dataset(str(nc), engine="netcdf4")
    print(f"  [plot_altimetry] "
          f"{ds.sizes.get('time', '?')} "
          f"observations loaded")
    return ds


# =============================================================================
# Longitude conversion
# =============================================================================

def _lon_to_360(lon_arr: np.ndarray) -> np.ndarray:
    """Convert longitude array from -180..180 to 0..360.

    The collocated file stores lon_obs in -180..180
    (CCI convention). The Alaska/Bering Sea mesh
    domain is in 0..360. This ensures observations
    plot correctly across the full domain (150..230).
    """
    arr = np.asarray(lon_arr, dtype=float)
    return np.where(arr < 0, arr + 360.0, arr)


# =============================================================================
# Config helpers
# =============================================================================

def _pct_vmax(arr, pct=98, round_to=0.5):
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return 1.0
    raw = float(np.percentile(finite, pct))
    return max(round_to,
               np.ceil(raw / round_to) * round_to)


def _domain_extent(boundaries):
    if boundaries and "mesh_extent" in boundaries:
        ext = boundaries["mesh_extent"]
        return (ext[0] - PADDING_LON,
                ext[1] + PADDING_LON,
                ext[2] - PADDING_LAT,
                ext[3] + PADDING_LAT)
    return None


# =============================================================================
# Filters
# =============================================================================

def _apply_filters(ds, cfg: dict) -> np.ndarray:
    """Apply plot-time quality, time delta, and
    distance filters.

    These are secondary filters applied after any
    collocation-time distance filter already baked
    into collocated_hs_clean.nc.

    Filters:
      1. obs_swh_quality_level >= min_quality
      2. |time_deltas| <= max_time_delta_s (s)
      3. min(dist_deltas) <= max_dist_km (km) [opt]
      4. finite obs and model values
    """
    min_quality = int(cfg.get(
        "altimetry_plot_min_quality", 3))
    max_td_s    = float(cfg.get(
        "altimetry_plot_max_time_delta_s", 1800))
    max_dist_km = cfg.get(
        "altimetry_plot_max_dist_km")

    # 1. Quality
    quality = np.array(
        ds["obs_swh_quality_level"])
    mask    = quality >= min_quality

    # 2. Time delta (nanoseconds -> seconds)
    td_ns = np.array(ds["time_deltas"])
    td_s  = np.abs(td_ns) / 1e9
    mask &= td_s <= max_td_s

    # 3. Distance (optional)
    if max_dist_km is not None:
        dist_m = np.array(
            ds["dist_deltas"]).min(axis=1)
        mask &= (dist_m
                 < float(max_dist_km) * 1000.0)

    # 4. Finite values
    obs_swh = np.array(ds["obs_swh_adjusted"])
    mod_swh = np.array(
        ds["model_sigWaveHeight_weighted"])
    mask &= np.isfinite(obs_swh)
    mask &= np.isfinite(mod_swh)

    n_total = len(mask)
    n_keep  = int(mask.sum())
    print(f"  [plot_altimetry] plot-time filters:")
    print(f"    quality >= {min_quality}")
    print(f"    |time delta| <= {max_td_s:.0f} s")
    if max_dist_km is not None:
        print(f"    dist <= {max_dist_km} km")
    print(f"    keeping {n_keep}/{n_total} "
          f"observations")

    return mask


# =============================================================================
# Skill metrics
# =============================================================================

def _compute_skill(obs: np.ndarray,
                   mod: np.ndarray) -> dict:
    finite = np.isfinite(obs) & np.isfinite(mod)
    o = obs[finite]
    m = mod[finite]
    if len(o) < 2:
        return None

    bias = float(np.mean(m - o))
    rmse = float(
        np.sqrt(np.mean((m - o) ** 2)))
    r2   = (
        float(np.corrcoef(o, m)[0, 1] ** 2)
        if (np.var(o) > 1e-8
            and np.var(m) > 1e-8)
        else np.nan)

    A    = np.vstack(
        [o, np.ones(len(o))]).T
    a, b = np.linalg.lstsq(
        A, m, rcond=None)[0]

    return {"r2": r2, "rmse": rmse,
            "bias": bias, "a": a, "b": b,
            "n": len(o)}


def _stats_box(ax, skill: dict,
               unit: str = "m"):
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
        va="top", ha="left",
        fontsize=TICK_FS,
        bbox=dict(
            facecolor="white", alpha=0.85,
            edgecolor="0.7",
            boxstyle="round,pad=0.4"))


# =============================================================================
# Map plot helpers
# =============================================================================

def _save_fig(fig, out_path: Path, dpi: int):
    fig.savefig(
        str(out_path), dpi=dpi,
        format="jpeg", bbox_inches="tight",
        pil_kwargs={"quality": 90})
    plt.close(fig)
    print(f"  [plot_altimetry] "
          f"-> {out_path.name}")


def _make_map_axes(boundaries):
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
    return (fig, ax,
            lon_min, lon_max,
            lat_min, lat_max)


def _style_map_ax(ax,
                  lon_min, lon_max,
                  lat_min, lat_max,
                  title):
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
                           boundaries,
                           dpi: int):
    lons    = _lon_to_360(
        np.array(ds["lon_obs"]))
    lats    = np.array(ds["lat_obs"])
    sources = np.array(
        ds["source_obs"], dtype=str)

    unique_sources = sorted(set(sources))
    n_src          = len(unique_sources)
    cmap_cat       = matplotlib.colormaps[
        "tab20"].resampled(n_src)
    src_to_idx     = {s: i for i, s in
                      enumerate(unique_sources)}

    fig, ax, lon_min, lon_max, \
        lat_min, lat_max = \
        _make_map_axes(boundaries)

    _draw_boundaries(ax, boundaries)

    s_size = float(cfg.get(
        "altimetry_plot_location_s", 7))

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
        f"Satellite Altimetry Tracks by Source"
        f"  |  n = {len(lons):,}"
        f"  |  {start} to {end}")

    _save_fig(
        fig,
        out_dir
        / "altimetry_tracks_by_source.jpg",
        dpi)


# =============================================================================
# Plot 2 — Tracks by time
# =============================================================================

def plot_tracks_by_time(cfg: dict, ds,
                         out_dir: Path,
                         boundaries,
                         dpi: int):
    import pandas as pd

    lons  = _lon_to_360(
        np.array(ds["lon_obs"]))
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

    fig, ax, lon_min, lon_max, \
        lat_min, lat_max = \
        _make_map_axes(boundaries)

    _draw_boundaries(ax, boundaries)

    s_size = float(cfg.get(
        "altimetry_plot_location_s", 7))

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
        mdates.DateFormatter("%Y-%m-%d"))
    cbar.ax.tick_params(
        labelsize=TICK_FS, rotation=30)

    start = cfg["start_date"]
    end   = cfg["end_date"]
    _style_map_ax(
        ax, lon_min, lon_max,
        lat_min, lat_max,
        f"Satellite Altimetry Tracks by Time"
        f"  |  n = {len(lons):,}"
        f"  |  {start} to {end}")

    _save_fig(
        fig,
        out_dir
        / "altimetry_tracks_by_time.jpg",
        dpi)


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
                  vmin=None,
                  vmax=None,
                  categorical_legend=None):
    skill = _compute_skill(obs, mod)
    if skill is None:
        print(f"  [plot_altimetry] insufficient "
              f"data for {out_path.name}, "
              f"skipping.")
        return

    vmax_hs = cfg.get(
        "altimetry_scatter_vmax_hs")
    if vmax_hs is not None:
        ax_max = float(vmax_hs)
    else:
        ax_max = _pct_vmax(
            np.concatenate([obs, mod]),
            pct=98, round_to=0.5)
    ax_max = max(ax_max, 0.5)

    x_line = np.array([0, ax_max])
    y_line = (skill["a"] * x_line
              + skill["b"])

    fig, ax = plt.subplots(
        figsize=(6, 6),
        constrained_layout=True)

    if categorical_legend is not None:
        unique_vals = np.unique(color_vals)
        for idx in unique_vals:
            mask  = color_vals == idx
            label, color = (
                categorical_legend[int(idx)])
            ax.scatter(
                obs[mask], mod[mask],
                c=[color],
                s=4,
                linewidths=0,
                label=label,
                zorder=4)
        ax.legend(
            loc="lower right",
            fontsize=max(TICK_FS - 1, 7),
            framealpha=0.85,
            markerscale=2,
            title="Satellite",
            title_fontsize=TICK_FS)
    else:
        sc = ax.scatter(
            obs, mod,
            c=color_vals,
            cmap=cmap,
            vmin=vmin, vmax=vmax,
            s=4,
            linewidths=0,
            zorder=4)
        cbar = fig.colorbar(
            sc, ax=ax,
            location="right",
            pad=0.02,
            fraction=0.046,
            shrink=0.9)
        cbar.set_label(
            cbar_label,
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

    # OLS regression line
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
        format="jpeg",
        bbox_inches="tight",
        pil_kwargs={"quality": 90})
    plt.close(fig)
    print(f"  [plot_altimetry] "
          f"-> {out_path.name}")


# =============================================================================
# Scatter Plot 1 — Time delta (seconds)
# =============================================================================

def plot_scatter_time_delta(cfg: dict, ds,
                             mask: np.ndarray,
                             out_dir: Path,
                             dpi: int):
    obs   = np.array(
        ds["obs_swh_adjusted"])[mask]
    mod   = np.array(
        ds["model_sigWaveHeight_weighted"])[mask]
    td_ns = np.array(ds["time_deltas"])[mask]
    td_s  = np.abs(td_ns) / 1e9

    vmax_cfg = cfg.get(
        "altimetry_scatter_time_delta_vmax")
    vmax = (float(vmax_cfg)
            if vmax_cfg is not None
            else 1800.0)

    _make_scatter(
        obs, mod,
        color_vals=td_s,
        cmap="RdYlGn_r",
        cbar_label="Time delta (s)",
        title=("Model vs Observed $H_s$  |  "
               "coloured by time delta"),
        out_path=(
            out_dir
            / "altimetry_scatter_time_delta"
            ".jpg"),
        cfg=cfg, dpi=dpi,
        vmin=0, vmax=vmax)


# =============================================================================
# Scatter Plot 2 — Distance delta (km)
# =============================================================================

def plot_scatter_dist_delta(cfg: dict, ds,
                             mask: np.ndarray,
                             out_dir: Path,
                             dpi: int):
    obs     = np.array(
        ds["obs_swh_adjusted"])[mask]
    mod     = np.array(
        ds["model_sigWaveHeight_weighted"])[mask]
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
        title=("Model vs Observed $H_s$  |  "
               "coloured by distance delta"),
        out_path=(
            out_dir
            / "altimetry_scatter_dist_delta"
            ".jpg"),
        cfg=cfg, dpi=dpi,
        vmin=0, vmax=vmax)


# =============================================================================
# Scatter Plot 3 — Model depth (m)
# =============================================================================

def plot_scatter_depth(cfg: dict, ds,
                        mask: np.ndarray,
                        out_dir: Path,
                        dpi: int):
    obs   = np.array(
        ds["obs_swh_adjusted"])[mask]
    mod   = np.array(
        ds["model_sigWaveHeight_weighted"])[mask]
    depth = np.array(
        ds["model_dpt"])[mask].mean(axis=1)

    vmax = _pct_vmax(depth, 98, 100.0)

    _make_scatter(
        obs, mod,
        color_vals=depth,
        cmap="viridis",
        cbar_label="Model depth (m)",
        title=("Model vs Observed $H_s$  |  "
               "coloured by model depth"),
        out_path=(
            out_dir
            / "altimetry_scatter_depth.jpg"),
        cfg=cfg, dpi=dpi,
        vmin=0, vmax=vmax)


# =============================================================================
# Scatter Plot 4 — Distance to coast (km)
# =============================================================================

def plot_scatter_coast_dist(cfg: dict, ds,
                             mask: np.ndarray,
                             out_dir: Path,
                             dpi: int):
    obs      = np.array(
        ds["obs_swh_adjusted"])[mask]
    mod      = np.array(
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
        title=("Model vs Observed $H_s$  |  "
               "coloured by distance to coast"),
        out_path=(
            out_dir
            / "altimetry_scatter_coast_dist"
            ".jpg"),
        cfg=cfg, dpi=dpi,
        vmin=0, vmax=vmax)


# =============================================================================
# Scatter Plot 5 — Satellite source (categorical)
# =============================================================================

def plot_scatter_source(cfg: dict, ds,
                         mask: np.ndarray,
                         out_dir: Path,
                         dpi: int):
    obs     = np.array(
        ds["obs_swh_adjusted"])[mask]
    mod     = np.array(
        ds["model_sigWaveHeight_weighted"])[mask]
    sources = np.array(
        ds["source_obs"], dtype=str)[mask]

    unique_sources = sorted(set(sources))
    n_src          = len(unique_sources)
    cmap_cat       = matplotlib.colormaps[
        "tab20"].resampled(n_src)
    src_to_idx     = {s: i for i, s in
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
        title=("Model vs Observed $H_s$  |  "
               "coloured by satellite source"),
        out_path=(
            out_dir
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
        print(f"ERROR: collocation output dir "
              f"not found: {out_dir}")
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

    # ---- Map plots (all observations) ----
    plot_tracks_by_source(
        cfg, ds, out_dir, boundaries, dpi)
    plot_tracks_by_time(
        cfg, ds, out_dir, boundaries, dpi)

    # ---- Apply plot-time filters ----
    mask = _apply_filters(ds, cfg)
    if not mask.any():
        print("  [plot_altimetry] no observations"
              " pass the filters. "
              "Scatter plots skipped.")
        ds.close()
        return

    n = int(mask.sum())
    print(f"  [plot_altimetry] {n} observations"
          f" pass filters -> scatter plots")

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
    print(f"  Altimetry plots complete.")
    print(f"  All figures in {out_dir}")
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
