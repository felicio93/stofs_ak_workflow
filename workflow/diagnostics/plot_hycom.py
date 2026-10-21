"""
plot_hycom.py — HYCOM debug GIFs and verification of the HYCOM-derived inputs.

Two modes, one group per SLURM task, both using the shared plot_style:

  (default)   debug GIFs from the aggregated HYCOM stacks (TS/SSH/UV_1.nc).
              Plots every day.

  --outputs   verifies the SCHISM inputs built from those stacks
              (gen_3Dth, gen_nudge and, for the first group, gen_hotstart)
              and saves figures next to the GIFs.  Fails (non-zero exit, no
              sentinel) when a file is missing, has no time records, holds
              non-finite values or is constant, i.e. when HYCOM data were
              not actually used.  Values outside the expected range, or far
              from HYCOM at the open boundary, only produce warnings.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).
"""

import argparse
import gc
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
import imageio.v2 as imageio
import matplotlib.pyplot as plt

from workflow.core.config import (
    load_config,
    list_groups,
    model_dir,
    group_date_range,
    get_group_ndays,
)
from workflow.core.mesh_parser import read_nodes, read_open_boundaries
from workflow.core.plot_style import (
    make_frame,
    read_mesh_boundaries,
    _draw_boundaries,
    TITLE_FS,
    LABEL_FS,
    TICK_FS,
)


DPI     = 120
QUALITY = 85


def parse_range(value):
    if value is None:
        return (None, None)
    lo, hi = value
    lo = None if (isinstance(lo, str)
                  and lo.lower() == "auto") else lo
    hi = None if (isinstance(hi, str)
                  and hi.lower() == "auto") else hi
    return (lo, hi)


def robust_limits(data, lo, hi):
    arr    = np.asarray(data)
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
    shape      = [1, 1, 1]
    shape[depth_axis] = arr.shape[depth_axis]
    depth_idx  = np.arange(arr.shape[depth_axis]).reshape(shape)
    masked_idx = np.where(valid, depth_idx, -1)
    deepest    = masked_idx.max(axis=depth_axis)
    deepest_s  = np.clip(deepest, 0,
                         arr.shape[depth_axis] - 1)
    bottom_v   = np.take_along_axis(
        arr,
        np.expand_dims(deepest_s, axis=depth_axis),
        axis=depth_axis,
    ).squeeze(axis=depth_axis)
    bottom_v = np.where(deepest >= 0, bottom_v, np.nan)
    return block.isel(depth=0).copy(data=bottom_v)


def make_gif(frames, gif_path, fps=5):
    if not frames:
        print(f"  No frames for {gif_path.name}, skipping.")
        return
    images = [imageio.imread(f) for f in frames]
    imageio.mimsave(gif_path, images,
                    duration=1.0/fps, loop=0)
    for f in frames:
        Path(f).unlink(missing_ok=True)
    print(f"  Wrote {gif_path}")


def check_missing_days(ntime, cfg, ym, tag):
    """Warn if the number of time records is less than the group length.

    Uses get_group_ndays so it works correctly for both monthly
    (YYYYMM) and ndays (YYYYMMDD) group IDs.
    """
    ndays = get_group_ndays(cfg, ym)
    if ntime < ndays:
        print(f"  WARNING [{tag} {ym}]: {ntime} records, "
              f"group has {ndays} days.")


