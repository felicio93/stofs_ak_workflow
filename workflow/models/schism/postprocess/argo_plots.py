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

  Plot 3 — Profile matrix (argo_profiles_{var}.jpg)
      2 × 2 panel: Argo observed profiles (a), SCHISM model profiles (b),
      depth-resolved Mean Bias ± 1σ (c), and RMSE (d). Individual profiles
      drawn as semi-transparent LineCollections. One figure per variable.

Inputs
------
  P{ID}/P{ID}_collocate_argo/collocated_{var}.nc   (from collocate_argo)
  fix/hgrid.gr3                                     (for mesh boundaries)

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
  collocate_argo_plot_max_depth   depth filter in metres (default 2000)
  collocate_argo_plot_max_dist_km optional: keep only profiles ≤ X km from
                                  nearest mesh node (null = no filter)
  collocate_argo_plot_dpi         DPI for all saved figures (default 150)

Variable names in the collocated NetCDF (from OCSTrack make_collocated_nc_3d)
------------------------------------------------------------------------------
  time              (time,)             datetime64 coordinate
  lat               (time,)             degrees_north
  lon               (time,)             degrees_east — stored in 0..360 frame
  depth             (time, n_levels)    negative (multiply × -1 for display)
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
# Variable metadata: maps internal variable name -> display label, units,
# histogram x-ranges, and profile colour.
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
    """Load mesh boundaries from fix/hgrid.gr3 (or .ll). Returns None if
    neither file exists — plots proceed without boundaries."""
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
    """Open collocated_{var}.nc. Returns (ds, path) or (None, None)."""
    import xarray as xr
    nc = out_dir / f"collocated_{var}.nc"
    if not nc.exists() or nc.stat().st_size == 0:
        print(f"  [plot_argo] {nc.name} not found — run collocate_argo first.")
        return None, None
    ds = xr.open_dataset(str(nc), engine="netcdf4")
    return ds, nc


def _apply_dist_filter(ds, max_dist_km):
    """Keep only profiles where the nearest mesh node is ≤ max_dist_km km."""
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
    """Return depth as positive-downward (flip sign of negative z values)."""
    return np.abs(depth_arr)


def _panel_letter(ax, letter: str, fontsize: int = 13):
    """Place a bold panel letter (e.g. 'a)') in the top-left corner."""
    ax.text(0.04, 0.97, f"{letter})",
            transform=ax.transAxes, va="top", fontweight="bold",
            fontsize=fontsize,
            bbox=dict(facecolor="white", alpha=0.85, edgecolor="none", pad=2))


# ---------------------------------------------------------------------------
# Plot 1 — Location map
# ---------------------------------------------------------------------------

