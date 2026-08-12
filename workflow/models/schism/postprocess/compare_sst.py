"""
models/schism/postprocess/compare_sst.py
========================================
Phase 5 step "compare_sst" — model vs. satellite SST.

Two-stage SLURM design (see submit_compare_sst.py), one array task per day:

  Stage 1 (parallel array, one task per day):
      For day D, compute the model SST field to compare against the daily
      satellite field:
        sst_match: daily_mean (default) -> arithmetic daily mean of SCHISM
                   surface temperature across all timesteps that fall on D
        sst_match: nearest              -> SCHISM surface temperature at the
                   timestep nearest 12:00Z on D (the satellite timestamp)
      Render a two-panel frame (top: model, bottom: satellite) with a shared
      color scale and 200/2000 m isobaths, saved to
      P{ID}/P{ID}_compare_sst/frames/sst__YYYYMMDD.jpg.

  Stage 2 (serial, afterok):
      Assemble the daily frames into one GIF over compare_sst_start/end.

The LEO L3S-DY product is a daily collated field, so daily_mean matching is
the physically appropriate default.

CLI:
    python -m workflow.models.schism.postprocess.compare_sst frames \
        --config <cfg> --date YYYYMMDD
    python -m workflow.models.schism.postprocess.compare_sst assemble \
        --config <cfg>
"""

import argparse
import gc
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import load_config, list_months, model_dir
from workflow.models.schism.postprocess import plot_common as pc


def _frames_dir(cfg) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_compare_sst" / "frames"


def _gif_dir(cfg) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_compare_sst"


def _month_of(d: date) -> str:
    return d.strftime("%Y%m")


def _find_any_out2d(cfg):
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    for ym in list_months(cfg):
        outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
        stacks = pc.list_output_stacks(outputs, "out2d")
        if stacks:
            return stacks[0]
    return None


# =============================================================================
# Model SST for one day
# =============================================================================

def _model_sst_for_day(cfg, d: date):
    """Return (values, used_desc) — the model surface SST field for day d,
    matched to the satellite according to sst_match. None if unavailable."""
    import numpy as np
    import xarray as xr

    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    ym    = _month_of(d)
    outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
    stacks  = pc.list_output_stacks(outputs, "temperature")
    if not stacks:
        return None, "no temperature_*.nc"

    match = str(cfg.get("sst_match", "daily_mean")).lower()
    day0  = np.datetime64(d.isoformat())
    day1  = day0 + np.timedelta64(1, "D")

    # Collect surface-temperature timesteps that fall on day d.
    day_vals = []
    nearest_val = None
    nearest_dt  = None
    target = np.datetime64(f"{d.isoformat()}T12")

    for nc in stacks:
        ds = xr.open_dataset(str(nc), engine="h5netcdf",
                             drop_variables=pc.SAFE_DROP)
        if "temperature" not in ds:
            ds.close(); continue
        times = ds["time"].values
        in_day = (times >= day0) & (times < day1)
        if not in_day.any():
            ds.close(); continue

        surf = pc.extract_layer(ds["temperature"], "surface")  # (time, node)
        for i in np.nonzero(in_day)[0]:
            v = np.asarray(surf[i])
            if v.ndim > 1:
                v = v.ravel()
            if match == "daily_mean":
                day_vals.append(v)
            else:  # nearest
                dt = abs(times[i] - target)
                if nearest_dt is None or dt < nearest_dt:
                    nearest_dt, nearest_val = dt, v
        ds.close()

    if match == "daily_mean":
        if not day_vals:
            return None, "no model timesteps on day"
        arr = np.nanmean(np.stack(day_vals, axis=0), axis=0)
        return arr, f"daily mean of {len(day_vals)} timestep(s)"
    else:
        if nearest_val is None:
            return None, "no model timesteps on day"
        return nearest_val, "nearest timestep to 12:00Z"


# =============================================================================
# Satellite SST for one day
# =============================================================================

def _sat_sst_for_day(cfg, d: date):
    """Return (lon2d, lat2d, sst2d) satellite field for day d, or None."""
    import numpy as np
    import xarray as xr

    mdir = model_dir(cfg)
    f = mdir / "obs" / "sst_leo" / f"leosst_{d:%Y%m%d}.nc"
    if not (f.exists() and f.stat().st_size > 0):
        return None
    ds = xr.open_dataset(str(f), engine="h5netcdf")
    lon = np.array(ds["lon"])
    lat = np.array(ds["lat"])
    sst = np.array(ds["sst"])
    ds.close()
    if sst.ndim == 3:
        sst = sst[0]
    lon2d, lat2d = np.meshgrid(lon, lat)
    return lon2d, lat2d, sst


# =============================================================================
# Stage 1 — one two-panel frame for one day
# =============================================================================

