"""
models/schism/postprocess/argo_plots.py
========================================
Phase 5 step "plot_argo" (interactive; runs in the swf_plot env).

Produces three diagnostic plots from the collocated Argo float NetCDF files
written by the ``collocate_argo`` step:

  Plot 1 — Location map (argo_location_map.jpg)
      Scatter of every Argo profile position over the run period, coloured by
      date (turbo colormap). Mesh boundaries (open=blue, land=red,
      island=green) are overlaid from fix/hgrid.gr3.

  Plot 2 — Skill histograms (argo_skill_histograms_{var}.jpg)
      1 × 3 panel: per-profile R², Mean Bias, and RMSE distributions as
      histograms with 10th / median / 90th percentile markers. One figure per
      variable (temperature, salinity).

      NOTE: model values are sparse (SCHISM sigma layers cover only the shelf
      depth at each node, while Argo floats often reach the deep ocean).
      Profiles that have fewer than 2 overlapping levels between Argo and SCHISM
      are excluded from skill metrics. A per-profile count of overlapping levels
      is printed. If coverage is extremely sparse, skill metrics may use very
      few profiles — this is a scientific limitation of comparing a shelf model
      with open-ocean Argo floats.

  Plot 3 — Profile matrix (argo_profiles_{var}.jpg)
      2 × 2 panel: Argo observed profiles (a), SCHISM model profiles (b),
      depth-resolved Mean Bias ± 1σ (c), and RMSE (d). Individual profiles
      drawn as semi-transparent LineCollections. Obs and model panels are
      plotted independently (obs profiles always shown; model lines only where
      SCHISM has valid values). Bias/RMSE panels use pooled per-level statistics
      across all profiles.

Inputs
------
  P{ID}/P{ID}_collocate_argo/collocated_{var}_clean.nc  (preferred; distance-filtered)
  P{ID}/P{ID}_collocate_argo/collocated_{var}.nc        (fallback if clean file absent)
  fix/hgrid.gr3                                         (for mesh boundaries)

Outputs
-------
  P{ID}/P{ID}_collocate_argo/argo_location_map.jpg
  P{ID}/P{ID}_collocate_argo/argo_skill_histograms_temperature.jpg
  P{ID}/P{ID}_collocate_argo/argo_skill_histograms_salinity.jpg
  P{ID}/P{ID}_collocate_argo/argo_profiles_temperature.jpg
  P{ID}/P{ID}_collocate_argo/argo_profiles_salinity.jpg
  P{ID}/P{ID}_collocate_argo/plot_argo.done

Config keys (postprocess.yaml)
-------------------------------
  collocate_argo_vars             list of variables to plot (default [temperature, salinity])
  collocate_argo_plot_max_depth   depth filter in metres (default 300; set to the
                                  approximate shelf depth of your domain — Argo
                                  floats reach >5000 m but SCHISM sigma layers
                                  only cover the model's actual bathymetry)
  collocate_argo_plot_max_dist_km optional: keep only profiles ≤ X km from
                                  nearest mesh node (null = no filter; the clean
                                  file is already pre-filtered at
                                  collocate_argo_dist_threshold_km)
  collocate_argo_plot_dpi         DPI for all saved figures (default 150)

Variable names in the collocated NetCDF (from OCSTrack make_collocated_nc_3d)
------------------------------------------------------------------------------
  time              (time,)             datetime64 coordinate
  lat               (time,)             degrees_north
  lon               (time,)             degrees_east — stored in 0..360 frame
  depth             (time, n_levels)    NEGATIVE metres (z-convention; abs for display)
  argo_temp         (time, n_levels)    °C
  argo_psal         (time, n_levels)    PSU
  model_temperature (time, n_levels)    °C  — NaN outside SCHISM's sigma layer range
  model_salinity    (time, n_levels)    PSU — NaN outside SCHISM's sigma layer range
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
# Variable metadata
# ---------------------------------------------------------------------------
VAR_META = {
    "temperature": {
        "obs_var":   "argo_temp",
        "mod_var":   "model_temperature",
        "label":     "Temperature",
        "unit":      "°C",
        "bias_xlim": (-5, 5),
        "rmse_xlim": (0, 5),
        "r2_xlim":   (0, 1),
        "hist_bias_range": (-5, 5),
        "hist_rmse_range": (0, 5),
        "mod_color": "royalblue",
    },
    "salinity": {
        "obs_var":   "argo_psal",
        "mod_var":   "model_salinity",
        "label":     "Salinity",
        "unit":      "PSU",
        "bias_xlim": (-2, 2),
        "rmse_xlim": (0, 2),
        "r2_xlim":   (0, 1),
        "hist_bias_range": (-2, 2),
        "hist_rmse_range": (0, 2),
        "mod_color": "teal",
    },
}


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
                print(f"  [plot_argo] could not load boundaries from {hp.name}: {exc}")
    print("  [plot_argo] hgrid.gr3/.ll not found in fix/ — plotting without boundaries.")
    return None


def _load_collocated(out_dir: Path, var: str):
    """Prefer the distance-filtered clean file; fall back to the full file."""
    import xarray as xr
    clean_nc = out_dir / f"collocated_{var}_clean.nc"
    full_nc  = out_dir / f"collocated_{var}.nc"

    if clean_nc.exists() and clean_nc.stat().st_size > 0:
        nc = clean_nc
    elif full_nc.exists() and full_nc.stat().st_size > 0:
        print(f"  [plot_argo] {clean_nc.name} not found — "
              f"falling back to {full_nc.name} (no distance filter applied).")
        nc = full_nc
    else:
        print(f"  [plot_argo] neither {clean_nc.name} nor {full_nc.name} found "
              f"— run collocate_argo first.")
        return None, None

    ds = xr.open_dataset(str(nc), engine="netcdf4")
    n = ds.sizes.get("time", "?")
    print(f"  [plot_argo] loading {nc.name}  ({n} profiles)")
    return ds, nc


def _apply_dist_filter(ds, max_dist_km):
    if max_dist_km is None:
        return ds
    thresh_m = float(max_dist_km) * 1000.0
    nearest = ds["dist_deltas"].min(dim="nearest_nodes")
    mask = nearest.values < thresh_m
    if not mask.any():
        print(f"  [plot_argo] WARNING: dist filter {max_dist_km} km removes all "
              f"profiles. Ignoring filter.")
        return ds
    return ds.isel(time=mask)


def _depth_pos(depth_arr: np.ndarray) -> np.ndarray:
    """Return depth as positive-downward values (flip negative z convention)."""
    return np.abs(depth_arr)


def _panel_letter(ax, letter: str, fontsize: int = 13):
    ax.text(0.04, 0.97, f"{letter})",
            transform=ax.transAxes, va="top", fontweight="bold",
            fontsize=fontsize,
            bbox=dict(facecolor="white", alpha=0.85, edgecolor="none", pad=2))


def _data_summary(obs_arr, mod_arr, dep_arr, max_depth, var_name):
    """Print a quick summary of data coverage to help diagnose sparse overlap."""
    z = _depth_pos(dep_arr)
    obs_valid = ~np.isnan(obs_arr) & ~np.isnan(z) & (z <= max_depth)
    mod_valid = ~np.isnan(mod_arr) & ~np.isnan(z) & (z <= max_depth)
    both_valid = obs_valid & mod_valid
    n_profiles = obs_arr.shape[0]
    n_obs_prof  = int((obs_valid.sum(axis=1) >= 1).sum())
    n_mod_prof  = int((mod_valid.sum(axis=1) >= 1).sum())
    n_both_prof = int((both_valid.sum(axis=1) >= 2).sum())
    print(f"  [plot_argo] {var_name} data coverage (depth ≤ {max_depth:.0f} m):")
    print(f"    Total profiles     : {n_profiles}")
    print(f"    With obs data      : {n_obs_prof}")
    print(f"    With model data    : {n_mod_prof}  "
          f"(model NaN outside SCHISM sigma range)")
    print(f"    Both obs+model ≥2  : {n_both_prof}  (used for skill metrics)")


# ---------------------------------------------------------------------------
# Plot 1 — Location map
# ---------------------------------------------------------------------------

def plot_argo_location(cfg, ds, out_dir: Path, boundaries, dpi: int):
    """Scatter map of Argo profile locations coloured by date."""
    lons  = ds["lon"].values
    lats  = ds["lat"].values
    times = ds["time"].values

    time_nums = mdates.date2num(times.astype("datetime64[ms]").astype(object))
    vmin_t = mdates.date2num(np.datetime64(cfg["start_date"], "ms").astype(object))
    vmax_t = mdates.date2num(np.datetime64(cfg["end_date"],   "ms").astype(object))

    if boundaries and "mesh_extent" in boundaries:
        ext = boundaries["mesh_extent"]
        lon_min = ext[0] - PADDING_LON; lon_max = ext[1] + PADDING_LON
        lat_min = ext[2] - PADDING_LAT; lat_max = ext[3] + PADDING_LAT
    else:
        lon_min = float(lons.min()) - PADDING_LON
        lon_max = float(lons.max()) + PADDING_LON
        lat_min = float(lats.min()) - PADDING_LAT
        lat_max = float(lats.max()) + PADDING_LAT

    fw, fh = _aspect_figsize(lon_min, lon_max, lat_min, lat_max)
    fig, ax = plt.subplots(figsize=(fw, fh + 0.8), constrained_layout=True)

    _draw_boundaries(ax, boundaries)

    sc = ax.scatter(lons, lats, c=time_nums, cmap="turbo",
                    vmin=vmin_t, vmax=vmax_t,
                    s=14, edgecolor="k", linewidth=0.2, zorder=6)

    cbar = fig.colorbar(sc, ax=ax, orientation="horizontal",
                        pad=0.06, shrink=0.85, aspect=40)
    cbar.set_label("Profile Date", fontsize=CBAR_FS)
    cbar.ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    cbar.ax.tick_params(labelsize=TICK_FS, rotation=30)

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_xlabel("Longitude (°E)", fontsize=LABEL_FS)
    ax.set_ylabel("Latitude (°N)", fontsize=LABEL_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.set_aspect("equal")
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

def _compute_profile_metrics(obs_arr, mod_arr, depth_arr, max_depth: float):
    """Per-profile R², Bias, RMSE for levels where BOTH obs AND model are valid
    and |depth| ≤ max_depth.  Requires ≥ 2 overlapping levels per profile.

    NOTE: model values are NaN outside the SCHISM sigma layer range.
    Profiles with fewer than 2 overlapping levels are skipped.
    """
    r2_list, bias_list, rmse_list = [], [], []
    z_all = _depth_pos(depth_arr)   # (n_profiles, n_levels) — positive downward

    for i in range(obs_arr.shape[0]):
        z   = z_all[i]
        obs = obs_arr[i]
        mod = mod_arr[i]
        # Require: obs valid AND model valid AND within depth limit
        mask = ~np.isnan(obs) & ~np.isnan(mod) & ~np.isnan(z) & (z <= max_depth)
        if mask.sum() < 2:
            continue
        o = obs[mask]; m = mod[mask]
        bias = float(np.mean(m - o))
        rmse = float(np.sqrt(np.mean((m - o) ** 2)))
        if np.var(o) > 1e-8 and np.var(m) > 1e-8:
            r2 = float(np.corrcoef(o, m)[0, 1] ** 2)
        else:
            r2 = np.nan
        bias_list.append(bias)
        rmse_list.append(rmse)
        r2_list.append(r2)
    return r2_list, bias_list, rmse_list


def plot_argo_histograms(cfg, ds, var: str, out_dir: Path,
                         max_depth: float, dpi: int):
    """1 × 3 histogram figure: R², Mean Bias, RMSE."""
    meta = VAR_META[var]
    obs_arr = ds[meta["obs_var"]].values
    mod_arr = ds[meta["mod_var"]].values
    dep_arr = ds["depth"].values

    _data_summary(obs_arr, mod_arr, dep_arr, max_depth, var)

    r2_list, bias_list, rmse_list = _compute_profile_metrics(
        obs_arr, mod_arr, dep_arr, max_depth)

    r2_arr   = np.array([v for v in r2_list   if not np.isnan(v)])
    bias_arr = np.array([v for v in bias_list if not np.isnan(v)])
    rmse_arr = np.array([v for v in rmse_list if not np.isnan(v)])

    if len(bias_arr) == 0:
        print(f"  [plot_argo] histograms ({var}): no overlapping obs+model "
              f"levels within {max_depth:.0f} m — try increasing "
              f"collocate_argo_plot_max_depth in postprocess.yaml.")
        return

    print(f"  [plot_argo] histograms ({var}): {len(bias_arr)} valid profiles.")

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    ax_r2, ax_bias, ax_rmse = axes

    panels = [
        (ax_r2,   r2_arr,   f"R² (Pearson)",
         "royalblue",    meta["r2_xlim"],          (0, None)),
        (ax_bias, bias_arr, f"Mean Bias ({meta['unit']})",
         "crimson",      meta["hist_bias_range"],   (0, None)),
        (ax_rmse, rmse_arr, f"RMSE ({meta['unit']})",
         "forestgreen",  meta["hist_rmse_range"],   (0, None)),
    ]

    letters = "abc"
    for (ax, data, xlabel, color, xlim, _ylim), letter in zip(panels, letters):
        ax.hist(data, bins=40, range=xlim,
                color=color, alpha=0.7, edgecolor="black", linewidth=0.8)
        median_val = float(np.median(data))
        p10 = float(np.percentile(data, 10))
        p90 = float(np.percentile(data, 90))
        ax.axvline(p10,        color="#333333", linestyle=":",  linewidth=1.5,
                   label=f"10th %: {p10:.2f}")
        ax.axvline(median_val, color="k",       linestyle="--", linewidth=2.0,
                   label=f"Median: {median_val:.2f}")
        ax.axvline(p90,        color="#333333", linestyle=":",  linewidth=1.5,
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
        f"{meta['label']} Profile Skill  |  "
        f"Depth ≤ {max_depth:.0f} m  |  n = {len(bias_arr)} profiles  |  "
        f"{start} to {end}",
        fontsize=TITLE_FS, fontweight="bold",
    )

    out_path = out_dir / f"argo_skill_histograms_{var}.jpg"
    fig.savefig(str(out_path), dpi=dpi, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": 90})
    plt.close(fig)
    print(f"  [plot_argo] histograms ({var}) -> {out_path.name}")


# ---------------------------------------------------------------------------
# Plot 3 — Profile matrix (obs | model | bias | RMSE)
# ---------------------------------------------------------------------------

def _interp_profile(z_pos, values, common_depth):
    """Interpolate a profile onto common_depth (positive downward).
    Returns NaN where extrapolation would be needed."""
    valid = ~np.isnan(z_pos) & ~np.isnan(values)
    if valid.sum() < 2:
        return np.full_like(common_depth, np.nan)
    sort_idx = np.argsort(z_pos[valid])
    zv = z_pos[valid][sort_idx]
    vv = values[valid][sort_idx]
    f = sci_interp.interp1d(zv, vv, bounds_error=False, fill_value=np.nan)
    return f(common_depth)


def plot_argo_profiles(cfg, ds, var: str, out_dir: Path,
                       max_depth: float, dpi: int):
    """2 × 2 profile matrix: obs | model | bias ± 1σ | RMSE.

    Obs and model panels are plotted independently:
      - Obs  : all profiles that have ≥ 2 valid levels within max_depth
      - Model: all profiles that have ≥ 1 valid model level within max_depth
      - Bias / RMSE: pooled per-level statistics interpolated onto a common
        depth grid, computed only where BOTH obs and model are non-NaN
    """
    meta      = VAR_META[var]
    obs_arr   = ds[meta["obs_var"]].values     # (time, n_levels)
    mod_arr   = ds[meta["mod_var"]].values     # (time, n_levels)
    dep_arr   = _depth_pos(ds["depth"].values) # positive downward

    _data_summary(obs_arr, mod_arr, dep_arr, max_depth, var)

    # ---- Select profiles valid for each panel independently ----
    obs_idx, mod_idx = [], []
    for i in range(obs_arr.shape[0]):
        z = dep_arr[i]
        m_obs = ~np.isnan(obs_arr[i]) & ~np.isnan(z) & (z <= max_depth)
        m_mod = ~np.isnan(mod_arr[i]) & ~np.isnan(z) & (z <= max_depth)
        if m_obs.sum() >= 2:
            obs_idx.append(i)
        if m_mod.sum() >= 1:
            mod_idx.append(i)

    if not obs_idx:
        print(f"  [plot_argo] profiles ({var}): no obs profiles within "
              f"{max_depth:.0f} m — try increasing collocate_argo_plot_max_depth.")
        return

    print(f"  [plot_argo] profiles ({var}): "
          f"{len(obs_idx)} obs profiles, {len(mod_idx)} model profiles "
          f"within {max_depth:.0f} m.")

    # ---- Common depth grid for pooled bias / RMSE ----
    step = 5.0
    common_depth = np.arange(0, max_depth + step, step)

    # Interpolate obs and model onto common_depth for ALL profiles that
    # have any data (not requiring both to be valid for the same profile).
    # Bias/RMSE are only computed at levels where BOTH are non-NaN.
    obs_interp_all = []
    mod_interp_all = []
    for i in range(obs_arr.shape[0]):
        z = dep_arr[i]
        oi = _interp_profile(z, obs_arr[i], common_depth)
        mi = _interp_profile(z, mod_arr[i], common_depth)
        obs_interp_all.append(oi)
        mod_interp_all.append(mi)

    obs_mat = np.array(obs_interp_all)  # (n_profiles, n_depth)
    mod_mat = np.array(mod_interp_all)

    # Per-level stats: only where BOTH are non-NaN
    err_mat   = mod_mat - obs_mat   # NaN where either is NaN
    mean_err  = np.nanmean(err_mat, axis=0)
    std_err   = np.nanstd(err_mat,  axis=0)
    rmse_prof = np.sqrt(np.nanmean(err_mat ** 2, axis=0))

    # Count of valid overlapping profiles per depth level (for reference)
    n_overlap = np.sum(~np.isnan(err_mat), axis=0)

    # ---- Figure ----
    fig = plt.figure(figsize=(12, 10), constrained_layout=True)
    gs  = gridspec.GridSpec(2, 2, figure=fig, wspace=0.08, hspace=0.08)
    ax_obs  = fig.add_subplot(gs[0, 0])
    ax_mod  = fig.add_subplot(gs[0, 1], sharey=ax_obs)
    ax_bias = fig.add_subplot(gs[1, 0], sharey=ax_obs)
    ax_rmse = fig.add_subplot(gs[1, 1], sharey=ax_obs)

    alpha = 0.15; lw = 0.8
    mod_color = meta["mod_color"]

    # ---- a) Argo observations ----
    obs_lines = []
    for i in obs_idx:
        z = dep_arr[i]; o = obs_arr[i]
        m = ~np.isnan(o) & ~np.isnan(z) & (z <= max_depth)
        if m.sum() >= 2:
            obs_lines.append(np.column_stack((o[m], z[m])))
    if obs_lines:
        ax_obs.add_collection(
            LineCollection(obs_lines, colors="gray", alpha=alpha, linewidths=lw))
    ax_obs.autoscale_view()
    ax_obs.set_title("Argo Observations", fontsize=TITLE_FS,
                     fontweight="bold", pad=4)
    ax_obs.set_xlabel(f"{meta['label']} ({meta['unit']})", fontsize=LABEL_FS)
    ax_obs.set_ylabel("Depth (m)", fontsize=LABEL_FS)
    ax_obs.tick_params(labelsize=TICK_FS)
    ax_obs.grid(True, linestyle="--", alpha=0.4)
    _panel_letter(ax_obs, "a")

    # Robust x limits from all valid obs within depth range
    obs_flat = obs_arr[obs_idx][
        ~np.isnan(obs_arr[obs_idx]) & (dep_arr[obs_idx] <= max_depth)]
    if obs_flat.size > 0:
        xlo = float(np.percentile(obs_flat, 2))
        xhi = float(np.percentile(obs_flat, 98))
        ax_obs.set_xlim(xlo, xhi)

    # ---- b) SCHISM model ----
    mod_lines = []
    for i in mod_idx:
        z = dep_arr[i]; m = mod_arr[i]
        mk = ~np.isnan(m) & ~np.isnan(z) & (z <= max_depth)
        if mk.sum() >= 1:
            mod_lines.append(np.column_stack((m[mk], z[mk])))
    if mod_lines:
        ax_mod.add_collection(
            LineCollection(mod_lines, colors=mod_color, alpha=alpha, linewidths=lw))
    ax_mod.autoscale_view()
    ax_mod.set_title("SCHISM Model", fontsize=TITLE_FS,
                     fontweight="bold", pad=4, color=mod_color)
    ax_mod.set_xlabel(f"{meta['label']} ({meta['unit']})", fontsize=LABEL_FS)
    ax_mod.tick_params(axis="y", labelleft=False, labelsize=TICK_FS)
    ax_mod.tick_params(axis="x", labelsize=TICK_FS)
    ax_mod.grid(True, linestyle="--", alpha=0.4)
    _panel_letter(ax_mod, "b")
    if obs_flat.size > 0:
        ax_mod.set_xlim(xlo, xhi)

    # ---- c) Mean Bias ± 1σ (pooled per level) ----
    # Mask depth levels where we have no overlap at all
    has_data = n_overlap > 0
    if has_data.any():
        ax_bias.plot(mean_err[has_data], common_depth[has_data],
                     color=mod_color, lw=2)
        ax_bias.fill_betweenx(
            common_depth[has_data],
            (mean_err - std_err)[has_data],
            (mean_err + std_err)[has_data],
            alpha=0.20, color=mod_color,
        )
    ax_bias.axvline(0, color="k", lw=1.2)
    ax_bias.set_title("Mean Bias (Model − Obs)", fontsize=TITLE_FS,
                      fontweight="bold", pad=4)
    ax_bias.set_xlabel(f"Bias ({meta['unit']})", fontsize=LABEL_FS)
    ax_bias.set_ylabel("Depth (m)", fontsize=LABEL_FS)
    ax_bias.tick_params(labelsize=TICK_FS)
    ax_bias.grid(True, linestyle="--", alpha=0.4)
    _panel_letter(ax_bias, "c")

    valid_err = mean_err[has_data] if has_data.any() else np.array([0.0])
    valid_std = std_err[has_data]  if has_data.any() else np.array([0.0])
    max_abs = float(np.nanmax(np.abs(valid_err + valid_std)))
    max_abs = max(0.1, np.ceil(max_abs / 0.5) * 0.5)
    ax_bias.set_xlim(-max_abs, max_abs)

    # ---- d) RMSE (pooled per level) ----
    if has_data.any():
        ax_rmse.plot(rmse_prof[has_data], common_depth[has_data],
                     color=mod_color, lw=2)
    ax_rmse.set_title("RMSE", fontsize=TITLE_FS, fontweight="bold", pad=4)
    ax_rmse.set_xlabel(f"RMSE ({meta['unit']})", fontsize=LABEL_FS)
    ax_rmse.tick_params(axis="y", labelleft=False, labelsize=TICK_FS)
    ax_rmse.tick_params(axis="x", labelsize=TICK_FS)
    ax_rmse.grid(True, linestyle="--", alpha=0.4)
    _panel_letter(ax_rmse, "d")
    ax_rmse.set_xlim(0, max_abs)

    # ---- Shared y-axis ----
    ax_obs.set_ylim(max_depth, 0)   # 0 at top, max_depth at bottom

    start = cfg["start_date"]; end = cfg["end_date"]
    fig.suptitle(
        f"SCHISM vs Argo — {meta['label']}  |  "
        f"Depth ≤ {max_depth:.0f} m  |  {start} to {end}",
        fontsize=TITLE_FS, fontweight="bold",
    )

    out_path = out_dir / f"argo_profiles_{var}.jpg"
    fig.savefig(str(out_path), dpi=dpi, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": 90})
    plt.close(fig)
    print(f"  [plot_argo] profiles ({var}) -> {out_path.name}")


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def run_plot_argo(cfg: dict, config_dir=None):
    """Run all three Argo diagnostic plots."""
    out_dir = _out_dir(cfg)
    if not out_dir.is_dir():
        print(f"ERROR: collocation output dir not found: {out_dir}")
        print("  Run collocate_argo first.")
        return

    max_depth   = float(cfg.get("collocate_argo_plot_max_depth",  300))
    max_dist_km = cfg.get("collocate_argo_plot_max_dist_km")
    dpi         = int(cfg.get("collocate_argo_plot_dpi", 150))
    variables   = cfg.get("collocate_argo_vars") or ["temperature", "salinity"]
    variables   = [v for v in variables if v in VAR_META]

    if not variables:
        print("ERROR: collocate_argo_vars has no recognised variables "
              "(expected temperature and/or salinity).")
        return

    print(f"\n{'='*60}")
    print(f"  Argo diagnostic plots")
    print(f"  Variables: {variables}")
    print(f"  Max depth: {max_depth} m  |  DPI: {dpi}")
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
            print(f"  [plot_argo] {var}: no profiles remain after distance filter, skipping.")
            continue
        datasets[var] = ds

    if not datasets:
        print("  [plot_argo] no collocated data available for any variable. "
              "Run collocate_argo first.")
        return

    # Plot 1: location map (once, from first available variable)
    first_var = next(iter(datasets))
    plot_argo_location(cfg, datasets[first_var], out_dir, boundaries, dpi)

    # Plots 2 & 3: per variable
    for var, ds in datasets.items():
        plot_argo_histograms(cfg, ds, var, out_dir, max_depth, dpi)
        plot_argo_profiles(cfg, ds, var, out_dir, max_depth, dpi)
        ds.close()

    (out_dir / "plot_argo.done").touch()
    print(f"\n{'='*60}")
    print(f"  Argo plots complete. All figures in {out_dir}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Argo diagnostic plots for SCHISM collocation")
    ap.add_argument("--config", required=True,
                    help="Path to project config/ directory")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    run_plot_argo(cfg, args.config)