def plot_argo_location(cfg, ds, out_dir: Path, boundaries, dpi: int):
    """Scatter map of Argo profile locations coloured by date."""
    lons = ds["lon"].values
    lats = ds["lat"].values
    times = ds["time"].values  # datetime64

    time_nums = mdates.date2num(times.astype("datetime64[ms]").astype(object))

    # Colour limits from the full run window so colours are reproducible.
    vmin_t = mdates.date2num(
        np.datetime64(cfg["start_date"], "ms").astype(object))
    vmax_t = mdates.date2num(
        np.datetime64(cfg["end_date"],   "ms").astype(object))

    # --- Figure size from mesh extent (or data extent fallback) ---
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
    # Add vertical room for the horizontal colorbar below the map.
    fh_total = fh + 0.8

    fig, ax = plt.subplots(figsize=(fw, fh_total), constrained_layout=True)

    # --- Mesh boundaries ---
    _draw_boundaries(ax, boundaries)

    # --- Scatter ---
    sc = ax.scatter(
        lons, lats,
        c=time_nums, cmap="turbo",
        vmin=vmin_t, vmax=vmax_t,
        s=14, edgecolor="k", linewidth=0.2,
        zorder=6,
    )

    # --- Horizontal colorbar below the map ---
    cbar = fig.colorbar(
        sc, ax=ax, orientation="horizontal",
        pad=0.06, shrink=0.85, aspect=40,
    )
    cbar.set_label("Profile Date", fontsize=CBAR_FS)
    cbar.ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    cbar.ax.tick_params(labelsize=TICK_FS, rotation=30)

    # --- Axis formatting ---
    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_xlabel("Longitude (°E)", fontsize=LABEL_FS)
    ax.set_ylabel("Latitude (°N)", fontsize=LABEL_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.set_aspect("equal")
    ax.set_title(
        f"Argo Float Profile Locations  (n = {len(lons)})",
        fontsize=TITLE_FS, fontweight="bold", pad=10,
    )
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
    """Compute per-profile R², Bias, RMSE over |depth| ≤ max_depth.

    Returns three lists (r2_list, bias_list, rmse_list), each one value
    per profile that had ≥ 3 valid depth levels.
    """
    r2_list, bias_list, rmse_list = [], [], []
    for obs, mod, dep in zip(obs_arr, mod_arr, depth_arr):
        z = _depth_pos(dep)
        mask = (~np.isnan(obs) & ~np.isnan(mod) &
                ~np.isnan(z) & (z <= max_depth))
        if mask.sum() < 3:
            continue
        o = obs[mask]; m = mod[mask]
        bias = float(np.mean(m - o))
        rmse = float(np.sqrt(np.mean((m - o) ** 2)))
        if np.var(o) > 1e-8 and np.var(m) > 1e-8:
            r = np.corrcoef(o, m)[0, 1]
            r2 = float(r ** 2)
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

    r2_list, bias_list, rmse_list = _compute_profile_metrics(
        obs_arr, mod_arr, dep_arr, max_depth)

    # Drop NaNs cleanly.
    r2_arr   = np.array([v for v in r2_list   if not np.isnan(v)])
    bias_arr = np.array([v for v in bias_list if not np.isnan(v)])
    rmse_arr = np.array([v for v in rmse_list if not np.isnan(v)])

    if len(bias_arr) == 0:
        print(f"  [plot_argo] histograms ({var}): no valid profiles, skipping.")
        return

    print(f"  [plot_argo] histograms ({var}): {len(bias_arr)} valid profiles.")

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
    ax_r2, ax_bias, ax_rmse = axes

    # ---- data, labels, colours, x-ranges ----
    panels = [
        (ax_r2,   r2_arr,   f"R² (Pearson)",
         "royalblue", meta["r2_xlim"],   (0, None)),
        (ax_bias, bias_arr, f"Mean Bias ({meta['unit']})",
         "crimson",   meta["hist_bias_range"], (0, None)),
        (ax_rmse, rmse_arr, f"RMSE ({meta['unit']})",
         "forestgreen", meta["hist_rmse_range"], (0, None)),
    ]

    letters = "abc"
    for (ax, data, xlabel, color, xlim, _ylim), letter in zip(panels, letters):
        ax.hist(data, bins=40, range=xlim,
                color=color, alpha=0.7,
                edgecolor="black", linewidth=0.8)

        median_val  = float(np.median(data))
        p10, p90    = float(np.percentile(data, 10)), float(np.percentile(data, 90))

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
        ax.legend(loc="upper center",
                  bbox_to_anchor=(0.5, -0.16),
                  ncol=3, fontsize=TICK_FS, frameon=False)
        _panel_letter(ax, letter, fontsize=TITLE_FS)

    start = cfg["start_date"]; end = cfg["end_date"]
    fig.suptitle(
        f"{meta['label']} Profile Skill  |  Depth ≤ {max_depth:.0f} m  |  "
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
    """Linearly interpolate a single profile onto common_depth (both positive
    downward).  Returns NaN where extrapolation would be needed."""
    valid = ~np.isnan(z_pos) & ~np.isnan(values)
    if valid.sum() < 3:
        return np.full_like(common_depth, np.nan)
    sort_idx = np.argsort(z_pos[valid])
    zv = z_pos[valid][sort_idx]
    vv = values[valid][sort_idx]
    f = sci_interp.interp1d(zv, vv, bounds_error=False, fill_value=np.nan)
    return f(common_depth)


def plot_argo_profiles(cfg, ds, var: str, out_dir: Path,
                       max_depth: float, dpi: int):
    """2 × 2 profile matrix: obs | model | bias ± 1σ | RMSE."""
    meta = VAR_META[var]
    obs_arr = ds[meta["obs_var"]].values   # (time, n_levels)
    mod_arr = ds[meta["mod_var"]].values   # (time, n_levels)
    dep_arr = _depth_pos(ds["depth"].values)  # positive downward

    # Only keep profiles that have at least 3 valid levels within max_depth.
    valid_profiles = []
    for i in range(obs_arr.shape[0]):
        z = dep_arr[i]; o = obs_arr[i]; m = mod_arr[i]
        mask = (~np.isnan(z) & ~np.isnan(o) & ~np.isnan(m) & (z <= max_depth))
        if mask.sum() >= 3:
            valid_profiles.append(i)

    if not valid_profiles:
        print(f"  [plot_argo] profiles ({var}): no valid profiles, skipping.")
        return

    print(f"  [plot_argo] profiles ({var}): {len(valid_profiles)} valid profiles.")

    obs_v   = obs_arr[valid_profiles]
    mod_v   = mod_arr[valid_profiles]
    dep_v   = dep_arr[valid_profiles]

    # Common depth grid for bias / RMSE curves.
    step = 5.0
    common_depth = np.arange(0, max_depth + step, step)

    # Interpolate each profile error onto common_depth.
    err_profiles = []
    obs_interp   = []
    mod_interp   = []
    for z, o, m in zip(dep_v, obs_v, mod_v):
        oi = _interp_profile(z, o, common_depth)
        mi = _interp_profile(z, m, common_depth)
        obs_interp.append(oi)
        mod_interp.append(mi)
        err_profiles.append(mi - oi)

    err_arr  = np.array(err_profiles)   # (n_profiles, n_depth)
    obs_i    = np.array(obs_interp)
    mod_i    = np.array(mod_interp)

    mean_err  = np.nanmean(err_arr,  axis=0)
    std_err   = np.nanstd(err_arr,   axis=0)
    rmse_prof = np.sqrt(np.nanmean(err_arr ** 2, axis=0))

    # ---- Figure ----
    fig = plt.figure(figsize=(12, 10), constrained_layout=True)
    gs = gridspec.GridSpec(2, 2, figure=fig, wspace=0.08, hspace=0.08)

    ax_obs  = fig.add_subplot(gs[0, 0])
    ax_mod  = fig.add_subplot(gs[0, 1], sharey=ax_obs)
    ax_bias = fig.add_subplot(gs[1, 0], sharey=ax_obs)
    ax_rmse = fig.add_subplot(gs[1, 1], sharey=ax_obs)

    alpha = 0.15; lw = 0.8
    mod_color = meta["mod_color"]

    # ---- a) Argo observations ----
    obs_lines = [
        np.column_stack((o[~np.isnan(o) & (d <= max_depth)],
                         d[~np.isnan(o) & (d <= max_depth)]))
        for o, d in zip(obs_v, dep_v)
        if (~np.isnan(o) & (d <= max_depth)).sum() >= 2
    ]
    if obs_lines:
        ax_obs.add_collection(
            LineCollection(obs_lines, colors="gray", alpha=alpha, linewidths=lw))
    ax_obs.set_title("Argo Observations", fontsize=TITLE_FS,
                     fontweight="bold", pad=4)
    ax_obs.set_xlabel(f"{meta['label']} ({meta['unit']})", fontsize=LABEL_FS)
    ax_obs.set_ylabel("Depth (m)", fontsize=LABEL_FS)
    ax_obs.tick_params(labelsize=TICK_FS)
    ax_obs.grid(True, linestyle="--", alpha=0.4)
    _panel_letter(ax_obs, "a")

    # Robust x limits from 2nd–98th percentile of valid obs values.
    obs_flat = obs_v[~np.isnan(obs_v) & (dep_v <= max_depth)]
    if obs_flat.size > 0:
        xlo = float(np.percentile(obs_flat, 2))
        xhi = float(np.percentile(obs_flat, 98))
        ax_obs.set_xlim(xlo, xhi)

    # ---- b) SCHISM model ----
    mod_lines = [
        np.column_stack((m[~np.isnan(m) & (d <= max_depth)],
                         d[~np.isnan(m) & (d <= max_depth)]))
        for m, d in zip(mod_v, dep_v)
        if (~np.isnan(m) & (d <= max_depth)).sum() >= 2
    ]
    if mod_lines:
        ax_mod.add_collection(
            LineCollection(mod_lines, colors=mod_color, alpha=alpha, linewidths=lw))
    ax_mod.set_title("SCHISM Model", fontsize=TITLE_FS,
                     fontweight="bold", pad=4, color=mod_color)
    ax_mod.set_xlabel(f"{meta['label']} ({meta['unit']})", fontsize=LABEL_FS)
    ax_mod.tick_params(axis="y", labelleft=False, labelsize=TICK_FS)
    ax_mod.tick_params(axis="x", labelsize=TICK_FS)
    ax_mod.grid(True, linestyle="--", alpha=0.4)
    _panel_letter(ax_mod, "b")
    if obs_flat.size > 0:
        ax_mod.set_xlim(xlo, xhi)

    # ---- c) Mean Bias ± 1σ ----
    ax_bias.plot(mean_err, common_depth, color=mod_color, lw=2)
    ax_bias.fill_betweenx(
        common_depth,
        mean_err - std_err, mean_err + std_err,
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

    # Symmetric x limits for bias panel.
    max_abs = float(np.nanmax(np.abs(mean_err + std_err)))
    max_abs = max(0.1, np.ceil(max_abs / 0.5) * 0.5)  # round up to nearest 0.5
    ax_bias.set_xlim(-max_abs, max_abs)

    # ---- d) RMSE ----
    ax_rmse.plot(rmse_prof, common_depth, color=mod_color, lw=2)
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

    max_depth   = float(cfg.get("collocate_argo_plot_max_depth",  2000))
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

    # Load mesh boundaries once (shared by all plots).
    boundaries = _load_boundaries(cfg)

    # Load all requested datasets up front.
    datasets = {}
    for var in variables:
        ds, _ = _load_collocated(out_dir, var)
        if ds is None:
            continue
        ds = _apply_dist_filter(ds, max_dist_km)
        if ds.sizes.get("time", 0) == 0:
            print(f"  [plot_argo] {var}: no profiles after distance filter, skipping.")
            continue
        datasets[var] = ds

    if not datasets:
        print("  [plot_argo] no collocated data available for any variable. "
              "Run collocate_argo first.")
        return

    # --- Plot 1: location map (once, using first available variable) ---
    first_var = next(iter(datasets))
    plot_argo_location(cfg, datasets[first_var], out_dir, boundaries, dpi)

    # --- Plots 2 & 3: per variable ---
    for var, ds in datasets.items():
        plot_argo_histograms(cfg, ds, var, out_dir, max_depth, dpi)
        plot_argo_profiles(cfg, ds, var, out_dir, max_depth, dpi)
        ds.close()

    (out_dir / "plot_argo.done").touch()
    print(f"\n{'='*60}")
    print(f"  Argo plots complete. All figures in {out_dir}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI entry point (for direct invocation / testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Argo diagnostic plots for SCHISM collocation")
    ap.add_argument("--config", required=True, help="Path to project config/ directory")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    run_plot_argo(cfg, args.config)
