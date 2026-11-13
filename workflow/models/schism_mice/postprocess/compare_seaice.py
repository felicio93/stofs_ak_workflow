"""
models/schism_mice/postprocess/compare_seaice.py
================================================
Phase 5 step "compare_seaice" — model vs. NOAA/NSIDC sea-ice concentration.

The model's daily-mean ice concentration (iceTracer_2 in out2d_*.nc) is
averaged onto the 25 km NSIDC polar stereographic cells (each node weighted
by its area) and compared on the cells that are NSIDC ocean
(surface_type_mask = 50), have a valid observation and are covered by the
mesh for at least compare_seaice_min_coverage of their area (default 0.5).

Per day (Stage 1, SLURM array, one task per few days):
    frames/seaice__<YYYYMMDD>.jpg       model (on its mesh) above NSIDC (each
                                        25 km cell in lon/lat), in the
                                        compare_sst layout
    frames_diff/seaice_diff__<YYYYMMDD>.jpg
                                        model - NSIDC, drawn on the mesh (each
                                        node shows its 25 km cell) with the
                                        plot_outputs frame style
    stats/seaice__<YYYYMMDD>.json       statistics below
Then (Stage 2, after Stage 1): compare_seaice.gif, compare_seaice_diff.gif,
compare_seaice_stats.csv and compare_seaice_timeseries.png in
P{ID}/P{ID}_compare_seaice/.

Statistics (on the compared cells; areas use the true cell area):
    extent      area of cells with concentration >= 0.15   [km2]
    ice area    sum of concentration x cell area            [km2]
    IIEE        integrated ice-edge error: area where model and NSIDC
                disagree on ice (>= 0.15) / no ice, = over + under   [km2]
    bias, RMSE  model - NSIDC concentration on the cells where either has
                at least 15 % ice
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from workflow.core.config import (
    group_date_range, list_groups, load_config, model_dir,
)
from workflow.models.schism.postprocess import plot_common as pc
from workflow.models.schism_mice.postprocess.download_seaice import (
    local_file,
)

ICE_EDGE = 0.15
NSIDC_PROJ4 = ("+proj=stere +lat_0=90 +lat_ts=70 +lon_0=-45 +k=1 +x_0=0 "
               "+y_0=0 +a=6378273 +b=6356889.449 +units=m +no_defs")


def _out_dir(cfg) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_compare_seaice"


def _group_for_day(cfg, d: date):
    for gid in list_groups(cfg):
        gstart, gend = group_date_range(cfg, gid)
        if gstart <= d <= gend:
            return gid
    return None


def _outputs(cfg, gid) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"R{pid}" / f"R{pid}_{gid}" / "outputs"


# =============================================================================
# Observation
# =============================================================================

def read_obs(path: Path) -> dict:
    """NSIDC day: conc (ny, nx; NaN = no valid value), ocean mask, cell
    centre x (nx) / y (ny) in m, proj4 string."""
    import numpy as np
    import netCDF4
    with netCDF4.Dataset(str(path)) as ds:
        v = ds["cdr_seaice_conc"]
        v.set_auto_maskandscale(True)
        conc = np.ma.filled(v[0].astype("f8"), np.nan)
        mask = np.asarray(ds.groups["cdr_supplementary"]["surface_type_mask"][:])
        if mask.ndim == 3:
            mask = mask[0]
        x = np.asarray(ds["x"][:], dtype="f8")
        y = np.asarray(ds["y"][:], dtype="f8")
        proj4 = NSIDC_PROJ4
        if "crs" in ds.variables and "proj4text" in ds["crs"].ncattrs():
            proj4 = str(ds["crs"].getncattr("proj4text"))
    conc[(conc < 0) | (conc > 1)] = np.nan
    return {"conc": conc, "ocean": mask == 50, "x": x, "y": y, "proj4": proj4}


# =============================================================================
# Model -> NSIDC cells (cached per project)
# =============================================================================

def _node_areas(lon, lat, tris):
    """Node areas [m2]: one third of each touching triangle, on a sphere."""
    import numpy as np
    R = 6371000.0
    lo, la = np.deg2rad(lon[tris]), np.deg2rad(lat[tris])
    clat = np.cos(la.mean(axis=1))
    dlo = (lo - lo[:, :1] + np.pi) % (2 * np.pi) - np.pi     # dateline-safe
    xe, ye = R * dlo * clat[:, None], R * (la - la[:, :1])
    area = 0.5 * np.abs((xe[:, 1] - xe[:, 0]) * (ye[:, 2] - ye[:, 0])
                        - (xe[:, 2] - xe[:, 0]) * (ye[:, 1] - ye[:, 0]))
    out = np.zeros(lon.size)
    for k in range(3):
        np.add.at(out, tris[:, k], area / 3.0)
    return out


def mesh_bins(cfg, obs: dict) -> dict:
    """{'cell': NSIDC cell index of every node (-1 outside the grid),
    'node_area': m2, 'cell_area': true area of every cell (ny, nx) m2,
    'coverage': mesh area / cell area (ny, nx)}.
    Cached in P{ID}_compare_seaice/mesh_bins.npz."""
    import numpy as np
    cache = _out_dir(cfg) / "mesh_bins.npz"
    ny, nx = obs["conc"].shape
    if cache.exists():
        z = np.load(cache)
        if z["cell_area"].shape == (ny, nx):
            return {k: z[k] for k in z.files}

    from pyproj import Proj, Transformer
    gid = list_groups(cfg)[0]
    stacks = pc.list_output_stacks(_outputs(cfg, gid), "out2d")
    if not stacks:
        raise SystemExit(f"ERROR: no out2d_*.nc in {_outputs(cfg, gid)}")
    lon, lat, _dep, triang, _ = pc.load_mesh(stacks[0])
    node_area = _node_areas(lon, lat, triang.triangles)

    tr = Transformer.from_crs("EPSG:4326", obs["proj4"], always_xy=True)
    lon180 = (lon + 180.0) % 360.0 - 180.0
    px, py = tr.transform(lon180, lat)
    dx = obs["x"][1] - obs["x"][0]
    dy = obs["y"][1] - obs["y"][0]                    # negative (north up)
    col = np.floor((px - (obs["x"][0] - dx / 2)) / dx).astype(np.int64)
    row = np.floor((py - (obs["y"][0] - dy / 2)) / dy).astype(np.int64)
    inside = (col >= 0) & (col < nx) & (row >= 0) & (row < ny)
    cell = np.where(inside, row * nx + col, -1)

    # true cell areas from the projection's areal scale at cell centres
    X, Y = np.meshgrid(obs["x"], obs["y"])
    clon, clat = Transformer.from_crs(obs["proj4"], "EPSG:4326",
                                      always_xy=True).transform(X, Y)
    fac = Proj(obs["proj4"]).get_factors(clon.ravel(), clat.ravel())
    cell_area = (abs(dx * dy) / np.asarray(fac.areal_scale)).reshape(ny, nx)

    covered = np.bincount(cell[inside], weights=node_area[inside],
                          minlength=ny * nx).reshape(ny, nx)
    out = {"cell": cell.astype(np.int64), "node_area": node_area,
           "cell_area": cell_area, "coverage": covered / cell_area}


    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(f"mesh_bins.{np.random.randint(1 << 30)}.tmp.npz")
    np.savez(tmp, **out)
    tmp.replace(cache)
    return out


def model_conc_for_day(cfg, d: date):
    """Daily mean model ice concentration at the nodes, or (None, reason)."""
    import numpy as np
    import xarray as xr
    gid = _group_for_day(cfg, d)
    if gid is None:
        return None, "no run group for this day"
    stacks = pc.list_output_stacks(_outputs(cfg, gid), "out2d")
    if not stacks:
        return None, f"no out2d_*.nc for group {gid}"
    day0 = np.datetime64(d.isoformat())
    day1 = day0 + np.timedelta64(1, "D")
    total, n = None, 0
    for nc in stacks:
        with xr.open_dataset(str(nc), drop_variables=pc.SAFE_DROP) as ds:
            if "iceTracer_2" not in ds:
                return None, f"{nc.name} has no iceTracer_2 (iof_mice(7) = 1?)"
            t = ds["time"].values
            sel = (t >= day0) & (t < day1)
            if not sel.any():
                continue
            a = np.asarray(ds["iceTracer_2"].values[sel], dtype="f8")
            s = np.nansum(a, axis=0)
            total = s if total is None else total + s
            n += int(sel.sum())
    if n == 0:
        return None, "no model records on this day"
    return total / n, f"daily mean of {n} record(s)"


# =============================================================================
# One day
# =============================================================================

def compare_day(cfg, d: date) -> bool:
    import numpy as np
    obs_file = local_file(cfg, d)
    if obs_file is None:
        print(f"  {d}: no NSIDC file (download_seaice), skipping.")
        return False
    model, why = model_conc_for_day(cfg, d)
    if model is None:
        print(f"  {d}: no model ice ({why}), skipping.")
        return False
    obs = read_obs(obs_file)
    bins = mesh_bins(cfg, obs)
    ny, nx = obs["conc"].shape
    cell, w = bins["cell"], bins["node_area"]
    ok = (cell >= 0) & np.isfinite(model)
    num = np.bincount(cell[ok], weights=(w * model)[ok], minlength=ny * nx)
    den = np.bincount(cell[ok], weights=w[ok], minlength=ny * nx)
    with np.errstate(invalid="ignore", divide="ignore"):
        mod = (num / den).reshape(ny, nx)
    min_cov = float(cfg.get("compare_seaice_min_coverage", 0.5))
    valid = (obs["ocean"] & np.isfinite(obs["conc"]) & np.isfinite(mod)
             & (bins["coverage"] >= min_cov))
    if not valid.any():
        print(f"  {d}: no NSIDC ocean cell covered by the mesh, skipping.")
        return False

    A = bins["cell_area"] / 1e6                                  # km2
    o, m = obs["conc"], mod
    mi, oi = (m >= ICE_EDGE) & valid, (o >= ICE_EDGE) & valid
    zone = mi | oi
    diff = (m - o)[zone]
    st = {
        "date": d.isoformat(),
        "obs_file": obs_file.name,
        "model": why,
        "cells_compared": int(valid.sum()),
        "extent_model_km2": float(A[mi].sum()),
        "extent_obs_km2": float(A[oi].sum()),
        "area_model_km2": float((A * m)[valid].sum()),
        "area_obs_km2": float((A * o)[valid].sum()),
        "iiee_km2": float(A[mi != oi].sum()),
        "over_km2": float(A[mi & ~oi].sum()),
        "under_km2": float(A[oi & ~mi].sum()),
        "bias": float(diff.mean()) if diff.size else float("nan"),
        "rmse": float(np.sqrt((diff ** 2).mean())) if diff.size else float("nan"),
        "cells_ice_zone": int(zone.sum()),
    }
    sdir = _out_dir(cfg) / "stats"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"seaice__{d:%Y%m%d}.json").write_text(json.dumps(st, indent=1))
    _plot_pair(cfg, d, model, why, obs, obs_file)
    cdiff = np.where(valid, m - o, np.nan).ravel()
    node_diff = np.where(cell >= 0, cdiff[np.clip(cell, 0, None)], np.nan)
    _plot_diff(cfg, d, node_diff, st)
    print(f"  {d}: extent model {st['extent_model_km2']:.0f} / NSIDC "
          f"{st['extent_obs_km2']:.0f} km2, IIEE {st['iiee_km2']:.0f} km2, "
          f"bias {st['bias']:+.3f}, RMSE {st['rmse']:.3f}")
    return True


_MESH = {}


def _mesh(cfg):
    """(x, y, depth, triangulation, is_tri, boundaries) of the run's mesh,
    loaded once per process."""
    key = cfg["project_id"]
    if key not in _MESH:
        stacks = pc.list_output_stacks(_outputs(cfg, list_groups(cfg)[0]),
                                       "out2d")
        x, y, depth, triang, is_tri = pc.load_mesh(stacks[0])
        boundaries = None
        for hp in (model_dir(cfg) / "fix" / "hgrid.ll",
                   model_dir(cfg) / "fix" / "hgrid.gr3"):
            if hp.exists():
                try:
                    from workflow.core.plot_style import read_mesh_boundaries
                    boundaries = read_mesh_boundaries(hp)
                except Exception:
                    boundaries = None
                break
        _MESH[key] = (x, y, depth, triang, is_tri, boundaries)
    return _MESH[key]


def _obs_lonlat(cfg, obs):
    """Cell-corner lon/lat (ny+1, nx+1) of the NSIDC grid in the domain's
    lon_reference, and a (ny, nx) mask of the cells to draw (inside the
    domain box, not split by the lon seam)."""
    import numpy as np
    from pyproj import Transformer
    x, y = obs["x"], obs["y"]
    dx, dy = x[1] - x[0], y[1] - y[0]
    xe = np.append(x - dx / 2, x[-1] + dx / 2)
    ye = np.append(y - dy / 2, y[-1] + dy / 2)
    inv = Transformer.from_crs(obs["proj4"], "EPSG:4326", always_xy=True)
    lon_c, lat_c = inv.transform(*np.meshgrid(xe, ye))
    lon_m, lat_m = inv.transform(*np.meshgrid(x, y))
    if str(cfg.get("lon_reference", "360")) == "360":
        lon_c, lon_m = lon_c % 360.0, lon_m % 360.0
    else:
        lon_c = (lon_c + 180.0) % 360.0 - 180.0
        lon_m = (lon_m + 180.0) % 360.0 - 180.0
    corners = np.stack([lon_c[:-1, :-1], lon_c[:-1, 1:],
                        lon_c[1:, :-1], lon_c[1:, 1:]])
    seam = corners.max(axis=0) - corners.min(axis=0) > 10.0
    box = ((lon_m >= float(cfg["lon_min"]) - 2) & (lon_m <= float(cfg["lon_max"]) + 2)
           & (lat_m >= float(cfg["lat_min"]) - 2) & (lat_m <= float(cfg["lat_max"]) + 2))
    return lon_c, lat_c, box & ~seam


def _plot_pair(cfg, d, model_nodes, model_desc, obs, obs_file):
    """Model above NSIDC, in the compare_sst layout (panel size, isobaths,
    boundary colours, colorbars, titles)."""
    import gc
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.axes_grid1 import make_axes_locatable

    x, y, depth, triang, _is_tri, boundaries = _mesh(cfg)
    lon_c, lat_c, draw = _obs_lonlat(cfg, obs)
    ob = np.ma.masked_where(~(draw & obs["ocean"] & np.isfinite(obs["conc"])),
                            obs["conc"])

    vmin, vmax = 0.0, 1.0
    cmap     = cfg.get("compare_seaice_cmap", "Blues_r")
    dpi      = int(cfg.get("compare_seaice_dpi", 150))
    isobaths = cfg.get("isobaths", [200, 2000])
    if isobaths:
        isobaths = [float(v) for v in isobaths]
    lon_min, lon_max = float(cfg["lon_min"]), float(cfg["lon_max"])
    lat_min, lat_max = float(cfg["lat_min"]), float(cfg["lat_max"])

    panel_w = 10.0
    panel_h = panel_w * (lat_max - lat_min) / (lon_max - lon_min)
    fig = plt.figure(figsize=(panel_w, panel_h * 2 + 0.8))
    ax1 = fig.add_subplot(2, 1, 1)
    ax2 = fig.add_subplot(2, 1, 2)

    tp1 = ax1.tripcolor(triang, np.asarray(model_nodes), shading="flat",
                        cmap=cmap, vmin=vmin, vmax=vmax, rasterized=True)
    tp2 = ax2.pcolormesh(lon_c, lat_c, ob, cmap=cmap, vmin=vmin, vmax=vmax,
                         rasterized=True)
    titles = (f"SCHISM sea-ice concentration — {d:%Y-%m-%d}  ({model_desc})",
              f"NOAA/NSIDC CDR sea-ice concentration (25 km) — {d:%Y-%m-%d}"
              f"  ({obs_file.name.split('_')[-1].replace('.nc', '')})")
    labels = ("Model ice concentration", "NSIDC ice concentration")
    for ax, tp, title, label in ((ax1, tp1, titles[0], labels[0]),
                                 (ax2, tp2, titles[1], labels[1])):
        if isobaths:
            try:
                ax.tricontour(triang, np.asarray(depth), levels=isobaths,
                              colors="k", linewidths=0.4, alpha=0.6)
            except Exception:
                pass
        if boundaries is not None:
            for lo, la in boundaries.get("open", []):
                ax.plot(lo, la, color="blue", linewidth=1.0, alpha=0.8)
            for lo, la in boundaries.get("land", []):
                ax.plot(lo, la, color="red", linewidth=0.6, alpha=0.7)
            for lo, la in boundaries.get("island", []):
                ax.plot(lo, la, color="green", linewidth=0.6, alpha=0.7)
        cax = make_axes_locatable(ax).append_axes("right", size="3%", pad=0.1)
        fig.colorbar(tp, cax=cax).set_label(label, fontsize=9)
        ax.set_title(title, fontsize=10)
        ax.set_xlim(lon_min, lon_max)
        ax.set_ylim(lat_min, lat_max)
        ax.set_aspect("equal")

    fig.tight_layout(pad=0.5, h_pad=0.8)
    fdir = _out_dir(cfg) / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(fdir / f"seaice__{d:%Y%m%d}.jpg"), dpi=dpi,
                format="jpeg", bbox_inches="tight", pil_kwargs={"quality": 90})
    plt.close(fig)
    gc.collect()


def _plot_diff(cfg, d, node_diff, st):
    """Model - NSIDC on the mesh, with the plot_outputs frame style."""
    x, y, depth, triang, _is_tri, boundaries = _mesh(cfg)
    isobaths = cfg.get("isobaths", [200, 2000])
    if isobaths:
        isobaths = [float(v) for v in isobaths]
    fdir = _out_dir(cfg) / "frames_diff"
    fdir.mkdir(parents=True, exist_ok=True)
    title = (f"Ice concentration, model − NSIDC — {d:%Y-%m-%d}\n"
             f"bias {st['bias']:+.2f}  RMSE {st['rmse']:.2f}  IIEE "
             f"{st['iiee_km2']/1e3:.0f}k km²  (cells with ≥ 15 % ice)")
    pc.render_frame(
        triang, node_diff, title=title,
        out_path=fdir / f"seaice_diff__{d:%Y%m%d}.jpg",
        cbar_label="Model − NSIDC (25 km cells)", cmap="RdBu_r",
        vmin=-1.0, vmax=1.0, depth=depth, isobaths=isobaths,
        dpi=int(cfg.get("plot_outputs_dpi", 150)), boundaries=boundaries,
        padding=pc.plot_padding(cfg))


# =============================================================================
# Stage 2: GIF, CSV, time series
# =============================================================================

def assemble(cfg):
    import csv
    odir = _out_dir(cfg)
    stats = [json.loads(p.read_text())
             for p in sorted((odir / "stats").glob("seaice__*.json"))]
    if not stats:
        print("  compare_seaice: no daily statistics found; nothing to "
              "assemble.")
        return
    with open(odir / "compare_seaice_stats.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(stats[0]))
        wr.writeheader()
        wr.writerows(stats)
    _plot_timeseries(cfg, stats, odir / "compare_seaice_timeseries.png")
    fps  = int(cfg.get("compare_seaice_fps", 4))
    keep = bool(cfg.get("keep_frames", True))
    pc.assemble_gif(sorted((odir / "frames").glob("seaice__*.jpg")),
                    odir / "compare_seaice.gif", fps=fps, keep_frames=keep)
    pc.assemble_gif(sorted((odir / "frames_diff").glob("seaice_diff__*.jpg")),
                    odir / "compare_seaice_diff.gif", fps=fps,
                    keep_frames=keep)
    (odir / "compare_seaice.done").touch()
    print(f"  compare_seaice: {len(stats)} day(s) -> {odir}")


def _plot_timeseries(cfg, stats, out):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    t = np.array([np.datetime64(s["date"]) for s in stats])
    g = lambda k: np.array([s[k] for s in stats], dtype="f8")
    fig, ax = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    ax[0].plot(t, g("extent_model_km2") / 1e3, "r-", label="extent, model")
    ax[0].plot(t, g("extent_obs_km2") / 1e3, "k--", label="extent, NSIDC")
    ax[0].plot(t, g("area_model_km2") / 1e3, "r:", label="ice area, model")
    ax[0].plot(t, g("area_obs_km2") / 1e3, "k:", label="ice area, NSIDC")
    ax[0].set_ylabel("10³ km²"); ax[0].legend(fontsize=8)
    ax[1].plot(t, g("iiee_km2") / 1e3, "k-", label="IIEE")
    ax[1].plot(t, g("over_km2") / 1e3, "r-", lw=0.8, label="model ice, NSIDC none")
    ax[1].plot(t, g("under_km2") / 1e3, "b-", lw=0.8, label="NSIDC ice, model none")
    ax[1].set_ylabel("10³ km²"); ax[1].legend(fontsize=8)
    ax[2].plot(t, g("bias"), "k-", label="bias (model - NSIDC)")
    ax[2].plot(t, g("rmse"), "m-", label="RMSE")
    ax[2].axhline(0, color="0.6", lw=0.5)
    ax[2].set_ylabel("concentration"); ax[2].legend(fontsize=8)
    loc = mdates.AutoDateLocator()
    ax[2].xaxis.set_major_locator(loc)
    ax[2].xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    fig.suptitle(f"M{cfg['project_id']} sea ice vs NOAA/NSIDC CDR (25 km), on "
                 f"NSIDC ocean cells covered by the mesh", fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)


# =============================================================================
# CLI
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description="Model vs NSIDC sea-ice "
                                             "concentration")
    ap.add_argument("--config", required=True)
    sub = ap.add_subparsers(dest="stage", required=True)
    pf = sub.add_parser("frames", help="compare one day")
    pf.add_argument("--date", required=True, help="YYYYMMDD")
    sub.add_parser("assemble", help="GIF, CSV and time series")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    if args.stage == "frames":
        s = args.date
        compare_day(cfg, date(int(s[:4]), int(s[4:6]), int(s[6:8])))
    else:
        assemble(cfg)


if __name__ == "__main__":
    main()