def plot_two_layer_var(ds, varname, label, cmap, vlim,
                       out_dir, ym, tmp_dir,
                       boundaries=None):
    lo, hi = robust_limits(ds[varname].values, *vlim)
    frames = []
    ntime  = ds.sizes["time"]
    print(f"  {varname}: rendering {ntime} frames...")

    lon2d = ds["xlon"].values
    lat2d = ds["ylat"].values

    for i in range(ntime):
        t       = pd.Timestamp(
            ds.time.values[i]).strftime("%Y-%m-%d")
        block   = ds[varname].isel(time=i)
        surface = block.isel(depth=0).values
        bottom  = bottom_layer(block).values

        # Surface
        fp = tmp_dir / f"{varname}_surf_{ym}_{i:03d}.jpg"
        make_frame(lon2d, lat2d, surface,
                   f"Surface {label}", t,
                   cmap, lo, hi, label, fp,
                   dpi=DPI, quality=QUALITY,
                   boundaries=boundaries)
        frames.append(str(fp))

        # Bottom
        fp2 = tmp_dir / f"{varname}_bot_{ym}_{i:03d}.jpg"
        make_frame(lon2d, lat2d, bottom,
                   f"Bottom {label}", t,
                   cmap, lo, hi, label, fp2,
                   dpi=DPI, quality=QUALITY,
                   boundaries=boundaries)
        frames.append(str(fp2))

        print(f"    frame {i+1:>3}/{ntime}  ({t})",
              flush=True)

    surf_frames = frames[0::2]
    bot_frames  = frames[1::2]
    make_gif(surf_frames,
             out_dir / f"HYCOM_{varname}_surface_{ym}.gif")
    make_gif(bot_frames,
             out_dir / f"HYCOM_{varname}_bottom_{ym}.gif")


def plot_ssh(ds, cmap, vlim, out_dir, ym, tmp_dir,
             boundaries=None):
    lo, hi = robust_limits(ds["surf_el"].values, *vlim)
    frames = []
    ntime  = ds.sizes["time"]
    print(f"  ssh: rendering {ntime} frames...")

    lon2d = ds["xlon"].values
    lat2d = ds["ylat"].values

    for i in range(ntime):
        t    = pd.Timestamp(
            ds.time.values[i]).strftime("%Y-%m-%d")
        vals = ds["surf_el"].isel(time=i).values
        fp   = tmp_dir / f"ssh_{ym}_{i:03d}.jpg"
        make_frame(lon2d, lat2d, vals,
                   "Sea Surface Height", t,
                   cmap, lo, hi, "SSH (m)", fp,
                   dpi=DPI, quality=QUALITY,
                   boundaries=boundaries)
        frames.append(str(fp))
        print(f"    frame {i+1:>3}/{ntime}  ({t})",
              flush=True)
    make_gif(frames, out_dir / f"HYCOM_ssh_{ym}.gif")


