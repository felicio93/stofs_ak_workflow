"""
models/schism/postprocess/argo_plots.py
========================================
Phase 5 step "plot_argo" (interactive; runs in the swf_plot env).

Produces four diagnostic plots from the collocated Argo float NetCDF files
written by the ``collocate_argo`` step:

  Plot 1 — Location map (argo_location_map.jpg)
      Scatter of every Argo profile position over the run period, coloured by
      date (turbo colormap). Mesh boundaries (open=blue, land=red,
      island=green) are overlaid from fix/hgrid.gr3.

  Plot 2 — Skill histograms (argo_skill_histograms_{var}.jpg)
      1 × 3 panel: per-profile R², Mean Bias, and RMSE distributions as
      histograms with 10th / median / 90th percentile markers. One figure per
      variable (temperature, salinity).

  Plot 3 — Profile matrix (argo_profiles_{var}.jpg)
      2 × 2 panel: Argo observed profiles (a), SCHISM model profiles (b),
      depth-resolved Mean Bias ± 1σ (c), and RMSE (d). One figure per variable.

  Plot 4 — Spatial skill map (argo_skill_map_{var}.jpg)
      Two-panel scatter map: left = per-profile RMSE, right = per-profile R².
      Profiles with no model overlap shown as gray dots. One figure per variable.

All axis limits, colormaps and colour ranges are fully configurable via
postprocess.yaml — see the Config keys section below and the template file.

Inputs
------
  P{ID}/P{ID}_collocate_argo/collocated_{var}_clean.nc  (preferred)
  P{ID}/P{ID}_collocate_argo/collocated_{var}.nc        (fallback)
  fix/hgrid.gr3                                         (for mesh boundaries)

Outputs
-------
  P{ID}/P{ID}_collocate_argo/argo_location_map.jpg
  P{ID}/P{ID}_collocate_argo/argo_skill_histograms_{temperature|salinity}.jpg
  P{ID}/P{ID}_collocate_argo/argo_profiles_{temperature|salinity}.jpg
  P{ID}/P{ID}_collocate_argo/argo_skill_map_{temperature|salinity}.jpg
  P{ID}/P{ID}_collocate_argo/plot_argo.done

Config keys (postprocess.yaml) — all optional, all have sensible defaults
--------------------------------------------------------------------------
General
  collocate_argo_vars                    [temperature, salinity]
  collocate_argo_plot_max_depth          300  (m; depth filter for all plots)
  collocate_argo_plot_max_dist_km        null (secondary distance filter)
  collocate_argo_plot_dpi                150

Plot 1 — Location map
  collocate_argo_plot_location_cmap      turbo
  collocate_argo_plot_location_s         14   (scatter marker size)

Plot 2 — Skill histograms
  collocate_argo_plot_hist_bins          40
  collocate_argo_plot_r2_xlim            [0, 1]
  collocate_argo_plot_temp_bias_xlim     null  (null = auto 99th-pct of data)
  collocate_argo_plot_temp_rmse_xlim     null
  collocate_argo_plot_psal_bias_xlim     null
  collocate_argo_plot_psal_rmse_xlim     null
  collocate_argo_plot_temp_hist_color    royalblue
  collocate_argo_plot_psal_hist_color    teal

Plot 3 — Profile matrix
  collocate_argo_plot_temp_xlim          null  (null = auto 1st–99th pct of obs)
  collocate_argo_plot_psal_xlim          null
  collocate_argo_plot_temp_bias_xlim     (shared with histogram bias xlim above)
  collocate_argo_plot_temp_rmse_xlim     (shared)
  collocate_argo_plot_psal_bias_xlim     (shared)
  collocate_argo_plot_psal_rmse_xlim     (shared)
  collocate_argo_plot_temp_color         royalblue
  collocate_argo_plot_psal_color         teal
  collocate_argo_plot_profile_alpha      0.15
  collocate_argo_plot_profile_lw         0.8

Plot 4 — Spatial skill map
  collocate_argo_plot_rmse_cmap          plasma
  collocate_argo_plot_r2_cmap            RdYlGn
  collocate_argo_plot_temp_rmse_vmax     null  (null = 99th pct of data)
  collocate_argo_plot_psal_rmse_vmax     null
  collocate_argo_plot_skill_s            20    (scatter marker size)

Variable names in the collocated NetCDF (from OCSTrack make_collocated_nc_3d)
------------------------------------------------------------------------------
  time              (time,)             datetime64 coordinate
  lat               (time,)             degrees_north
  lon               (time,)             degrees_east — 0..360 frame
  depth             (time, n_levels)    NEGATIVE metres (abs for display)
  argo_temp         (time, n_levels)    °C
  argo_psal         (time, n_levels)    PSU
  model_temperature (time, n_levels)    °C
  model_salinity    (time, n_levels)    PSU
  dist_deltas       (time, nearest_nodes) metres
  time_deltas       (time,)             seconds
"""

