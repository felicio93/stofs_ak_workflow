"""
diagnostics/inspect_mesh.py
===========================
Step 0 worker — SLURM job that reads all fix/ input files and generates
diagnostic JPEG plots saved to M{ID}/D{ID}/D{ID}_fix/.

One plot per file:
  hgrid.gr3         -> bathymetry
  albedo.gr3
  diffmin.gr3
  diffmax.gr3
  watertype.gr3     -> fixed colorbar 1-10
  shapiro.gr3
  windrot_geo2proj.gr3
  rough.gr3 | drag.gr3 | manning.gr3  -> auto-detect friction file
  TEM_nudge.gr3
  SAL_nudge.gr3
  estuary.gr3       -> skip silently if missing
  mesh_resolution   -> computed from hgrid.gr3
                       D = 2*sqrt(Area/pi) metres (diameter of
                       equivalent circle, not radius)
  vertical_layers   -> computed from vgrid.in (nvrt - kbp + 1 per node)
  rx1               -> vgrid_work/rx1.gr3 from gen_vgrid (largest
                       hydrostatic-consistency rx1 of the sides at each node)
  partition         -> partition.prop: each MPI rank's elements in one
                       colour, neighbouring ranks always in different
                       colours (a rank split into pieces shows as several
                       separate patches of one colour)

Outputs are JPEG files. A sentinel file (inspect_mesh.done) is
written after all plots complete.

Usage (called by SLURM via workflow.diagnostics.submit_inspect_mesh):
    python -m workflow.diagnostics.inspect_mesh --config <config_dir>
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import matplotlib.tri as mtri

from workflow.core.config import load_config, model_dir
from workflow.core.plot_style import (
    make_frame_tripcolor,
    read_mesh_boundaries,
)
from workflow.core.mesh_parser import read_nodes

DPI         = 300
QUALITY     = 92
PADDING_LON = 5.0
PADDING_LAT = 2.0


# =============================================================================
# Mesh reader
# =============================================================================

def read_hgrid(hgrid_path: Path):
    """Read hgrid.gr3 and return (lon, lat, depth, triangles, elem_map).
    Quads are split into two triangles for matplotlib Triangulation.
    """
    print(f"  Reading {hgrid_path.name} ...")

    _, lon, lat, depth = read_nodes(hgrid_path)
    np_nodes = len(lon)

    triangles = []
    elem_idx  = []
    with open(hgrid_path) as f:
        f.readline()                          # title
        ne, _ = map(int, f.readline().split())
        for _ in range(np_nodes):             # skip node block
            f.readline()
        for e in range(ne):
            parts = f.readline().split()
            nv    = int(parts[1])
            n     = [int(x) - 1 for x in parts[2:2 + nv]]
            triangles.append([n[0], n[1], n[2]])
            elem_idx.append(e)
            if nv == 4:
                triangles.append([n[0], n[2], n[3]])
                elem_idx.append(e)

    tri_arr  = np.array(triangles, dtype=int)
    elem_map = np.array(elem_idx,  dtype=int)
    print(f"    {np_nodes:,} nodes, {ne:,} elements")
    return lon, lat, depth, tri_arr, elem_map


def read_gr3_values(path: Path, np_nodes: int):
    """Read the scalar value column from a .gr3 file."""
    from workflow.core.mesh_parser import read_nodes as _read_nodes
    _, _, _, vals = _read_nodes(path)
    return vals


def read_vgrid_layers(vgrid_path: Path, np_nodes: int):
    """Read vgrid.in and return the number of active layers per node.

    Returns (ivcor, nvrt, nlayers_per_node).
    For SZ  (ivcor=2): nlayers is uniform = nvrt.
    For LSC2 (ivcor=1): nlayers[i] = nvrt - kbp[i] + 1.
    """
    print(f"  Reading {vgrid_path.name} (first 3 lines only) ...")
    with open(vgrid_path) as f:
        # first token only: lines carry comments ("2 !ivcor"), and the SZ
        # second line is "nvrt kz h_s"
        ivcor = int(f.readline().split()[0])
        nvrt  = int(f.readline().split()[0])
        if ivcor == 1:    # LSC2
            kbp_line = f.readline()
            kbp      = np.fromstring(kbp_line, dtype=int, sep=' ')
            nlayers  = nvrt - kbp + 1
            print(f"    LSC2: nvrt={nvrt}, "
                  f"kbp range [{kbp.min()},{kbp.max()}], "
                  f"layers range [{nlayers.min()},{nlayers.max()}]")
        else:              # SZ
            nlayers = np.full(np_nodes, nvrt, dtype=int)
            print(f"    SZ: nvrt={nvrt} (uniform)")
    return ivcor, nvrt, nlayers


# =============================================================================
# Mesh resolution
# =============================================================================

def compute_resolution_m(lon, lat, triangles):
    """Compute equivalent-circle diameter per element in metres.

    D = 2 * sqrt(Area / pi)

    This is the diameter (not the radius) of the circle whose area
    equals the element area. Uses the spherical excess / Heron formula
    for accuracy over the large Alaska domain.
    """
    from numpy import deg2rad, sin, cos, arctan2, sqrt

    R_earth = 6371000.0   # metres

    n0, n1, n2 = triangles[:, 0], triangles[:, 1], triangles[:, 2]

    lo0, la0 = deg2rad(lon[n0]), deg2rad(lat[n0])
    lo1, la1 = deg2rad(lon[n1]), deg2rad(lat[n1])
    lo2, la2 = deg2rad(lon[n2]), deg2rad(lat[n2])

    def haversine(lo_a, la_a, lo_b, la_b):
        dlo = lo_b - lo_a
        dla = la_b - la_a
        a   = (sin(dla/2)**2
               + cos(la_a) * cos(la_b) * sin(dlo/2)**2)
        return 2 * R_earth * arctan2(sqrt(a), sqrt(1 - a))

    a = haversine(lo1, la1, lo2, la2)
    b = haversine(lo0, la0, lo2, la2)
    c = haversine(lo0, la0, lo1, la1)

    s    = (a + b + c) / 2
    area = np.sqrt(np.maximum(s*(s-a)*(s-b)*(s-c), 0.0))

    # Diameter of the equivalent circle
    return 2.0 * np.sqrt(area / np.pi)



def partition_colours(rank, tri_arr, elem_map, np_nodes):
    """Colour class per rank such that ranks sharing a node never share a
    colour (greedy colouring of the rank adjacency graph, highest degree
    first). Returns (class per rank, number of classes, number of ranks).
    Falls back to shuffled ranks mod 20 without scipy or beyond 40
    classes."""
    nrank = int(rank.max()) + 1
    try:
        from scipy import sparse
        er = rank[elem_map]                       # rank of each triangle
        A = sparse.csr_matrix(
            (np.ones(tri_arr.size, np.int8),
             (np.repeat(er, tri_arr.shape[1]), tri_arr.ravel())),
            shape=(nrank, np_nodes))
        A.sum_duplicates(); A.data[:] = 1
        nb = (A @ A.T).tocsr()
        cls = np.full(nrank, -1, dtype=np.int64)
        for r in np.argsort(-np.diff(nb.indptr), kind="stable"):
            used = set(cls[nb.indices[nb.indptr[r]:nb.indptr[r + 1]]])
            c = 0
            while c in used:
                c += 1
            cls[r] = c
        ncls = int(cls.max()) + 1
        if ncls <= 40:
            return cls, ncls, nrank
    except ImportError:
        pass
    perm = np.random.default_rng(15).permutation(nrank)
    return perm % 20, 20, nrank

# =============================================================================
# Plot helper
# =============================================================================

def make_plot(triangulation, values, title, out_path,
              lon_min, lon_max, lat_min, lat_max,
              cmap="viridis", vmin=None, vmax=None,
              cbar_label=None, is_elem=False,
              cbar_ticks=None, cbar_extend_minmax=False,
              boundaries=None):
    """Delegate to the shared plot_style tripcolor helper."""
    return make_frame_tripcolor(
        triangulation, values, title, out_path,
        cbar_label=cbar_label, cmap=cmap,
        vmin=vmin, vmax=vmax,
        is_elem=is_elem, cbar_ticks=cbar_ticks,
        cbar_extend_minmax=cbar_extend_minmax,
        padding_lon=PADDING_LON, padding_lat=PADDING_LAT,
        dpi=DPI, quality=QUALITY, boundaries=boundaries,
    )


# =============================================================================
# Main
# =============================================================================

def inspect_mesh(cfg: dict):
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    fix  = mdir / "fix"
    out  = mdir / f"D{pid}" / f"D{pid}_fix"
    out.mkdir(parents=True, exist_ok=True)

    sentinel = out / "inspect_mesh.done"
    if sentinel.exists():
        print("  inspect_mesh already complete "
              "(sentinel found). Skipping.")
        return

    lon_min = float(cfg["lon_min"])
    lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"])
    lat_max = float(cfg["lat_max"])

    # --- Read mesh ---
    hgrid = fix / "hgrid.gr3"
    if not hgrid.exists():
        print(f"ERROR: {hgrid} not found. "
              "Copy hgrid.gr3 to fix/ first.")
        sys.exit(1)

    lon, lat, depth, tri_arr, elem_map = read_hgrid(hgrid)
    np_nodes = len(lon)

    triang = mtri.Triangulation(lon, lat, tri_arr)

    # Load mesh boundaries for overlay on all plots
    hgrid_ll = fix / "hgrid.ll"
    boundaries = None
    for hpath in (hgrid_ll, hgrid):
        try:
            boundaries = read_mesh_boundaries(hpath)
            print(
                f"  Loaded mesh boundaries: "
                f"{len(boundaries['open'])} open, "
                f"{len(boundaries['land'])} land, "
                f"{len(boundaries['island'])} island")
            break
        except Exception as exc:
            print(f"  WARNING: could not read boundaries "
                  f"from {hpath.name}: {exc}")

    # --- Bathymetry ---
    make_plot(
        triang, depth, "Bathymetry (m)",
        out / "bathymetry.jpg",
        lon_min, lon_max, lat_min, lat_max,
        cmap="viridis", cbar_label="Depth (m)",
        cbar_extend_minmax=True, boundaries=boundaries)

    # --- Standard .gr3 scalar files ---
    gr3_specs = [
        ("albedo.gr3",
         "Albedo",
         "viridis",  None, None, None),
        ("diffmin.gr3",
         "Diffmin",
         "plasma",   None, None, None),
        ("diffmax.gr3",
         "Diffmax",
         "plasma",   None, None, None),
        ("watertype.gr3",
         "Water Type",
         "tab20b",   1.0,  10.0, list(range(1, 11))),
        ("shapiro.gr3",
         "Shapiro Filter",
         "viridis",  None, None, None),
        ("windrot_geo2proj.gr3",
         "Wind Rotation (deg)",
         "RdBu_r",   None, None, None),
        ("TEM_nudge.gr3",
         "Temperature Nudging Mask",
         "YlOrRd",   None, None, None),
        ("SAL_nudge.gr3",
         "Salinity Nudging Mask",
         "YlOrRd",   None, None, None),
    ]
    for fname, title, cmap, vmin, vmax, ticks in gr3_specs:
        fpath = fix / fname
        if not fpath.exists():
            print(f"  WARNING: {fname} not found in fix/, "
                  f"skipping.")
            continue
        print(f"  Processing {fname} ...")
        vals = read_gr3_values(fpath, np_nodes)
        stem = fname.replace(".gr3", "")
        make_plot(
            triang, vals, title, out / f"{stem}.jpg",
            lon_min, lon_max, lat_min, lat_max,
            cmap=cmap, vmin=vmin, vmax=vmax,
            cbar_ticks=ticks, boundaries=boundaries)

    # --- Estuary (optional) ---
    estuary = fix / "estuary.gr3"
    if estuary.exists():
        print("  Processing estuary.gr3 ...")
        vals = read_gr3_values(estuary, np_nodes)
        make_plot(
            triang, vals, "Estuary Mask",
            out / "estuary.jpg",
            lon_min, lon_max, lat_min, lat_max,
            cmap="YlOrRd", vmin=0, vmax=1,
            boundaries=boundaries)
    else:
        print("  estuary.gr3 not found in fix/, skipping "
              "(run gen_estuary first).")

    # --- tvd.prop (element-based) ---
    tvd_path = fix / "tvd.prop"
    if tvd_path.exists():
        print("  Processing tvd.prop ...")
        tvd_raw = np.loadtxt(tvd_path, usecols=1)
        tvd_tri = tvd_raw[elem_map]
        make_plot(
            triang, tvd_tri,
            "TVD Property (1=TVD, 0=no TVD)",
            out / "tvd.jpg",
            lon_min, lon_max, lat_min, lat_max,
            cmap="cividis", vmin=0, vmax=1,
            is_elem=True, boundaries=boundaries)
    else:
        print("  tvd.prop not found in fix/, skipping.")

    # --- partition.prop (element-based): one colour per MPI rank ---
    part_path = fix / "partition.prop"
    if part_path.exists():
        print("  Processing partition.prop ...")
        rank = np.loadtxt(part_path, usecols=1, dtype=np.int64)
        cls, ncls, nrank = partition_colours(rank, tri_arr, elem_map,
                                             np_nodes)
        from matplotlib.colors import ListedColormap
        import matplotlib.pyplot as plt
        cmap = ListedColormap(
            (list(plt.get_cmap("tab20").colors)
             + list(plt.get_cmap("tab20b").colors))[:max(ncls, 2)])
        make_plot(
            triang, cls[rank][elem_map].astype(float),
            f"Domain decomposition ({nrank} ranks, partition.prop)",
            out / "partition.jpg",
            lon_min, lon_max, lat_min, lat_max,
            cmap=cmap, vmin=-0.5, vmax=max(ncls, 2) - 0.5,
            cbar_label=f"colour class ({ncls} colours; neighbouring "
                       f"ranks always differ)",
            is_elem=True, boundaries=boundaries)
    else:
        print("  partition.prop not found in fix/, skipping.")

    # --- Bottom friction (auto-detect) ---
    friction_found = None
    for fname in ("rough.gr3", "drag.gr3", "manning.gr3"):
        if (fix / fname).exists():
            friction_found = fname
            break
    if friction_found:
        print(f"  Processing {friction_found} "
              f"(bottom friction) ...")
        vals = read_gr3_values(fix / friction_found, np_nodes)
        make_plot(
            triang, vals,
            f"Bottom Friction ({friction_found})",
            out / "bottom_friction.jpg",
            lon_min, lon_max, lat_min, lat_max,
            cmap="turbo", boundaries=boundaries)
    else:
        print("  WARNING: no friction file "
              "(rough.gr3/drag.gr3/manning.gr3) found.")

    # --- Mesh resolution ---
    # D = 2 * sqrt(Area/pi)  — diameter of equivalent circle
    print("  Computing mesh resolution ...")
    resolution = compute_resolution_m(lon, lat, tri_arr)
    # colour scale over the full range (smallest to largest element)
    res_vmax = float(resolution.max())
    make_frame_tripcolor(
        triang, resolution,
        "Mesh Resolution  D = 2√(Area/π)  (m)",
        out / "mesh_resolution.jpg",
        cbar_label="Resolution (m)",
        cmap="turbo_r",
        is_elem=True, vmax=res_vmax,
        padding_lon=PADDING_LON, padding_lat=PADDING_LAT,
        dpi=DPI, quality=QUALITY, boundaries=boundaries,
    )
    print("  -> Saved mesh_resolution.jpg")

    # --- Vertical layers ---
    vgrid = fix / "vgrid.in"
    if vgrid.exists():
        print("  Computing vertical layers from vgrid.in ...")
        _, nvrt, nlayers = read_vgrid_layers(vgrid, np_nodes)
        make_plot(
            triang, nlayers.astype(float),
            f"Number of Vertical Layers  (nvrt={nvrt})",
            out / "vertical_layers.jpg",
            lon_min, lon_max, lat_min, lat_max,
            cmap="viridis",
            cbar_label="Number of active layers",
            boundaries=boundaries)
    else:
        print("  WARNING: vgrid.in not found in fix/, "
              "skipping vertical layers plot.")

    # --- Hydrostatic consistency rx1 (written by gen_vgrid) ---
    rx1_path = mdir / "vgrid_work" / "rx1.gr3"
    if rx1_path.exists():
        print("  Processing vgrid_work/rx1.gr3 ...")
        rx = read_gr3_values(rx1_path, np_nodes)
        wet = rx[rx > 0]
        top = float(np.percentile(wet, 99.9)) if wet.size else 1.0
        make_plot(
            triang, rx,
            f"Hydrostatic consistency rx1 (max per node; max "
            f"{rx.max():.1f}, 99th pct {np.percentile(wet, 99):.1f})"
            if wet.size else "Hydrostatic consistency rx1",
            out / "rx1.jpg",
            lon_min, lon_max, lat_min, lat_max,
            cmap="magma_r", vmin=0, vmax=max(top, 1.0),
            cbar_label="rx1 (colour scale to the 99.9th percentile)",
            boundaries=boundaries)
    else:
        print("  vgrid_work/rx1.gr3 not found (made by gen_vgrid), "
              "skipping the rx1 plot.")

    sentinel.touch()
    print(f"\n  All mesh diagnostic plots written to {out}")
    print(f"  Sentinel: {sentinel}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate mesh diagnostic plots from "
                    "fix/ files")
    parser.add_argument(
        "--config", required=True,
        help="Path to config/ directory")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    inspect_mesh(cfg)