def plot_month(cfg: dict, ym: str):
    """Plot HYCOM debug GIFs for one group.

    ym is a group ID: 'YYYYMM' for monthly grouping or
    'YYYYMMDD' for ndays grouping.
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
    ddir = mdir / f"D{pid}" / f"D{pid}_{ym}"
    ddir.mkdir(parents=True, exist_ok=True)
    tmp      = ddir / "frames_tmp"
    sentinel = ddir / "plot_hycom.done"

    if sentinel.exists():
        print(f"  plot_hycom: {ym} already complete. Skipping.")
        return

    tmp.mkdir(parents=True, exist_ok=True)

    cmap     = cfg.get("plot_cmap", "jet")
    temp_lim = parse_range(cfg.get("plot_temp_range"))
    salt_lim = parse_range(cfg.get("plot_salt_range"))
    ssh_lim  = parse_range(cfg.get("plot_ssh_range"))
    uvel_lim = parse_range(
        cfg.get("plot_uvel_range", [-1, 1]))
    vvel_lim = parse_range(
        cfg.get("plot_vvel_range", [-1, 1]))

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
                print(f"  WARNING: could not read "
                      f"boundaries: {exc}")
            break

    ts_file  = idir / "TS_1.nc"
    ssh_file = idir / "SSH_1.nc"
    uv_file  = idir / "UV_1.nc"

    # Determine group info for display
    gstart, gend = group_date_range(cfg, ym)
    ndays        = get_group_ndays(cfg, ym)
    print(f"\n--- Plotting {ym}  "
          f"({gstart} -> {gend}, {ndays} days) -> {ddir} ---")

    if ts_file.exists():
        ds = xr.open_dataset(ts_file, decode_times=True)
        check_missing_days(ds.sizes["time"], cfg, ym, "TS")
        if "temperature" in ds:
            plot_two_layer_var(
                ds, "temperature",
                "Potential Temp (°C)", cmap, temp_lim,
                ddir, ym, tmp, boundaries=boundaries)
        ds.close(); del ds; gc.collect()

        ds = xr.open_dataset(ts_file, decode_times=True)
        if "salinity" in ds:
            plot_two_layer_var(
                ds, "salinity",
                "Salinity (psu)", cmap, salt_lim,
                ddir, ym, tmp, boundaries=boundaries)
        ds.close(); del ds; gc.collect()
    else:
        print(f"  WARNING: {ts_file} not found.")

    if ssh_file.exists():
        ds = xr.open_dataset(ssh_file, decode_times=True)
        check_missing_days(ds.sizes["time"], cfg, ym, "SSH")
        plot_ssh(ds, cmap, ssh_lim, ddir, ym, tmp,
                 boundaries=boundaries)
        ds.close(); del ds; gc.collect()
    else:
        print(f"  WARNING: {ssh_file} not found.")

    if uv_file.exists():
        ds = xr.open_dataset(uv_file, decode_times=True)
        check_missing_days(ds.sizes["time"], cfg, ym, "UV")
        if "water_u" in ds:
            plot_two_layer_var(
                ds, "water_u",
                "U Current (m/s)", cmap, uvel_lim,
                ddir, ym, tmp, boundaries=boundaries)
        ds.close(); del ds; gc.collect()

        ds = xr.open_dataset(uv_file, decode_times=True)
        if "water_v" in ds:
            plot_two_layer_var(
                ds, "water_v",
                "V Current (m/s)", cmap, vvel_lim,
                ddir, ym, tmp, boundaries=boundaries)
        ds.close(); del ds; gc.collect()
    else:
        print(f"  WARNING: {uv_file} not found.")

    try:
        tmp.rmdir()
    except OSError:
        pass

    gif_count = len(list(ddir.glob("HYCOM_*.gif")))
    if gif_count > 0:
        sentinel.touch()
        print(f"  {gif_count} GIF(s) written. "
              f"Sentinel: {sentinel}")
    else:
        print(f"  WARNING: no GIFs produced for {ym}. "
              f"Sentinel NOT written.")


# =============================================================================
# Verification of the HYCOM-derived SCHISM inputs (--outputs)
# =============================================================================

# Expected value ranges: outside them only a warning is raised.
RANGES = {
    "T":    (-3.0, 40.0),
    "S":    (0.0, 45.0),
    "UV":   (-5.0, 5.0),
    "SSH":  (-20.0, 20.0),
}
# Mean |output - nearest HYCOM| at the open boundary (day 0) above which a
# warning is raised.  The validated M11 inputs gave 0.03 degC, 0.02 psu and
# 0.002 m.
BND_TOL = {"T": 1.0, "S": 0.5, "SSH": 0.1}


def _nearest(axis, values):
    """Index of the nearest element of the ascending `axis` for each value."""
    i = np.clip(np.searchsorted(axis, values), 1, len(axis) - 1)
    return np.where(np.abs(values - axis[i - 1]) <= np.abs(values - axis[i]),
                    i - 1, i)


def _check_array(name, a, rng, fails, warns):
    """Structural checks (fail) and range check (warn). Returns True if ok."""
    if a.shape[0] == 0:
        fails.append(f"{name}: no time records")
        return False
    bad = ~np.isfinite(a)
    if bad.any():
        fails.append(f"{name}: {int(bad.sum())} non-finite value(s)")
        return False
    if float(np.ptp(a)) == 0.0:
        fails.append(f"{name}: constant ({float(a.flat[0]):g}) -- "
                     f"HYCOM data were not used")
        return False
    lo, hi = rng
    if float(a.min()) < lo or float(a.max()) > hi:
        warns.append(f"{name}: values {float(a.min()):.3g}..{float(a.max()):.3g}"
                     f" outside expected {lo}..{hi}")
    print(f"  {name:14s} min={float(a.min()):9.3f} max={float(a.max()):9.3f}"
          f" std={float(a.std()):8.3f}")
    return True


def _load_th(path):
    """time_series of a *.th.nc / *_nu.nc file as float64 (time,node,lev,comp)."""
    with xr.open_dataset(path, decode_times=False) as ds:
        return ds["time_series" if "time_series" in ds
                  else "tracer_concentration"].values.astype("f8")


def _save_jpeg(fig, path):
    fig.savefig(path, dpi=DPI, format="jpeg", bbox_inches="tight",
                pil_kwargs={"quality": QUALITY})
    plt.close(fig)
    print(f"  Wrote {path}")


def _scatter_panel(ax, lon, lat, vals, title, label, cmap, boundaries, vlim):
    lo, hi = robust_limits(vals, *vlim)
    sc = ax.scatter(lon, lat, c=vals, s=1.0, cmap=cmap, vmin=lo, vmax=hi,
                    rasterized=True)
    cb = ax.figure.colorbar(sc, ax=ax, location="right", pad=0.02,
                            fraction=0.046, shrink=0.9)
    cb.set_label(label, fontsize=LABEL_FS)
    cb.ax.tick_params(labelsize=TICK_FS)
    _draw_boundaries(ax, boundaries)
    ax.set_xlabel("Longitude (°E)", fontsize=LABEL_FS)
    ax.set_ylabel("Latitude (°N)", fontsize=LABEL_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=TITLE_FS, fontweight="bold", pad=10)


def _verify_boundary(cfg, idir, ddir, ym, hgrid_ll, fails, warns):
    """Check elev2D / uv3D / TEM_3D / SAL_3D and compare with HYCOM."""
    files = {"T": "TEM_3D.th.nc", "S": "SAL_3D.th.nc",
             "UV": "uv3D.th.nc", "SSH": "elev2D.th.nc"}
    data = {}
    print("  -- open-boundary files (gen_3Dth) --")
    for key, fname in files.items():
        path = idir / fname
        if not path.exists() or path.stat().st_size == 0:
            fails.append(f"{fname}: missing or empty")
            continue
        a = _load_th(path)
        if _check_array(fname, a, RANGES[key], fails, warns):
            data[key] = a
    if len(data) < 4 or hgrid_ll is None:
        return

    # Open-boundary node coordinates, in the order of cfg['open_boundaries']
    segs = read_open_boundaries(hgrid_ll)
    ids  = cfg.get("open_boundaries", [1, 2])
    lon  = np.concatenate([segs[i - 1][1] for i in ids])
    lat  = np.concatenate([segs[i - 1][2] for i in ids])
    nnod = data["T"].shape[1]
    if len(lon) != nnod:
        warns.append(f"open boundary node count ({len(lon)}) differs from "
                     f"the th files ({nnod}); HYCOM comparison skipped")
        return

    ts_f, ssh_f, uv_f = idir / "TS_1.nc", idir / "SSH_1.nc", idir / "UV_1.nc"
    if not (ts_f.exists() and ssh_f.exists() and uv_f.exists()):
        warns.append("HYCOM stacks missing; boundary comparison skipped")
        return
    ts  = xr.open_dataset(ts_f,  decode_times=False)
    ssh = xr.open_dataset(ssh_f, decode_times=False)
    uv  = xr.open_dataset(uv_f,  decode_times=False)
    xl  = ts["xlon"].values.astype("f8")
    if lon.min() < 0 and xl.max() > 180:          # lonshift case
        xl = np.where(xl > 180, xl - 360, xl)
    ix = _nearest(xl, lon)
    iy = _nearest(ts["ylat"].values.astype("f8"), lat)

    # day 0; the surface is the LAST vertical level of the th files
    hyc = {
        "T":   ts["temperature"].isel(time=0, depth=0).values[iy, ix],
        "S":   ts["salinity"].isel(time=0, depth=0).values[iy, ix],
        "SSH": ssh["surf_el"].isel(time=0).values[iy, ix],
    }
    hu = uv["water_u"].isel(time=0, depth=0).values[iy, ix]
    hv = uv["water_v"].isel(time=0, depth=0).values[iy, ix]
    hyc["UV"] = np.hypot(hu, hv)
    out = {
        "T":   data["T"][:, :, -1, 0],
        "S":   data["S"][:, :, -1, 0],
        "SSH": data["SSH"][:, :, 0, 0],
        "UV":  np.hypot(data["UV"][:, :, -1, 0], data["UV"][:, :, -1, 1]),
    }
    units = {"T": "degC", "S": "psu", "SSH": "m", "UV": "m/s"}
    for k in ("T", "S", "SSH", "UV"):
        d = float(np.nanmean(np.abs(out[k][0] - hyc[k])))
        print(f"  boundary day 0, mean |{k} out - HYCOM| = {d:.4f} {units[k]}")
        if k in BND_TOL and d > BND_TOL[k]:
            warns.append(f"boundary {k}: mean |out - HYCOM| = {d:.3f} "
                         f"{units[k]} (> {BND_TOL[k]})")

    # figure: surface values along the open boundary
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), constrained_layout=True)
    titles = {"T": "Surface temperature (°C)", "S": "Surface salinity (psu)",
              "SSH": "Elevation (m)", "UV": "Surface current speed (m/s)"}
    x = np.arange(nnod)
    for ax, k in zip(axes.ravel(), ("T", "S", "SSH", "UV")):
        ax.plot(x, out[k][0], "-", lw=1.2, label="SCHISM file, day 0")
        if out[k].shape[0] > 1:
            ax.plot(x, out[k][-1], "--", lw=1.0,
                    label=f"SCHISM file, day {out[k].shape[0] - 1}")
        ax.plot(x, hyc[k], ".", ms=2, color="k", label="HYCOM nearest, day 0")
        ax.set_title(titles[k], fontsize=TITLE_FS, fontweight="bold")
        ax.set_xlabel("Open boundary node index", fontsize=LABEL_FS)
        ax.tick_params(labelsize=TICK_FS)
        ax.legend(fontsize=8)
    fig.suptitle(f"Open-boundary inputs vs HYCOM — group {ym}",
                 fontsize=TITLE_FS + 2, fontweight="bold")
    _save_jpeg(fig, ddir / f"HYCOM_outputs_boundary_{ym}.jpg")
    for d in (ts, ssh, uv):
        d.close()


def _verify_nudge(cfg, idir, ddir, ym, hgrid_ll, boundaries, fails, warns):
    print("  -- nudging files (gen_nudge) --")
    cmap = cfg.get("plot_cmap", "jet")
    vals = {}
    for key, fname in (("T", "TEM_nu.nc"), ("S", "SAL_nu.nc")):
        path = idir / fname
        if not path.exists() or path.stat().st_size == 0:
            fails.append(f"{fname}: missing or empty")
            continue
        with xr.open_dataset(path, decode_times=False) as ds:
            if "tracer_concentration" not in ds:
                warns.append(f"{fname}: no tracer_concentration (surface "
                             f"restoring file?); not verified")
                continue
            a    = ds["tracer_concentration"].values.astype("f8")
            node = ds["map_to_global_node"].values
        if _check_array(fname, a, RANGES[key], fails, warns):
            vals[key] = (a[0, :, -1, 0], node)
    if len(vals) < 2 or hgrid_ll is None:
        return
    _, lons, lats, _ = read_nodes(hgrid_ll)
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), constrained_layout=True)
    for ax, (key, label) in zip(axes, (("T", "Surface temperature (°C)"),
                                       ("S", "Surface salinity (psu)"))):
        v, node = vals[key]
        _scatter_panel(ax, lons[node - 1], lats[node - 1], v,
                       f"Nudging {label.split(' (')[0].lower()}, day 0",
                       label, cmap, boundaries, (None, None))
    fig.suptitle(f"Nudging inputs — group {ym}", fontsize=TITLE_FS + 2,
                 fontweight="bold")
    _save_jpeg(fig, ddir / f"HYCOM_outputs_nudge_{ym}.jpg")


def _verify_hotstart(cfg, idir, ddir, ym, hgrid_ll, boundaries, fails, warns):
    print("  -- hotstart (gen_hotstart) --")
    path = idir / "hotstart.nc"
    if not path.exists() or path.stat().st_size == 0:
        fails.append("hotstart.nc: missing or empty")
        return
    with xr.open_dataset(path, decode_times=False) as ds:
        tr  = ds["tr_nd"].isel(nVert=-1)          # surface level, all nodes
        t   = tr.isel(ntracers=0).values.astype("f8")
        s   = tr.isel(ntracers=1).values.astype("f8")
    ok_t = _check_array("hotstart T",   t[None, :], RANGES["T"], fails, warns)
    ok_s = _check_array("hotstart S",   s[None, :], RANGES["S"], fails, warns)
    if not (ok_t and ok_s) or hgrid_ll is None:
        return
    cmap = cfg.get("plot_cmap", "jet")
    _, lons, lats, _ = read_nodes(hgrid_ll)
    step = max(1, len(lons) // 400000)           # keep the scatter light
    sl   = slice(None, None, step)
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), constrained_layout=True)
    _scatter_panel(axes[0], lons[sl], lats[sl], t[sl],
                   "Hotstart surface temperature", "°C", cmap, boundaries,
                   (None, None))
    _scatter_panel(axes[1], lons[sl], lats[sl], s[sl],
                   "Hotstart surface salinity", "psu", cmap, boundaries,
                   (None, None))
    fig.suptitle(f"Hotstart — group {ym}", fontsize=TITLE_FS + 2,
                 fontweight="bold")
    _save_jpeg(fig, ddir / f"HYCOM_outputs_hotstart_{ym}.jpg")


def verify_outputs(cfg: dict, ym: str) -> bool:
    """Verify the HYCOM-derived inputs of one group; return True on PASS.

    Checks only the steps enabled in steps.yaml (gen_3Dth, gen_nudge, and
    gen_hotstart for the first group).  Writes
    D{ID}_{group}/plot_hycom_outputs.done on PASS.
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    idir = mdir / f"I{pid}" / f"I{pid}_{ym}"
    ddir = mdir / f"D{pid}" / f"D{pid}_{ym}"
    ddir.mkdir(parents=True, exist_ok=True)
    sentinel = ddir / "plot_hycom_outputs.done"
    if sentinel.exists():
        print(f"  plot_hycom_outputs: {ym} already complete. Skipping.")
        return True

    en = lambda k: bool(cfg.get(k, False))
    first = ym == list_groups(cfg)[0]
    hgrid_ll = mdir / "fix" / "hgrid.ll"
    hgrid_ll = hgrid_ll if hgrid_ll.exists() else None
    boundaries = None
    if hgrid_ll is not None:
        try:
            boundaries = read_mesh_boundaries(hgrid_ll)
        except Exception as exc:
            print(f"  WARNING: could not read boundaries: {exc}")
    else:
        print("  WARNING: fix/hgrid.ll not found; maps and the HYCOM "
              "comparison are skipped.")

    print(f"\n--- Verifying HYCOM-derived inputs for {ym} -> {ddir} ---")
    fails, warns = [], []
    if en("gen_3Dth"):
        _verify_boundary(cfg, idir, ddir, ym, hgrid_ll, fails, warns)
    if en("gen_nudge"):
        _verify_nudge(cfg, idir, ddir, ym, hgrid_ll, boundaries, fails, warns)
    if en("gen_hotstart") and first:
        _verify_hotstart(cfg, idir, ddir, ym, hgrid_ll, boundaries,
                         fails, warns)

    lines = [f"group {ym}: {'FAIL' if fails else 'PASS'}"]
    lines += [f"FAIL: {m}" for m in fails] + [f"WARN: {m}" for m in warns]
    (ddir / "plot_hycom_outputs.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(f"  {l}" for l in lines))
    if fails:
        print(f"  Sentinel NOT written for {ym}.")
        return False
    sentinel.touch()
    print(f"  Sentinel: {sentinel}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Plot HYCOM debug GIFs for one group")
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--group", "--month", dest="month", required=True,
        help="Group ID (YYYYMM for monthly or YYYYMMDD for ndays)")
    parser.add_argument(
        "--outputs", action="store_true",
        help="Verify the gen_3Dth / gen_nudge / gen_hotstart outputs and "
             "plot them instead of drawing the HYCOM GIFs")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    if args.outputs:
        sys.exit(0 if verify_outputs(cfg, args.month) else 1)
    plot_month(cfg, args.month)