import argparse
import warnings
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
from matplotlib.collections import LineCollection
import scipy.interpolate as sci_interp

from workflow.core.config import load_config, model_dir
from workflow.core.plot_style import (
    read_mesh_boundaries, _draw_boundaries,
    _aspect_figsize,
    TITLE_FS, LABEL_FS, TICK_FS, CBAR_FS,
    PADDING_LON, PADDING_LAT,
)

warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---------------------------------------------------------------------------
# Variable metadata — hardcoded fallback defaults; all overridden by cfg
# ---------------------------------------------------------------------------
VAR_META = {
    "temperature": {
        "obs_var":   "argo_temp",
        "mod_var":   "model_temperature",
        "label":     "Temperature",
        "unit":      "°C",
        "mod_color_key": "collocate_argo_plot_temp_color",
        "mod_color_default": "royalblue",
        "hist_color_key": "collocate_argo_plot_temp_hist_color",
        "hist_color_default": "royalblue",
        "xlim_key":       "collocate_argo_plot_temp_xlim",
        "bias_xlim_key":  "collocate_argo_plot_temp_bias_xlim",
        "rmse_xlim_key":  "collocate_argo_plot_temp_rmse_xlim",
        "rmse_vmax_key":  "collocate_argo_plot_temp_rmse_vmax",
    },
    "salinity": {
        "obs_var":   "argo_psal",
        "mod_var":   "model_salinity",
        "label":     "Salinity",
        "unit":      "PSU",
        "mod_color_key": "collocate_argo_plot_psal_color",
        "mod_color_default": "teal",
        "hist_color_key": "collocate_argo_plot_psal_hist_color",
        "hist_color_default": "teal",
        "xlim_key":       "collocate_argo_plot_psal_xlim",
        "bias_xlim_key":  "collocate_argo_plot_psal_bias_xlim",
        "rmse_xlim_key":  "collocate_argo_plot_psal_rmse_xlim",
        "rmse_vmax_key":  "collocate_argo_plot_psal_rmse_vmax",
    },
}


# ---------------------------------------------------------------------------
# Helpers for reading limits from cfg
# ---------------------------------------------------------------------------

def _xlim(cfg, key, default=None):
    """Return (lo, hi) from cfg[key] (a 2-element list), or default."""
    v = cfg.get(key)
    if v is not None:
        return float(v[0]), float(v[1])
    return default


def _vmax(cfg, key, data_arr, pct=99, round_to=0.5):
    """Return vmax from cfg[key] or the pct-th percentile of data_arr."""
    v = cfg.get(key)
    if v is not None:
        return float(v)
    finite = data_arr[~np.isnan(data_arr)]
    if finite.size == 0:
        return 1.0
    raw = float(np.percentile(finite, pct))
    return max(round_to, np.ceil(raw / round_to) * round_to)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _out_dir(cfg) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_collocate_argo"


def _load_boundaries(cfg):
    mdir = model_dir(cfg)
    for hp in (mdir / "fix" / "hgrid.gr3", mdir / "fix" / "hgrid.ll"):
        if hp.exists():
            try:
                return read_mesh_boundaries(hp)
            except Exception as exc:
                print(f"  [plot_argo] could not load boundaries: {exc}")
    print("  [plot_argo] hgrid.gr3/.ll not found — plotting without boundaries.")
    return None


def _load_collocated(out_dir: Path, var: str):
    import xarray as xr
    clean_nc = out_dir / f"collocated_{var}_clean.nc"
    full_nc  = out_dir / f"collocated_{var}.nc"
    if clean_nc.exists() and clean_nc.stat().st_size > 0:
        nc = clean_nc
    elif full_nc.exists() and full_nc.stat().st_size > 0:
        print(f"  [plot_argo] {clean_nc.name} not found — "
              f"falling back to {full_nc.name}.")
        nc = full_nc
    else:
        print(f"  [plot_argo] neither {clean_nc.name} nor {full_nc.name} found.")
        return None, None
    ds = xr.open_dataset(str(nc), engine="netcdf4")
    print(f"  [plot_argo] loading {nc.name}  ({ds.sizes.get('time','?')} profiles)")
    return ds, nc


def _apply_dist_filter(ds, max_dist_km):
    if max_dist_km is None:
        return ds
    thresh_m = float(max_dist_km) * 1000.0
    nearest  = ds["dist_deltas"].min(dim="nearest_nodes")
    mask     = nearest.values < thresh_m
    if not mask.any():
        print(f"  [plot_argo] WARNING: dist filter {max_dist_km} km removes all profiles.")
        return ds
    return ds.isel(time=mask)