def frame_for_day(cfg, d: date):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.axes_grid1 import make_axes_locatable
    from workflow.core.plot_style import read_mesh_boundaries

    fdir = _frames_dir(cfg)
    fdir.mkdir(parents=True, exist_ok=True)
    out  = fdir / f"sst__{d:%Y%m%d}.jpg"

    model_vals, model_desc = _model_sst_for_day(cfg, d)
    sat = _sat_sst_for_day(cfg, d)

    if model_vals is None and sat is None:
        print(f"  {d:%Y%m%d}: no model AND no satellite data, skipping.")
        return
    if model_vals is None:
        print(f"  {d:%Y%m%d}: no model data ({model_desc}), skipping.")
        return
    if sat is None:
        print(f"  {d:%Y%m%d}: no satellite data, skipping.")
        return

    # Mesh + isobaths + boundaries
    out2d0 = _find_any_out2d(cfg)
    if out2d0 is None:
        print(f"  {d:%Y%m%d}: no out2d_*.nc found in any run directory. "
              f"Has the model run completed?")
        return
    x, y, depth, triang = pc.load_mesh(out2d0)
    boundaries = None
    for hp in (model_dir(cfg) / "fix" / "hgrid.ll",
               model_dir(cfg) / "fix" / "hgrid.gr3"):
        if hp.exists():
            try:
                boundaries = read_mesh_boundaries(hp)
            except Exception:
                boundaries = None
            break

    lon2d, lat2d, sat_sst = sat

    vmin = cfg.get("compare_sst_vmin", -2.0)
    vmax = cfg.get("compare_sst_vmax", 12.0)
    cmap = cfg.get("compare_sst_cmap", "jet")
    dpi  = int(cfg.get("compare_sst_dpi", 150))
    isobaths = cfg.get("isobaths", [200, 2000])
    if isobaths:
        isobaths = [float(v) for v in isobaths]

    lon_min = float(cfg["lon_min"]); lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"]); lat_max = float(cfg["lat_max"])

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(6, 10),
                                   constrained_layout=True)

    # --- model panel ---
    tp1 = ax1.tripcolor(triang, np.asarray(model_vals), shading="flat",
                        cmap=cmap, vmin=vmin, vmax=vmax, rasterized=True)
    if isobaths:
        try:
            ax1.tricontour(triang, np.asarray(depth), levels=isobaths,
                           colors="k", linewidths=0.4, alpha=0.6)
        except Exception:
            pass
    div1 = make_axes_locatable(ax1)
    cax1 = div1.append_axes("right", size="4%", pad=0.15)
    fig.colorbar(tp1, cax=cax1).set_label("Model SST (°C)")
    ax1.set_title(f"SCHISM SST — {d:%Y-%m-%d}  ({model_desc})", fontsize=10)
    ax1.set_xlim(lon_min, lon_max); ax1.set_ylim(lat_min, lat_max)
    ax1.set_aspect("equal")

    # --- satellite panel ---
    tp2 = ax2.pcolormesh(lon2d, lat2d, sat_sst, cmap=cmap,
                         vmin=vmin, vmax=vmax, rasterized=True)
    div2 = make_axes_locatable(ax2)
    cax2 = div2.append_axes("right", size="4%", pad=0.15)
    fig.colorbar(tp2, cax=cax2).set_label("Satellite SST (°C)")
    ax2.set_title(f"LEO L3S-DY SST — {d:%Y-%m-%d}", fontsize=10)
    ax2.set_xlim(lon_min, lon_max); ax2.set_ylim(lat_min, lat_max)
    ax2.set_aspect("equal")

    fig.savefig(str(out), dpi=dpi, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": 90})
    plt.close(fig)
    gc.collect()
    print(f"  {d:%Y%m%d}: frame written -> {out.name}")


# =============================================================================
# Stage 2 — assemble the daily frames into a GIF
# =============================================================================

def assemble(cfg):
    fdir = _frames_dir(cfg)
    gdir = _gif_dir(cfg)
    gdir.mkdir(parents=True, exist_ok=True)

    fps         = int(cfg.get("compare_sst_fps", 4))
    keep_frames = bool(cfg.get("keep_frames", True))

    frames = sorted(fdir.glob("sst__*.jpg"))
    if not frames:
        print("  compare_sst: no frames found, no GIF produced.")
    else:
        pc.assemble_gif(frames, gdir / "compare_sst.gif",
                        fps=fps, keep_frames=keep_frames)
        if not keep_frames:
            try:
                fdir.rmdir()
            except OSError:
                pass

    (gdir / "compare_sst.done").touch()
    print(f"  compare_sst assembly complete -> {gdir}")


# =============================================================================
# CLI
# =============================================================================

def main():
    ap  = argparse.ArgumentParser(description="Model vs satellite SST")
    sub = ap.add_subparsers(dest="stage", required=True)

    pf = sub.add_parser("frames")
    pf.add_argument("--config", required=True)
    pf.add_argument("--date",   required=True, help="YYYYMMDD")

    pa = sub.add_parser("assemble")
    pa.add_argument("--config", required=True)

    args = ap.parse_args()
    cfg = load_config(Path(args.config))

    if args.stage == "frames":
        d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
        frame_for_day(cfg, d)
    elif args.stage == "assemble":
        assemble(cfg)


if __name__ == "__main__":
    main()
