"""
models/schism/postprocess/compare_sst.py
========================================
Phase 5 step "compare_sst" — model vs. satellite SST.

Two-stage SLURM design:
  Stage 1 (MPI parallel): mpi_frames() — one rank per day.
  Stage 2 (serial, afterok): assemble() — stitches daily frames into GIF.
"""

import argparse
import gc
import sys
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import load_config, list_months, model_dir
from workflow.models.schism.postprocess import plot_common as pc

# =============================================================================
# Module-level mesh cache — avoids scanning all run directories once per day
# in the MPI loop or legacy array path.
# =============================================================================
_mesh_cache = {}


def _get_mesh(cfg):
    """Load the mesh once per process and cache it."""
    key = cfg["project_id"]
    if key not in _mesh_cache:
        out2d0 = _find_any_out2d(cfg)
        if out2d0 is None:
            _mesh_cache[key] = (None, None, None, None, None)
        else:
            _mesh_cache[key] = pc.load_mesh(out2d0)
    return _mesh_cache[key]


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
        stacks  = pc.list_output_stacks(outputs, "out2d")
        if stacks:
            return stacks[0]
    return None

# =============================================================================
# Model SST for one day
# =============================================================================

def _model_sst_for_day(cfg, d: date):
    """Return (values, used_desc) — model surface SST for day d."""
    import numpy as np
    import xarray as xr

    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    ym    = _month_of(d)
    outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
    stacks  = pc.list_output_stacks(outputs, "temperature")
    if not stacks:
        return None, "no temperature_*.nc"

    match  = str(cfg.get("sst_match", "daily_mean")).lower()
    day0   = np.datetime64(d.isoformat())
    day1   = day0 + np.timedelta64(1, "D")
    target = np.datetime64(f"{d.isoformat()}T12")

    day_vals     = []
    nearest_val  = None
    nearest_dt   = None

    for nc in stacks:
        ds = xr.open_dataset(str(nc), drop_variables=pc.SAFE_DROP)
        if "temperature" not in ds:
            ds.close(); continue
        times  = ds["time"].values
        in_day = (times >= day0) & (times < day1)
        if not in_day.any():
            ds.close(); continue

        surf = pc.extract_layer(ds["temperature"], "surface")
        for i in np.nonzero(in_day)[0]:
            v = np.asarray(surf[i])
            if v.ndim > 1:
                v = v.ravel()
            if match == "daily_mean":
                day_vals.append(v)
            else:
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
    ds  = xr.open_dataset(str(f))
    lon = np.array(ds["lon"])
    lat = np.array(ds["lat"])
    sst = np.array(ds["sst"])
    ds.close()
    if sst.ndim == 3:
        sst = sst[0]
    lon2d, lat2d = np.meshgrid(lon, lat)
    return lon2d, lat2d, sst

# =============================================================================
# Stage 1 — MPI parallel frame generation
# =============================================================================

def _date_range(cfg) -> list:
    start = cfg.get("compare_sst_start") or cfg["start_date"]
    end   = cfg.get("compare_sst_end")   or cfg["end_date"]
    s = date.fromisoformat(str(start))
    e = date.fromisoformat(str(end))
    out = []
    d = s
    while d <= e:
        out.append(d)
        d += timedelta(days=1)
    return out


def mpi_frames(cfg):
    """MPI parallel frame generation — one rank per day."""
    from mpi4py import MPI

    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()

    if rank == 0:
        days = _date_range(cfg)
        print(f"  [rank 0] {len(days)} day(s) distributed across {size} rank(s).")
        sys.stdout.flush()
    else:
        days = None
    days = comm.bcast(days, root=0)

    if not days:
        if rank == 0:
            print("  compare_sst: empty date range.")
        comm.Barrier()
        return

    my_days = days[rank::size]
    for d in my_days:
        frame_for_day(cfg, d)

    comm.Barrier()
    if rank == 0:
        print("  compare_sst MPI frames complete.")
        (_frames_dir(cfg).parent / ".frames_done").touch()
        sys.stdout.flush()