def _depth_pos(depth_arr: np.ndarray) -> np.ndarray:
    return np.abs(depth_arr)


def _panel_letter(ax, letter: str, fontsize: int = 13):
    ax.text(0.04, 0.97, f"{letter})",
            transform=ax.transAxes, va="top", fontweight="bold",
            fontsize=fontsize,
            bbox=dict(facecolor="white", alpha=0.85, edgecolor="none", pad=2))


def _data_summary(obs_arr, mod_arr, dep_arr, max_depth, var_name):
    z = _depth_pos(dep_arr)
    obs_valid  = ~np.isnan(obs_arr) & ~np.isnan(z) & (z <= max_depth)
    mod_valid  = ~np.isnan(mod_arr) & ~np.isnan(z) & (z <= max_depth)
    both_valid = obs_valid & mod_valid
    n = obs_arr.shape[0]
    print(f"  [plot_argo] {var_name} coverage (depth ≤ {max_depth:.0f} m):")
    print(f"    Total profiles     : {n}")
    print(f"    With obs data      : {int((obs_valid.sum(1)>=1).sum())}")
    print(f"    With model data    : {int((mod_valid.sum(1)>=1).sum())}")
    print(f"    Both obs+model ≥2  : {int((both_valid.sum(1)>=2).sum())}")


# ---------------------------------------------------------------------------
# Per-profile metrics helper
# ---------------------------------------------------------------------------

def _compute_profile_metrics(obs_arr, mod_arr, depth_arr, max_depth,
                             min_levels: int = 2):
    """Per-profile R², Bias, RMSE where BOTH obs AND model valid, |depth|≤max_depth.
    Returns (r2_list, bias_list, rmse_list, valid_mask) where valid_mask[i]=True
    means profile i produced metrics. R² is NaN when fewer than 2 levels overlap.
    Set min_levels=1 to include profiles with only a single matching depth level
    (useful for skill maps where RMSE/Bias are still meaningful)."""
    z_all = _depth_pos(depth_arr)
    r2_list, bias_list, rmse_list = [], [], []
    valid_mask = np.zeros(obs_arr.shape[0], dtype=bool)
    for i in range(obs_arr.shape[0]):
        z = z_all[i]; obs = obs_arr[i]; mod = mod_arr[i]
        mask = ~np.isnan(obs) & ~np.isnan(mod) & ~np.isnan(z) & (z <= max_depth)
        if mask.sum() < min_levels:
            continue
        o = obs[mask]; m = mod[mask]
        bias = float(np.mean(m - o))
        rmse = float(np.sqrt(np.mean((m - o) ** 2)))
        r2   = float(np.corrcoef(o, m)[0, 1] ** 2) \
               if np.var(o) > 1e-8 and np.var(m) > 1e-8 else np.nan
        bias_list.append(bias); rmse_list.append(rmse); r2_list.append(r2)
        valid_mask[i] = True
    return r2_list, bias_list, rmse_list, valid_mask


# ---------------------------------------------------------------------------
# Plot 1 — Location map
# ---------------------------------------------------------------------------

