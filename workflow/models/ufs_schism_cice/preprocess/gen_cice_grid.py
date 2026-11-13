"""
models/ufs_schism_cice/preprocess/gen_cice_grid.py
==================================================
CICE regional grid made from the SCHISM mesh (once per project, --phase setup).

  fix/hgrid.ll  ->  fix/cice/grid_cice.nc    tlon tlat ulon ulat htn hte huw hus
                                             angle anglet (grid_format 'nc')
                    fix/cice/kmt_cice.nc     kmt: 1 ocean, 0 land
                    fix/cice/bathy_cice.nc   Bathymetry (m, positive down),
                                             TLAT, TLON (seabed stress)
                    fix/cice/mesh_cice.nc    ESMF unstructured mesh of the
                                             same cells (mesh_ice), with
                                             elementMask = kmt
                    fix/cice/cice_grid.png   kmt and bathymetry, for a look

The grid is a regular lon/lat grid over the mesh's bounding box, the kind used
by the UFS-Coastal Bering Sea case (Joseph Smith's generate_cice_grid_coastal.m,
Scott Durski's runs): the spacing is `resolution_km` in latitude, and in
longitude at the middle latitude of the domain. The conventions are those of
the Bering files (grid_reg_BER.nc): coordinates in radians, lengths in cm,
angle 0, kmt stored as double; the U point is the NE corner of the T cell.

kmt: a cell is ocean when at least `min_coverage` of it lies inside the
SCHISM mesh (sampled on `samples` x `samples` points per cell), so that CICE
only has cells where SCHISM can give it SST, currents and the freezing
potential.  Bathymetry is the hgrid depth interpolated to the cell centres.

Settings: the `cice_grid:` block of modelsetup.yaml (all optional):
  resolution_km : 4.0      cell size (km)
  margin_cells  : 0        cells added around the mesh bounding box
  min_coverage  : 0.5      fraction of a cell inside the mesh to be ocean
  samples       : 4        points per cell edge for the coverage
  min_depth     : 2.0      shallower ocean cells get this depth (m) in
                           the bathymetry file

Files already in fix/cice/ are kept (move them aside to remake them).
The work runs as one SLURM job (the mesh search needs memory).

Sentinel: fix/cice/gen_cice_grid.done
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from workflow.core.config import load_config, model_dir

R_EARTH = 6371220.0          # CICE radius (m), icepack_parameters radius
PRODUCTS = ("grid_cice.nc", "kmt_cice.nc", "bathy_cice.nc", "mesh_cice.nc")
DEFAULTS = {"resolution_km": 4.0, "margin_cells": 0, "min_coverage": 0.5,
            "samples": 4, "min_depth": 2.0}


def settings(cfg: dict) -> dict:
    s = dict(DEFAULTS)
    s.update(cfg.get("cice_grid") or {})
    return s


def cice_dir(cfg: dict) -> Path:
    return model_dir(cfg) / "fix" / "cice"


# =============================================================================
# Mesh
# =============================================================================

def read_hgrid(path: Path):
    """lon, lat, depth (np,), triangles (nt, 3) 0-based (quads split)."""
    import numpy as np
    with open(path) as f:
        f.readline()
        ne, np_ = (int(v) for v in f.readline().split()[:2])
        nodes = np.loadtxt(f, max_rows=np_, usecols=(1, 2, 3))
        tris = []
        for _ in range(ne):
            p = f.readline().split()
            n = int(p[1])
            v = [int(x) - 1 for x in p[2:2 + n]]
            tris.append(v[:3])
            if n == 4:
                tris.append([v[0], v[2], v[3]])
    lon, lat, dep = nodes[:, 0].copy(), nodes[:, 1], nodes[:, 2]
    if lon.max() - lon.min() > 180.0:        # crosses the dateline
        lon = lon % 360.0
    return lon, lat, dep, np.asarray(tris, dtype=np.int64)


# =============================================================================
# Grid
# =============================================================================

def make_grid(lon, lat, s: dict) -> dict:
    """Regular lon/lat grid over the mesh bounding box (degrees)."""
    import numpy as np
    km_per_deg = np.pi * R_EARTH / 180.0 / 1000.0
    dlat = float(s["resolution_km"]) / km_per_deg
    m = int(s["margin_cells"])
    lat0, lat1 = lat.min(), lat.max()
    dlon = dlat / np.cos(np.deg2rad(0.5 * (lat0 + lat1)))
    lon0, lon1 = lon.min(), lon.max()
    nx = int(np.ceil((lon1 - lon0) / dlon)) + 2 * m
    ny = int(np.ceil((lat1 - lat0) / dlat)) + 2 * m
    lon0 -= m * dlon
    lat0 -= m * dlat
    if lat0 + ny * dlat > 89.0:
        raise SystemExit("ERROR: the CICE grid would reach the pole; a "
                         "regular lon/lat grid cannot be used there.")
    return {"lon0": lon0, "lat0": lat0, "dlon": dlon, "dlat": dlat,
            "nx": nx, "ny": ny}


def grid_fields(g: dict) -> dict:
    """CICE grid variables (ny, nx): radians and cm, as in grid_reg_BER.nc."""
    import numpy as np
    nx, ny = g["nx"], g["ny"]
    i = np.arange(nx)
    j = np.arange(ny)
    tlon = g["lon0"] + (i + 0.5) * g["dlon"]
    tlat = g["lat0"] + (j + 0.5) * g["dlat"]
    ulon = g["lon0"] + (i + 1.0) * g["dlon"]
    ulat = g["lat0"] + (j + 1.0) * g["dlat"]
    TLON, TLAT = np.meshgrid(tlon, tlat)
    ULON, ULAT = np.meshgrid(ulon, ulat)
    rad = np.deg2rad
    dx = lambda latd: R_EARTH * rad(g["dlon"]) * np.cos(rad(latd)) * 100.0
    dy = R_EARTH * rad(g["dlat"]) * 100.0
    # htn: north face of the T cell (at the U latitude); hus: south face of
    # the U cell (at the T latitude); hte/huw: east/west faces (meridional)
    htn = dx(ULAT)
    hus = dx(TLAT)
    hte = np.full((ny, nx), dy)
    huw = np.full((ny, nx), dy)
    zero = np.zeros((ny, nx))
    return {"tlon": rad(TLON), "tlat": rad(TLAT), "ulon": rad(ULON),
            "ulat": rad(ULAT), "htn": htn, "hte": hte, "huw": huw,
            "hus": hus, "angle": zero, "anglet": zero}


def coverage_and_depth(lon, lat, dep, tris, g: dict, samples: int):
    """Fraction of each cell inside the mesh, and the mean hgrid depth over
    the sample points inside the mesh (NaN where none is)."""
    import numpy as np
    from matplotlib.tri import Triangulation, LinearTriInterpolator
    nx, ny, k = g["nx"], g["ny"], int(samples)
    print(f"  Triangulation: {lon.size} nodes, {len(tris)} triangles ...",
          flush=True)
    tri = Triangulation(lon, lat, tris)
    finder = tri.get_trifinder()
    interp = LinearTriInterpolator(tri, dep, trifinder=finder)
    off = (np.arange(k) + 0.5) / k
    inside = np.zeros((ny, nx))
    dsum = np.zeros((ny, nx))
    for ia, a in enumerate(off):
        for b in off:
            px = g["lon0"] + (np.arange(nx) + b) * g["dlon"]
            py = g["lat0"] + (np.arange(ny) + a) * g["dlat"]
            X, Y = np.meshgrid(px, py)
            d = interp(X, Y)
            ok = ~np.ma.getmaskarray(d)
            inside += ok
            dsum += np.where(ok, np.ma.filled(d, 0.0), 0.0)
        print(f"    coverage: sub-row {ia + 1}/{k} done", flush=True)
    cov = inside / (k * k)
    with np.errstate(invalid="ignore", divide="ignore"):
        depth = np.where(inside > 0, dsum / inside, np.nan)
    return cov, depth


# =============================================================================
# Writers
# =============================================================================

def _stamp(nc, what):
    nc.title = f"CICE {what} made from the SCHISM mesh by stofs_ak_workflow"
    nc.history = ("Created " + datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC") + " by workflow.models.ufs_schism_cice."
        "preprocess.gen_cice_grid")


def write_grid(path: Path, f: dict, g: dict):
    import netCDF4 as nc4
    units = {"tlon": "radians", "tlat": "radians", "ulon": "radians",
             "ulat": "radians", "htn": "cm", "hte": "cm", "huw": "cm",
             "hus": "cm", "angle": "radians", "anglet": "radians"}
    with nc4.Dataset(str(path), "w") as nc:
        nc.createDimension("y", g["ny"])
        nc.createDimension("x", g["nx"])
        for k, v in f.items():
            var = nc.createVariable(k, "f8", ("y", "x"))
            var.units = units[k]
            var[:] = v
        _stamp(nc, "grid")
        nc.resolution = (f"dlon {g['dlon']:.8f} deg, dlat {g['dlat']:.8f} "
                         f"deg")


def write_kmt(path: Path, kmt, g: dict):
    import netCDF4 as nc4
    with nc4.Dataset(str(path), "w") as nc:
        nc.createDimension("y", g["ny"])
        nc.createDimension("x", g["nx"])
        v = nc.createVariable("kmt", "f8", ("y", "x"))
        v.long_name = "ocean mask (1 ocean, 0 land)"
        v[:] = kmt
        _stamp(nc, "land mask")


def write_bathy(path: Path, depth, f: dict, g: dict):
    import numpy as np
    import netCDF4 as nc4
    with nc4.Dataset(str(path), "w") as nc:
        nc.createDimension("y", g["ny"])
        nc.createDimension("x", g["nx"])
        v = nc.createVariable("Bathymetry", "f8", ("y", "x"))
        v.units = "m"
        v.long_name = "ocean depth (positive down)"
        v[:] = depth
        for name, src in (("TLAT", "tlat"), ("TLON", "tlon")):
            c = nc.createVariable(name, "f8", ("y", "x"))
            c.units = "degrees"
            c[:] = np.rad2deg(f[src])
        _stamp(nc, "bathymetry")


def write_esmf_mesh(path: Path, g: dict, kmt):
    """ESMF unstructured mesh of the grid cells: element (j, i) is number
    j*nx + i (the order CICE uses), corners counter-clockwise."""
    import numpy as np
    import netCDF4 as nc4
    nx, ny = g["nx"], g["ny"]
    cx = g["lon0"] + np.arange(nx + 1) * g["dlon"]
    cy = g["lat0"] + np.arange(ny + 1) * g["dlat"]
    CX, CY = np.meshgrid(cx, cy)
    nodes = np.column_stack([CX.ravel(), CY.ravel()])
    J, I = np.meshgrid(np.arange(ny), np.arange(nx), indexing="ij")
    n0 = (J * (nx + 1) + I).ravel()
    conn = np.column_stack([n0, n0 + 1, n0 + nx + 2, n0 + nx + 1]) + 1
    tx = g["lon0"] + (np.arange(nx) + 0.5) * g["dlon"]
    ty = g["lat0"] + (np.arange(ny) + 0.5) * g["dlat"]
    TX, TY = np.meshgrid(tx, ty)
    centers = np.column_stack([TX.ravel(), TY.ravel()])
    with nc4.Dataset(str(path), "w") as nc:
        nc.createDimension("nodeCount", nodes.shape[0])
        nc.createDimension("elementCount", conn.shape[0])
        nc.createDimension("maxNodePElement", 4)
        nc.createDimension("coordDim", 2)
        v = nc.createVariable("nodeCoords", "f8", ("nodeCount", "coordDim"))
        v.units = "degrees"
        v[:] = nodes
        v = nc.createVariable("elementConn", "i4",
                              ("elementCount", "maxNodePElement"),
                              fill_value=-1)
        v.long_name = "Node Indices that define the element connectivity"
        v.start_index = 1
        v[:] = conn
        v = nc.createVariable("numElementConn", "i1", ("elementCount",))
        v.long_name = "Number of nodes per element"
        v[:] = 4
        v = nc.createVariable("centerCoords", "f8",
                              ("elementCount", "coordDim"))
        v.units = "degrees"
        v[:] = centers
        v = nc.createVariable("elementMask", "i4", ("elementCount",))
        v[:] = np.asarray(kmt, dtype=np.int32).ravel()
        nc.gridType = "unstructured mesh"
        nc.version = "0.9"
        _stamp(nc, "ESMF mesh")


def plot_grid(path: Path, f: dict, kmt, depth, lon, lat, g: dict):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    ext = [g["lon0"], g["lon0"] + g["nx"] * g["dlon"],
           g["lat0"], g["lat0"] + g["ny"] * g["dlat"]]
    fig, ax = plt.subplots(1, 2, figsize=(16, 7), constrained_layout=True)
    ax[0].imshow(kmt, origin="lower", extent=ext, cmap="Blues", vmin=0,
                 vmax=1.5, aspect="auto", interpolation="nearest")
    step = max(1, lon.size // 50000)
    ax[0].plot(lon[::step], lat[::step], ",", color="red", alpha=0.3)
    ax[0].set_title(f"kmt (blue = ocean), {g['nx']} x {g['ny']}, "
                    f"{int(kmt.sum())} ocean cells; red: SCHISM nodes")
    d = np.ma.masked_where(kmt < 0.5, depth)
    im = ax[1].imshow(d, origin="lower", extent=ext, cmap="viridis_r",
                      aspect="auto", interpolation="nearest",
                      norm=matplotlib.colors.LogNorm(vmin=1, vmax=max(
                          10.0, float(np.nanmax(d)))))
    fig.colorbar(im, ax=ax[1], label="depth (m)")
    ax[1].set_title("Bathymetry on ocean cells")
    for a in ax:
        a.set_xlabel("longitude")
        a.set_ylabel("latitude")
    fig.savefig(path, dpi=110)
    plt.close(fig)


# =============================================================================
# Step
# =============================================================================

def run_gen_cice_grid(cfg: dict) -> bool:
    import numpy as np
    out = cice_dir(cfg)
    sentinel = out / "gen_cice_grid.done"
    have = [p for p in PRODUCTS if (out / p).exists()]
    if sentinel.exists() and len(have) == len(PRODUCTS):
        print(f"  gen_cice_grid: {out} complete (sentinel found). Skipping.")
        return True
    if have and len(have) != len(PRODUCTS):
        print(f"ERROR: {out} has only some of the CICE grid files "
              f"({', '.join(have)}). They must be made together: move them "
              f"aside and re-run gen_cice_grid.")
        return False
    if have:
        print(f"  gen_cice_grid: all files present in {out}; kept "
              f"(not made by this step, no sentinel).")
        return True

    hgrid = model_dir(cfg) / "fix" / "hgrid.ll"
    if not hgrid.exists():
        print(f"ERROR: {hgrid} not found.")
        return False
    s = settings(cfg)
    print(f"--- gen_cice_grid: {hgrid} -> {out} ---")
    print(f"  settings: {s}")
    lon, lat, dep, tris = read_hgrid(hgrid)
    g = make_grid(lon, lat, s)
    print(f"  grid {g['nx']} x {g['ny']} = {g['nx'] * g['ny']} cells, "
          f"lon {g['lon0']:.3f} + {g['dlon']:.5f}, lat {g['lat0']:.3f} + "
          f"{g['dlat']:.5f} deg")
    f = grid_fields(g)
    cov, depth = coverage_and_depth(lon, lat, dep, tris, g, s["samples"])
    kmt = (cov >= float(s["min_coverage"])).astype(float)
    depth = np.where(kmt > 0, np.fmax(np.nan_to_num(depth, nan=0.0),
                                      float(s["min_depth"])), 0.0)
    print(f"  ocean cells: {int(kmt.sum())} of {kmt.size} "
          f"({100 * kmt.mean():.1f}%)")

    out.mkdir(parents=True, exist_ok=True)
    tmp = {p: out / (p + ".tmp") for p in PRODUCTS}
    write_grid(tmp["grid_cice.nc"], f, g)
    write_kmt(tmp["kmt_cice.nc"], kmt, g)
    write_bathy(tmp["bathy_cice.nc"], depth, f, g)
    write_esmf_mesh(tmp["mesh_cice.nc"], g, kmt)
    for p, t in tmp.items():
        t.replace(out / p)
    try:
        plot_grid(out / "cice_grid.png", f, kmt, depth, lon, lat, g)
    except Exception as exc:                      # the plot is only a check
        print(f"  WARNING: cice_grid.png not made: {exc}")
    sentinel.write_text(f"nx {g['nx']}\nny {g['ny']}\n")
    print(f"  Written: {', '.join(PRODUCTS)} in {out}")
    return True


def submit_gen_cice_grid(cfg: dict, config_dir: Path) -> str:
    """Run the step as a SLURM job; returns the job id ('' when done)."""
    from workflow.core.environment import env_python
    from workflow.core.slurm import (
        DEFAULT_ACCOUNT, DEFAULT_MAIL_USER, DEFAULT_PARTITION, SlurmSubmitter,
    )
    out = cice_dir(cfg)
    if (out / "gen_cice_grid.done").exists() and all(
            (out / p).exists() for p in PRODUCTS):
        print(f"  gen_cice_grid: {out} complete (sentinel found). Skipping.")
        return ""
    mdir = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    slurm = cfg.get("slurm", {})
    subs = {
        "JOBNAME":    f"cicegrid_M{cfg['project_id']}",
        "WORKDIR":    str(mdir),
        "ACCOUNT":    slurm.get("account",   DEFAULT_ACCOUNT),
        "PARTITION":  slurm.get("partition", DEFAULT_PARTITION),
        "MAILUSER":   slurm.get("mail_user", DEFAULT_MAIL_USER),
        "MEM":        slurm.get("gen_cice_grid_mem", "64G"),
        "WALLTIME":   slurm.get("gen_cice_grid_walltime", "01:00:00"),
        "LOGDIR":     str(logdir),
        "PY":         env_python(cfg, "gen_cice_grid", default="swf_plot"),
        "CONFIG_DIR": str(config_dir),
    }
    templates = Path(__file__).resolve().parents[1] / "templates" / "slurm"
    jid = SlurmSubmitter.parse_jobid(SlurmSubmitter(templates)
                                     .render_and_submit(
        "gen_cice_grid.sbatch", subs, logdir / "gen_cice_grid.sbatch"))
    print(f"  Submitted gen_cice_grid (job {jid}). Log: "
          f"{logdir}/gen_cice_grid.out")
    return jid


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)
    sys.exit(0 if run_gen_cice_grid(load_config(Path(args.config))) else 1)


if __name__ == "__main__":
    main()