# =============================================================================
# Stage 1 — single-day entry point
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
    out = fdir / f"sst__{d:%Y%m%d}.jpg"

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

    # Use the cached mesh (avoids re-scanning run directories each call).
    x, y, depth, triang, _is_tri = _get_mesh(cfg)
    if x is None:
        print(f"  {d:%Y%m%d}: no out2d_*.nc found. "
              f"Has the model run completed?")
        return

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

    vmin     = cfg.get("compare_sst_vmin", -2.0)
    vmax     = cfg.get("compare_sst_vmax", 12.0)
    cmap     = cfg.get("compare_sst_cmap", "jet")
    dpi      = int(cfg.get("compare_sst_dpi", 150))
    isobaths = cfg.get("isobaths", [200, 2000])
    if isobaths:
        isobaths = [float(v) for v in isobaths]

    lon_min = float(cfg["lon_min"]); lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"]); lat_max = float(cfg["lat_max"])

    lon_range = lon_max - lon_min
    lat_range = lat_max - lat_min
    panel_w   = 10.0
    panel_h   = panel_w * lat_range / lon_range
    fig_h     = panel_h * 2 + 0.8
    fig = plt.figure(figsize=(panel_w, fig_h))
    ax1 = fig.add_subplot(2, 1, 1)
    ax2 = fig.add_subplot(2, 1, 2)

    # --- model panel ---
    tp1 = ax1.tripcolor(triang, np.asarray(model_vals), shading="flat",
                        cmap=cmap, vmin=vmin, vmax=vmax, rasterized=True)
    if isobaths:
        try:
            ax1.tricontour(triang, np.asarray(depth), levels=isobaths,
                           colors="k", linewidths=0.4, alpha=0.6)
        except Exception:
            pass
    if boundaries is not None:
        for lo, la in boundaries.get("open",   []):
            ax1.plot(lo, la, color="blue",  linewidth=1.0, alpha=0.8)
        for lo, la in boundaries.get("land",   []):
            ax1.plot(lo, la, color="red",   linewidth=0.6, alpha=0.7)
        for lo, la in boundaries.get("island", []):
            ax1.plot(lo, la, color="green", linewidth=0.6, alpha=0.7)
    div1 = make_axes_locatable(ax1)
    cax1 = div1.append_axes("right", size="3%", pad=0.1)
    fig.colorbar(tp1, cax=cax1).set_label("Model SST (°C)", fontsize=9)
    ax1.set_title(f"SCHISM SST — {d:%Y-%m-%d}  ({model_desc})", fontsize=10)
    ax1.set_xlim(lon_min, lon_max); ax1.set_ylim(lat_min, lat_max)
    ax1.set_aspect("equal")

    # --- satellite panel ---
    tp2 = ax2.pcolormesh(lon2d, lat2d, sat_sst, cmap=cmap,
                         vmin=vmin, vmax=vmax, rasterized=True)
    if isobaths:
        try:
            ax2.tricontour(triang, np.asarray(depth), levels=isobaths,
                           colors="k", linewidths=0.4, alpha=0.6)
        except Exception:
            pass
    if boundaries is not None:
        for lo, la in boundaries.get("open",   []):
            ax2.plot(lo, la, color="blue",  linewidth=1.0, alpha=0.8)
        for lo, la in boundaries.get("land",   []):
            ax2.plot(lo, la, color="red",   linewidth=0.6, alpha=0.7)
        for lo, la in boundaries.get("island", []):
            ax2.plot(lo, la, color="green", linewidth=0.6, alpha=0.7)
    div2 = make_axes_locatable(ax2)
    cax2 = div2.append_axes("right", size="3%", pad=0.1)
    fig.colorbar(tp2, cax=cax2).set_label("Satellite SST (°C)", fontsize=9)
    ax2.set_title(f"LEO L3S-DY SST — {d:%Y-%m-%d}", fontsize=10)
    ax2.set_xlim(lon_min, lon_max); ax2.set_ylim(lat_min, lat_max)
    ax2.set_aspect("equal")

    fig.tight_layout(pad=0.5, h_pad=0.8)
    fig.savefig(str(out), dpi=dpi, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": 90})
    plt.close(fig)
    gc.collect()
    print(f"  {d:%Y%m%d}: frame written -> {out.name}")

# =============================================================================
# Stage 2 — assemble daily frames into a GIF
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

    sub.add_parser("mpi-frames", help="MPI parallel frame generation")

    pf = sub.add_parser("frames", help="render frame for one day (legacy)")
    pf.add_argument("--date", required=True, help="YYYYMMDD")

    sub.add_parser("assemble", help="assemble daily frames into a GIF")

    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg  = load_config(Path(args.config))

    if args.stage == "mpi-frames":
        mpi_frames(cfg)
    elif args.stage == "frames":
        d = date(int(args.date[:4]), int(args.date[4:6]), int(args.date[6:8]))
        frame_for_day(cfg, d)
    elif args.stage == "assemble":
        assemble(cfg)


if __name__ == "__main__":
    main()