def plot_argo_location(cfg, ds, out_dir: Path, boundaries, dpi: int):
    lons  = ds["lon"].values
    lats  = ds["lat"].values
    times = ds["time"].values

    time_nums = mdates.date2num(times.astype("datetime64[ms]").astype(object))
    vmin_t = mdates.date2num(np.datetime64(cfg["start_date"], "ms").astype(object))
    vmax_t = mdates.date2num(np.datetime64(cfg["end_date"],   "ms").astype(object))

    cmap = cfg.get("collocate_argo_plot_location_cmap", "turbo")
    s    = float(cfg.get("collocate_argo_plot_location_s", 14))

    if boundaries and "mesh_extent" in boundaries:
        ext = boundaries["mesh_extent"]
        lon_min = ext[0]-PADDING_LON; lon_max = ext[1]+PADDING_LON
        lat_min = ext[2]-PADDING_LAT; lat_max = ext[3]+PADDING_LAT
    else:
        lon_min = float(lons.min())-PADDING_LON; lon_max = float(lons.max())+PADDING_LON
        lat_min = float(lats.min())-PADDING_LAT; lat_max = float(lats.max())+PADDING_LAT

    fw, fh = _aspect_figsize(lon_min, lon_max, lat_min, lat_max)
    fig, ax = plt.subplots(figsize=(fw, fh+0.8), constrained_layout=True)
    _draw_boundaries(ax, boundaries)
    sc = ax.scatter(lons, lats, c=time_nums, cmap=cmap,
                    vmin=vmin_t, vmax=vmax_t,
                    s=s, edgecolor="k", linewidth=0.2, zorder=6)
    cbar = fig.colorbar(sc, ax=ax, orientation="horizontal",
                        pad=0.06, shrink=0.85, aspect=40)
    cbar.set_label("Profile Date", fontsize=CBAR_FS)
    cbar.ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    cbar.ax.tick_params(labelsize=TICK_FS, rotation=30)
    ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)
    ax.set_xlabel("Longitude (°E)", fontsize=LABEL_FS)
    ax.set_ylabel("Latitude (°N)", fontsize=LABEL_FS)
    ax.tick_params(labelsize=TICK_FS); ax.set_aspect("equal")
    ax.set_title(f"Argo Float Profile Locations  (n = {len(lons)})",
                 fontsize=TITLE_FS, fontweight="bold", pad=10)
    ax.grid(True, linestyle="--", alpha=0.3, zorder=0)
    out_path = out_dir / "argo_location_map.jpg"
    fig.savefig(str(out_path), dpi=dpi, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": 90})
    plt.close(fig)
    print(f"  [plot_argo] location map -> {out_path.name}")


# ---------------------------------------------------------------------------
# Plot 2 — Skill histograms
# ---------------------------------------------------------------------------

def plot_argo_histograms(cfg, ds, var: str, out_dir: Path, max_depth: float, dpi: int):
    meta    = VAR_META[var]
    obs_arr = ds[meta["obs_var"]].values
    mod_arr = ds[meta["mod_var"]].values
    dep_arr = ds["depth"].values
    _data_summary(obs_arr, mod_arr, dep_arr, max_depth, var)

    r2_list, bias_list, rmse_list, _ = _compute_profile_metrics(
        obs_arr, mod_arr, dep_arr, max_depth)
    r2_arr   = np.array([v for v in r2_list   if not np.isnan(v)])
    bias_arr = np.array([v for v in bias_list if not np.isnan(v)])
    rmse_arr = np.array([v for v in rmse_list if not np.isnan(v)])

    if len(bias_arr) == 0:
        print(f"  [plot_argo] histograms ({var}): no overlapping levels within "
              f"{max_depth:.0f} m.")
        return

    print(f"  [plot_argo] histograms ({var}): {len(bias_arr)} profiles.")

    # ---- resolve limits from cfg ----
    r2_xlim   = _xlim(cfg, "collocate_argo_plot_r2_xlim",   (0, 1))
    bias_xlim = _xlim(cfg, meta["bias_xlim_key"])
    rmse_xlim = _xlim(cfg, meta["rmse_xlim_key"])

    hist_bins = int(cfg.get("collocate_argo_plot_hist_bins", 40))
    col_bias  = cfg.get(meta["hist_color_key"], meta["hist_color_default"])

    # auto-compute missing limits from data
    if bias_xlim is None:
        p = float(np.ceil(np.percentile(np.abs(bias_arr), 99) / 0.5) * 0.5)
        bias_xlim = (-max(0.1, p), max(0.1, p))
    if rmse_xlim is None:
        p = float(np.ceil(np.percentile(rmse_arr, 99) / 0.5) * 0.5)
        rmse_xlim = (0, max(0.1, p))

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    ax_r2, ax_bias, ax_rmse = axes

    panels = [
        (ax_r2,   r2_arr,   "R² (Pearson)",
         "royalblue", r2_xlim),
        (ax_bias, bias_arr, f"Mean Bias ({meta['unit']})",
         "crimson",   bias_xlim),
        (ax_rmse, rmse_arr, f"RMSE ({meta['unit']})",
         col_bias,    rmse_xlim),
    ]
    # Override per-variable histogram colours
    panels[0] = (ax_r2, r2_arr, "R² (Pearson)", "royalblue", r2_xlim)
    panels[1] = (ax_bias, bias_arr, f"Mean Bias ({meta['unit']})", "crimson", bias_xlim)
    panels[2] = (ax_rmse, rmse_arr, f"RMSE ({meta['unit']})", "forestgreen", rmse_xlim)

    for (ax, data, xlabel, color, xlim), letter in zip(panels, "abc"):
        ax.hist(data, bins=hist_bins, range=xlim,
                color=color, alpha=0.7, edgecolor="black", linewidth=0.8)
        median_val = float(np.median(data))
        p10 = float(np.percentile(data, 10))
        p90 = float(np.percentile(data, 90))
        ax.axvline(p10, color="#333333", linestyle=":", linewidth=1.5,
                   label=f"10th %: {p10:.2f}")
        ax.axvline(median_val, color="k", linestyle="--", linewidth=2.0,
                   label=f"Median: {median_val:.2f}")
        ax.axvline(p90, color="#333333", linestyle=":", linewidth=1.5,
                   label=f"90th %: {p90:.2f}")
        ax.set_xlim(xlim)
        ax.set_xlabel(xlabel, fontsize=LABEL_FS, fontweight="bold")
        ax.set_ylabel("Number of Profiles", fontsize=LABEL_FS)
        ax.tick_params(labelsize=TICK_FS)
        ax.grid(True, linestyle="--", alpha=0.5)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16),
                  ncol=3, fontsize=TICK_FS, frameon=False)
        _panel_letter(ax, letter, fontsize=TITLE_FS)

    start = cfg["start_date"]; end = cfg["end_date"]
    fig.suptitle(
        f"{meta['label']} Profile Skill  |  Depth ≤ {max_depth:.0f} m  |  "
        f"n = {len(bias_arr)} profiles  |  {start} to {end}",
        fontsize=TITLE_FS, fontweight="bold")
    out_path = out_dir / f"argo_skill_histograms_{var}.jpg"
    fig.savefig(str(out_path), dpi=dpi, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": 90})
    plt.close(fig)
    print(f"  [plot_argo] histograms ({var}) -> {out_path.name}")


# ---------------------------------------------------------------------------
# Plot 3 — Profile matrix
# ---------------------------------------------------------------------------

def _interp_profile(z_pos, values, common_depth):
    valid = ~np.isnan(z_pos) & ~np.isnan(values)
    if valid.sum() < 2:
        return np.full_like(common_depth, np.nan)
    sort_idx = np.argsort(z_pos[valid])
    zv = z_pos[valid][sort_idx]; vv = values[valid][sort_idx]
    return sci_interp.interp1d(zv, vv, bounds_error=False,
                               fill_value=np.nan)(common_depth)


def plot_argo_profiles(cfg, ds, var: str, out_dir: Path, max_depth: float, dpi: int):
    meta      = VAR_META[var]
    obs_arr   = ds[meta["obs_var"]].values
    mod_arr   = ds[meta["mod_var"]].values
    dep_arr   = _depth_pos(ds["depth"].values)
    mod_color = cfg.get(meta["mod_color_key"], meta["mod_color_default"])
    alpha     = float(cfg.get("collocate_argo_plot_profile_alpha", 0.15))
    lw        = float(cfg.get("collocate_argo_plot_profile_lw", 0.8))

    _data_summary(obs_arr, mod_arr, dep_arr, max_depth, var)

    obs_idx, mod_idx = [], []
    for i in range(obs_arr.shape[0]):
        z = dep_arr[i]
        if (~np.isnan(obs_arr[i]) & ~np.isnan(z) & (z<=max_depth)).sum() >= 2:
            obs_idx.append(i)
        if (~np.isnan(mod_arr[i]) & ~np.isnan(z) & (z<=max_depth)).sum() >= 1:
            mod_idx.append(i)

    if not obs_idx:
        print(f"  [plot_argo] profiles ({var}): no obs within {max_depth:.0f} m.")
        return
    print(f"  [plot_argo] profiles ({var}): {len(obs_idx)} obs, "
          f"{len(mod_idx)} model profiles within {max_depth:.0f} m.")

    step = 5.0
    common_depth = np.arange(0, max_depth + step, step)

    obs_interp_all = [_interp_profile(dep_arr[i], obs_arr[i], common_depth)
                      for i in range(obs_arr.shape[0])]
    mod_interp_all = [_interp_profile(dep_arr[i], mod_arr[i], common_depth)
                      for i in range(obs_arr.shape[0])]
    obs_mat = np.array(obs_interp_all)
    mod_mat = np.array(mod_interp_all)
    err_mat = mod_mat - obs_mat
    mean_err  = np.nanmean(err_mat, axis=0)
    std_err   = np.nanstd(err_mat,  axis=0)
    rmse_prof = np.sqrt(np.nanmean(err_mat**2, axis=0))
    n_overlap = np.sum(~np.isnan(err_mat), axis=0)

    # ---- x limits ----
    obs_flat = obs_arr[obs_idx][~np.isnan(obs_arr[obs_idx]) & (dep_arr[obs_idx]<=max_depth)]
    if _xlim(cfg, meta["xlim_key"]) is not None:
        xlo, xhi = _xlim(cfg, meta["xlim_key"])
    elif obs_flat.size > 0:
        xlo = float(np.percentile(obs_flat, 1))
        xhi = float(np.percentile(obs_flat, 99))
    else:
        xlo, xhi = None, None

    has_data = n_overlap > 0
    all_errs = err_mat[~np.isnan(err_mat)]

    bias_xlim = _xlim(cfg, meta["bias_xlim_key"])
    if bias_xlim is None:
        if all_errs.size > 0:
            p = float(np.ceil(np.percentile(np.abs(all_errs), 99)/0.5)*0.5)
            bias_xlim = (-max(0.1, p), max(0.1, p))
        else:
            bias_xlim = (-1, 1)

    rmse_xlim = _xlim(cfg, meta["rmse_xlim_key"])
    if rmse_xlim is None:
        rmse_valid = rmse_prof[has_data & ~np.isnan(rmse_prof)] if has_data.any() else np.array([])
        rmse_max = float(np.ceil(np.percentile(rmse_valid, 99)/0.5)*0.5) \
            if rmse_valid.size > 0 else abs(bias_xlim[1])
        rmse_xlim = (0, max(0.1, rmse_max))

    # ---- Figure ----
    fig = plt.figure(figsize=(12, 10), constrained_layout=True)
    gs  = gridspec.GridSpec(2, 2, figure=fig, wspace=0.08, hspace=0.08)
    ax_obs  = fig.add_subplot(gs[0, 0])
    ax_mod  = fig.add_subplot(gs[0, 1], sharey=ax_obs)
    ax_bias = fig.add_subplot(gs[1, 0], sharey=ax_obs)
    ax_rmse = fig.add_subplot(gs[1, 1], sharey=ax_obs)

    # a) obs
    obs_lines = []
    for i in obs_idx:
        z = dep_arr[i]; o = obs_arr[i]
        m = ~np.isnan(o) & ~np.isnan(z) & (z<=max_depth)
        if m.sum() >= 2:
            obs_lines.append(np.column_stack((o[m], z[m])))
    if obs_lines:
        ax_obs.add_collection(LineCollection(obs_lines, colors="gray",
                                             alpha=alpha, linewidths=lw))
    ax_obs.autoscale_view()
    ax_obs.set_title("Argo Observations", fontsize=TITLE_FS, fontweight="bold", pad=4)
    ax_obs.set_xlabel(f"{meta['label']} ({meta['unit']})", fontsize=LABEL_FS)
    ax_obs.set_ylabel("Depth (m)", fontsize=LABEL_FS)
    ax_obs.tick_params(labelsize=TICK_FS); ax_obs.grid(True, ls="--", alpha=0.4)
    _panel_letter(ax_obs, "a")
    if xlo is not None: ax_obs.set_xlim(xlo, xhi)

    # b) model
    mod_lines = []
    for i in mod_idx:
        z = dep_arr[i]; m = mod_arr[i]
        mk = ~np.isnan(m) & ~np.isnan(z) & (z<=max_depth)
        if mk.sum() >= 1:
            mod_lines.append(np.column_stack((m[mk], z[mk])))
    if mod_lines:
        ax_mod.add_collection(LineCollection(mod_lines, colors=mod_color,
                                             alpha=alpha, linewidths=lw))
    ax_mod.autoscale_view()
    ax_mod.set_title("SCHISM Model", fontsize=TITLE_FS, fontweight="bold",
                     pad=4, color=mod_color)
    ax_mod.set_xlabel(f"{meta['label']} ({meta['unit']})", fontsize=LABEL_FS)
    ax_mod.tick_params(axis="y", labelleft=False, labelsize=TICK_FS)
    ax_mod.tick_params(axis="x", labelsize=TICK_FS)
    ax_mod.grid(True, ls="--", alpha=0.4); _panel_letter(ax_mod, "b")
    if xlo is not None: ax_mod.set_xlim(xlo, xhi)

    # c) bias
    if has_data.any():
        ax_bias.plot(mean_err[has_data], common_depth[has_data], color=mod_color, lw=2)
        ax_bias.fill_betweenx(common_depth[has_data],
                              (mean_err-std_err)[has_data],
                              (mean_err+std_err)[has_data],
                              alpha=0.20, color=mod_color)
    ax_bias.axvline(0, color="k", lw=1.2)
    ax_bias.set_title("Mean Bias (Model − Obs)", fontsize=TITLE_FS,
                      fontweight="bold", pad=4)
    ax_bias.set_xlabel(f"Bias ({meta['unit']})", fontsize=LABEL_FS)
    ax_bias.set_ylabel("Depth (m)", fontsize=LABEL_FS)
    ax_bias.tick_params(labelsize=TICK_FS); ax_bias.grid(True, ls="--", alpha=0.4)
    ax_bias.set_xlim(bias_xlim[0], bias_xlim[1])
    _panel_letter(ax_bias, "c")

    # d) RMSE
    if has_data.any():
        ax_rmse.plot(rmse_prof[has_data], common_depth[has_data], color=mod_color, lw=2)
    ax_rmse.set_title("RMSE", fontsize=TITLE_FS, fontweight="bold", pad=4)
    ax_rmse.set_xlabel(f"RMSE ({meta['unit']})", fontsize=LABEL_FS)
    ax_rmse.tick_params(axis="y", labelleft=False, labelsize=TICK_FS)
    ax_rmse.tick_params(axis="x", labelsize=TICK_FS)
    ax_rmse.grid(True, ls="--", alpha=0.4)
    ax_rmse.set_xlim(rmse_xlim[0], rmse_xlim[1])
    _panel_letter(ax_rmse, "d")

    ax_obs.set_ylim(max_depth, 0)

    start = cfg["start_date"]; end = cfg["end_date"]
    fig.suptitle(f"SCHISM vs Argo — {meta['label']}  |  "
                 f"Depth ≤ {max_depth:.0f} m  |  {start} to {end}",
                 fontsize=TITLE_FS, fontweight="bold")
    out_path = out_dir / f"argo_profiles_{var}.jpg"
    fig.savefig(str(out_path), dpi=dpi, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": 90})
    plt.close(fig)
    print(f"  [plot_argo] profiles ({var}) -> {out_path.name}")


# ---------------------------------------------------------------------------
# Plot 4 — Spatial skill map (RMSE + R²)
# ---------------------------------------------------------------------------

def plot_argo_skill_map(cfg, ds, var: str, out_dir: Path,
                        max_depth: float, skill_max_depth: float,
                        dpi: int, boundaries):
    """Two-panel scatter map: per-profile RMSE (left) and R² (right).
    Profiles with no model overlap are shown as gray dots.
    """
    meta    = VAR_META[var]
    obs_arr = ds[meta["obs_var"]].values
    mod_arr = ds[meta["mod_var"]].values
    dep_arr = ds["depth"].values
    lons    = ds["lon"].values
    lats    = ds["lat"].values

    # Use skill_max_depth (potentially larger than display max_depth) so that
    # profiles with deep-only measurements still get skill metrics.
    # min_levels=1: RMSE/Bias are meaningful with 1 overlapping level;
    # R² will be NaN for those (requires variance -> needs >= 2 points).
    r2_list, bias_list, rmse_list, valid_mask = _compute_profile_metrics(
        obs_arr, mod_arr, dep_arr, skill_max_depth, min_levels=1)

    rmse_arr   = np.full(len(lons), np.nan)
    r2_arr     = np.full(len(lons), np.nan)
    valid_idx  = np.where(valid_mask)[0]
    for k, i in enumerate(valid_idx):
        rmse_arr[i] = rmse_list[k]
        r2_arr[i]   = r2_list[k]  if not np.isnan(r2_list[k]) else np.nan

    n_valid   = int(valid_mask.sum())
    n_invalid = len(lons) - n_valid
    print(f"  [plot_argo] skill map ({var}): {n_valid} profiles with skill, "
          f"{n_invalid} without model overlap.")

    if n_valid == 0:
        print(f"  [plot_argo] skill map ({var}): no profiles with model overlap, skipping.")
        return

    # ---- limits / colormaps from cfg ----
    rmse_cmap = cfg.get("collocate_argo_plot_rmse_cmap", "plasma")
    r2_cmap   = cfg.get("collocate_argo_plot_r2_cmap",   "RdYlGn")
    s         = float(cfg.get("collocate_argo_plot_skill_s", 20))

    rmse_vals_valid = rmse_arr[valid_mask]
    rmse_vmax = _vmax(cfg, meta["rmse_vmax_key"], rmse_vals_valid, pct=99)
    rmse_vmin = 0.0

    r2_xlim_cfg = _xlim(cfg, "collocate_argo_plot_r2_xlim", (0, 1))
    r2_vmin, r2_vmax = r2_xlim_cfg

    # ---- domain extent ----
    if boundaries and "mesh_extent" in boundaries:
        ext = boundaries["mesh_extent"]
        lon_min = ext[0]-PADDING_LON; lon_max = ext[1]+PADDING_LON
        lat_min = ext[2]-PADDING_LAT; lat_max = ext[3]+PADDING_LAT
    else:
        lon_min = float(lons.min())-PADDING_LON; lon_max = float(lons.max())+PADDING_LON
        lat_min = float(lats.min())-PADDING_LAT; lat_max = float(lats.max())+PADDING_LAT

    # Size: two panels side by side
    fw_one, fh = _aspect_figsize(lon_min, lon_max, lat_min, lat_max)
    fig, (ax_rmse, ax_r2) = plt.subplots(
        1, 2, figsize=(fw_one * 2 + 0.5, fh + 0.8),
        constrained_layout=True)

    # ---- Draw both panels ----
    for ax, values, title, cmap, vmin, vmax, cbar_label, letter in [
        (ax_rmse, rmse_arr, f"RMSE ({meta['unit']})",
         rmse_cmap, rmse_vmin, rmse_vmax, f"RMSE ({meta['unit']})", "a"),
        (ax_r2,   r2_arr,   "R²",
         r2_cmap,  r2_vmin,  r2_vmax,   "R²",                      "b"),
    ]:
        _draw_boundaries(ax, boundaries)

        # Gray dots for profiles without model overlap
        if n_invalid > 0:
            ax.scatter(lons[~valid_mask], lats[~valid_mask],
                       c="lightgray", s=s*0.6, edgecolor="none",
                       zorder=4, label="No model overlap")

        # Coloured dots for profiles with skill
        sc = ax.scatter(lons[valid_mask], lats[valid_mask],
                        c=values[valid_mask], cmap=cmap,
                        vmin=vmin, vmax=vmax,
                        s=s, edgecolor="k", linewidth=0.15, zorder=5)

        cbar = fig.colorbar(sc, ax=ax, orientation="vertical",
                            pad=0.02, fraction=0.030, shrink=0.60)
        cbar.set_label(cbar_label, fontsize=CBAR_FS)
        cbar.ax.tick_params(labelsize=TICK_FS)

        ax.set_xlim(lon_min, lon_max); ax.set_ylim(lat_min, lat_max)
        ax.set_xlabel("Longitude (°E)", fontsize=LABEL_FS)
        ax.set_ylabel("Latitude (°N)", fontsize=LABEL_FS)
        ax.tick_params(labelsize=TICK_FS); ax.set_aspect("equal")
        ax.set_title(title, fontsize=TITLE_FS, fontweight="bold", pad=6)
        ax.grid(True, linestyle="--", alpha=0.3, zorder=0)
        _panel_letter(ax, letter)

        if n_invalid > 0:
            ax.legend(loc="lower right", fontsize=TICK_FS, framealpha=0.8,
                      markerscale=1.5)

    start = cfg["start_date"]; end = cfg["end_date"]
    skill_depth_str = f"{skill_max_depth:.0f} m" \
        if np.isfinite(skill_max_depth) else "full depth"
    fig.suptitle(
        f"SCHISM vs Argo — {meta['label']} Skill  |  "
        f"Skill depth ≤ {skill_depth_str}  |  {start} to {end}",
        fontsize=TITLE_FS, fontweight="bold", y=0.98)

    out_path = out_dir / f"argo_skill_map_{var}.jpg"
    fig.savefig(str(out_path), dpi=dpi, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": 90})
    plt.close(fig)
    print(f"  [plot_argo] skill map ({var}) -> {out_path.name}")


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def run_plot_argo(cfg: dict, config_dir=None):
    out_dir = _out_dir(cfg)
    if not out_dir.is_dir():
        print(f"ERROR: collocation output dir not found: {out_dir}")
        return

    max_depth   = float(cfg.get("collocate_argo_plot_max_depth",  300))
    # Depth used for COMPUTING skill metrics on the skill map. Defaults to
    # the full water column (inf) so all overlapping levels contribute,
    # regardless of the display max_depth used for the profile matrix.
    _skill_d = cfg.get("collocate_argo_plot_skill_max_depth")
    skill_max_depth = float(_skill_d) if _skill_d is not None else np.inf
    max_dist_km = cfg.get("collocate_argo_plot_max_dist_km")
    dpi         = int(cfg.get("collocate_argo_plot_dpi", 150))
    variables   = cfg.get("collocate_argo_vars") or ["temperature", "salinity"]
    variables   = [v for v in variables if v in VAR_META]

    if not variables:
        print("ERROR: collocate_argo_vars has no recognised variables.")
        return

    print(f"\n{'='*60}")
    print(f"  Argo diagnostic plots")
    print(f"  Variables: {variables}  |  Max depth: {max_depth} m  |  DPI: {dpi}")
    if max_dist_km:
        print(f"  Distance filter: ≤ {max_dist_km} km from nearest mesh node")
    print(f"  Output: {out_dir}")
    print(f"{'='*60}\n")

    boundaries = _load_boundaries(cfg)

    datasets = {}
    for var in variables:
        ds, _ = _load_collocated(out_dir, var)
        if ds is None:
            continue
        ds = _apply_dist_filter(ds, max_dist_km)
        if ds.sizes.get("time", 0) == 0:
            print(f"  [plot_argo] {var}: no profiles after filter, skipping.")
            continue
        datasets[var] = ds

    if not datasets:
        print("  [plot_argo] no collocated data available. Run collocate_argo first.")
        return

    # Plot 1: location map (once, first available variable)
    plot_argo_location(cfg, datasets[next(iter(datasets))], out_dir, boundaries, dpi)

    # Plots 2, 3, 4: per variable
    for var, ds in datasets.items():
        plot_argo_histograms(cfg, ds, var, out_dir, max_depth, dpi)
        plot_argo_profiles(cfg, ds, var, out_dir, max_depth, dpi)
        plot_argo_skill_map(cfg, ds, var, out_dir, max_depth,
                            skill_max_depth, dpi, boundaries)
        ds.close()

    (out_dir / "plot_argo.done").touch()
    print(f"\n{'='*60}")
    print(f"  Argo plots complete. All figures in {out_dir}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Argo diagnostic plots")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    run_plot_argo(load_config(Path(args.config)), args.config)
