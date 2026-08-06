"""
inspect_mesh.py
===============
Step 0 worker — SLURM job that reads all fix/ input files and generates
diagnostic TIFF plots saved to M{ID}/D{ID}/D{ID}_fix/.

One plot per file:
  hgrid.gr3         -> bathymetry
  albedo.gr3
  diffmin.gr3
  diffmax.gr3
  diffmax.gr3
  watertype.gr3     -> fixed colorbar 1-10
  shapiro.gr3
  windrot_geo2proj.gr3
  rough.gr3 | drag.gr3 | manning.gr3  -> auto-detect friction file
  TEM_nudge.gr3
  SAL_nudge.gr3
  estuary.gr3       -> skip silently if missing
  mesh_resolution   -> computed from hgrid.gr3 (R = sqrt(Area/pi), meters)
  vertical_layers   -> computed from vgrid.in (nvrt - kbp + 1 per node)

Outputs are 300 DPI TIFF files. A sentinel file (inspect_mesh.done) is
written after all plots complete.

Usage (called by SLURM via submit_inspect_mesh.py):
    python inspect_mesh.py --config <config_dir>
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import matplotlib.tri as mtri

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow.config import load_config, model_dir
from workflow.plot_style import make_frame_tripcolor, read_mesh_boundaries

DPI         = 300
QUALITY     = 92
PADDING_LON = 5.0
PADDING_LAT = 2.0


# =============================================================================
# Mesh reader
# =============================================================================

def read_hgrid(hgrid_path: Path):
    """
    Read hgrid.gr3 and return (lon, lat, depth, triangles).
    Quads are split into two triangles.
    """
    print(f"  Reading {hgrid_path.name} ...")
    triangles = []
    elem_idx   = []   # original element index for each triangle (for quad splitting)

    with open(hgrid_path) as f:
        f.readline()  # title
        ne, np_nodes = map(int, f.readline().split())

        lon   = np.empty(np_nodes)
        lat   = np.empty(np_nodes)
        depth = np.empty(np_nodes)

        for i in range(np_nodes):
            parts = f.readline().split()
            lon[i]   = float(parts[1])
            lat[i]   = float(parts[2])
            depth[i] = float(parts[3])

        for e in range(ne):
            parts = f.readline().split()
            nv = int(parts[1])
            n  = [int(x) - 1 for x in parts[2:2+nv]]
            triangles.append([n[0], n[1], n[2]])
            elem_idx.append(e)
            if nv == 4:
                triangles.append([n[0], n[2], n[3]])
                elem_idx.append(e)

    tri_arr  = np.array(triangles, dtype=int)
    elem_map = np.array(elem_idx, dtype=int)
    print(f"    {np_nodes:,} nodes, {ne:,} elements")
    return lon, lat, depth, tri_arr, elem_map


def read_gr3_values(path: Path, np_nodes: int):
    """Read the scalar depth/value column from a .gr3 file (NP values)."""
    vals = np.empty(np_nodes)
    with open(path) as f:
        f.readline(); f.readline()
        for i in range(np_nodes):
            parts = f.readline().split()
            vals[i] = float(parts[3])
    return vals


def read_prop_values(path: Path, ne: int):
    """Read a .prop file (NE element values, format: id val)."""
    vals = np.empty(ne)
    with open(path) as f:
        for e in range(ne):
            parts = f.readline().split()
            vals[e] = float(parts[1])
    return vals


def read_vgrid_layers(vgrid_path: Path, np_nodes: int):
    """
    Read only the first 3 meaningful lines of vgrid.in to get
    the number of active layers per node.
    Returns (ivcor, nvrt, nlayers_per_node).
    For SZ (ivcor=2): nlayers is uniform = nvrt.
    For LSC2 (ivcor=1): nlayers[i] = nvrt - kbp[i] + 1.
    """
    print(f"  Reading {vgrid_path.name} (first 3 lines only) ...")
    with open(vgrid_path) as f:
        ivcor = int(f.readline().strip())
        nvrt  = int(f.readline().strip())
        if ivcor == 1:  # LSC2
            kbp_line = f.readline()
            kbp = np.fromstring(kbp_line, dtype=int, sep=' ')
            nlayers = nvrt - kbp + 1
            print(f"    LSC2: nvrt={nvrt}, kbp range [{kbp.min()},{kbp.max()}], "
                  f"layers range [{nlayers.min()},{nlayers.max()}]")
        else:  # SZ
            nlayers = np.full(np_nodes, nvrt, dtype=int)
            print(f"    SZ: nvrt={nvrt} (uniform)")
    return ivcor, nvrt, nlayers


# =============================================================================
# Mesh resolution
# =============================================================================

def compute_resolution_m(lon, lat, triangles):
    """
    Compute equivalent-circle radius R = sqrt(Area / pi) per element in meters.
    Uses the spherical excess formula (accurate for large domains).
    """
    from numpy import deg2rad, sin, cos, arctan2, sqrt, pi
    R_earth = 6371000.0  # metres

    n0, n1, n2 = triangles[:, 0], triangles[:, 1], triangles[:, 2]

    # Convert to radians
    lo0, la0 = deg2rad(lon[n0]), deg2rad(lat[n0])
    lo1, la1 = deg2rad(lon[n1]), deg2rad(lat[n1])
    lo2, la2 = deg2rad(lon[n2]), deg2rad(lat[n2])

    # Side lengths via haversine
    def haversine(lo_a, la_a, lo_b, la_b):
        dlo = lo_b - lo_a
        dla = la_b - la_a
        a = sin(dla/2)**2 + cos(la_a)*cos(la_b)*sin(dlo/2)**2
        return 2*R_earth*arctan2(sqrt(a), sqrt(1-a))

    a = haversine(lo1, la1, lo2, la2)
    b = haversine(lo0, la0, lo2, la2)
    c = haversine(lo0, la0, lo1, la1)

    # Area via Heron's formula
    s = (a + b + c) / 2
    area = np.sqrt(np.maximum(s*(s-a)*(s-b)*(s-c), 0.0))
    return np.sqrt(area / np.pi)


# =============================================================================
# Plotting helpers
# =============================================================================

def _base_map(ax, extent, proj):
    """Set map extent and add features. extent must be in the projection's CRS."""
    ax.set_extent(extent, crs=proj)
    ax.add_feature(cfeature.LAND, facecolor="lightgray", zorder=2, edgecolor="k", linewidth=0.3)
    ax.add_feature(cfeature.COASTLINE, zorder=3, linewidth=0.3)
    gl = ax.gridlines(draw_labels=True, linewidth=0.4, color="gray",
                      alpha=0.5, linestyle="--", zorder=4)
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {"size": 8}
    gl.ylabel_style = {"size": 8}


def _shift_lons(lon):
    """Convert 0-360 longitudes to -180..180 for Cartopy compatibility."""
    return np.where(lon > 180, lon - 360, lon)


def _plot_extent(lon_min, lon_max, lat_min, lat_max):
    """Return extent in -180..180 space."""
    lmin = lon_min if lon_min <= 180 else lon_min - 360
    lmax = lon_max if lon_max <= 180 else lon_max - 360
    return [lmin, lmax, lat_min, lat_max]


# =============================================================================
# Plotting helpers (pure matplotlib, no cartopy)
# =============================================================================

def make_plot(triangulation, values, title, out_path,
              lon_min, lon_max, lat_min, lat_max,
              cmap="viridis", vmin=None, vmax=None,
              cbar_label=None, is_elem=False,
              cbar_ticks=None, cbar_extend_minmax=False,
              boundaries=None):
    """Delegate to the shared plot_style helper."""
    return make_frame_tripcolor(
        triangulation, values, title, out_path,
        cbar_label=cbar_label, cmap=cmap, vmin=vmin, vmax=vmax,
        is_elem=is_elem, cbar_ticks=cbar_ticks,
        cbar_extend_minmax=cbar_extend_minmax,
        padding_lon=PADDING_LON, padding_lat=PADDING_LAT,
        dpi=DPI, quality=QUALITY, boundaries=boundaries,
    )


# =============================================================================
# Main
# =============================================================================

def inspect_mesh(cfg: dict):
    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    fix   = mdir / "fix"
    out   = mdir / f"D{pid}" / f"D{pid}_fix"
    out.mkdir(parents=True, exist_ok=True)

    sentinel = out / "inspect_mesh.done"
    if sentinel.exists():
        print("  inspect_mesh already complete (sentinel found). Skipping.")
        return

    lon_min = float(cfg["lon_min"]); lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"]); lat_max = float(cfg["lat_max"])

    # --- Read mesh ---
    hgrid = fix / "hgrid.gr3"
    if not hgrid.exists():
        print(f"ERROR: {hgrid} not found. Copy hgrid.gr3 to fix/ first.")
        sys.exit(1)

    lon, lat, depth, tri_arr, elem_map = read_hgrid(hgrid)
    np_nodes = len(lon)
    ne_tri   = len(tri_arr)

    # Build triangulation directly in geographic lon/lat (0-360).
    triang = mtri.Triangulation(lon, lat, tri_arr)

    # Read mesh boundaries for overlay on all plots
    hgrid_ll = fix / "hgrid.ll"
    boundaries = None
    for hpath in (hgrid_ll, hgrid):
        try:
            boundaries = read_mesh_boundaries(hpath)
            print(f"  Loaded mesh boundaries: {len(boundaries['open'])} open, "
                  f"{len(boundaries['land'])} land, "
                  f"{len(boundaries['island'])} island")
            break
        except Exception as exc:
            print(f"  WARNING: could not read boundaries from {hpath.name}: {exc}")

    # --- Bathymetry ---
    make_plot(triang, depth, "Bathymetry (m)", out / "bathymetry.tiff",
              lon_min, lon_max, lat_min, lat_max,
              cmap="viridis", cbar_label="Depth (m)",
              cbar_extend_minmax=True, boundaries=boundaries)

    # --- Standard .gr3 scalar files ---
    gr3_specs = [
        ("albedo.gr3",            "Albedo",               "viridis",  None, None, None),
        ("diffmin.gr3",           "Diffmin",              "plasma",   None, None, None),
        ("diffmax.gr3",           "Diffmax",              "plasma",   None, None, None),
        ("watertype.gr3",         "Water Type",           "tab20b",   1.0,  10.0,
            list(range(1, 11))),
        ("shapiro.gr3",           "Shapiro Filter",       "viridis",  None, None, None),
        ("windrot_geo2proj.gr3",  "Wind Rotation (deg)",  "RdBu_r",   None, None, None),
        ("TEM_nudge.gr3",         "Temperature Nudging Mask", "YlOrRd", None, None, None),
        ("SAL_nudge.gr3",         "Salinity Nudging Mask",    "YlOrRd", None, None, None),
    ]
    for fname, title, cmap, vmin, vmax, ticks in gr3_specs:
        fpath = fix / fname
        if not fpath.exists():
            print(f"  WARNING: {fname} not found in fix/, skipping.")
            continue
        print(f"  Processing {fname} ...")
        vals = read_gr3_values(fpath, np_nodes)
        stem = fname.replace(".gr3", "")
        make_plot(triang, vals, title, out / f"{stem}.tiff",
                  lon_min, lon_max, lat_min, lat_max,
                  cmap=cmap, vmin=vmin, vmax=vmax,
                  cbar_ticks=ticks, boundaries=boundaries)

    # --- Estuary (optional) ---
    estuary = fix / "estuary.gr3"
    if estuary.exists():
        print("  Processing estuary.gr3 ...")
        vals = read_gr3_values(estuary, np_nodes)
        make_plot(triang, vals, "Estuary Mask", out / "estuary.tiff",
                  lon_min, lon_max, lat_min, lat_max,
                  cmap="YlOrRd", vmin=0, vmax=1, boundaries=boundaries)
    else:
        print("  estuary.gr3 not found in fix/, skipping (run gen_estuary first).")

    # --- tvd.prop (element-based: 0=no TVD, 1=TVD) ---
    tvd_path = fix / "tvd.prop"
    if tvd_path.exists():
        print("  Processing tvd.prop ...")
        tvd_raw = np.loadtxt(tvd_path, usecols=1)
        tvd_tri = tvd_raw[elem_map]
        make_plot(triang, tvd_tri, "TVD Property (1=TVD, 0=no TVD)",
                  out / "tvd.tiff",
                  lon_min, lon_max, lat_min, lat_max,
                  cmap="cividis", vmin=0, vmax=1, is_elem=True,
                  boundaries=boundaries)
    else:
        print("  tvd.prop not found in fix/, skipping.")

    # --- Bottom friction (auto-detect) ---
    friction_found = None
    for fname in ("rough.gr3", "drag.gr3", "manning.gr3"):
        if (fix / fname).exists():
            friction_found = fname
            break
    if friction_found:
        print(f"  Processing {friction_found} (bottom friction) ...")
        vals = read_gr3_values(fix / friction_found, np_nodes)
        make_plot(triang, vals, f"Bottom Friction ({friction_found})",
                  out / "bottom_friction.tiff",
                  lon_min, lon_max, lat_min, lat_max,
                  cmap="turbo", boundaries=boundaries)
    else:
        print("  WARNING: no friction file (rough.gr3/drag.gr3/manning.gr3) found.")

    # --- Mesh resolution (log-scale colorbar via shared helper) ---
    print("  Computing mesh resolution ...")
    resolution = compute_resolution_m(lon, lat, tri_arr)
    make_frame_tripcolor(
        triang, resolution,
        "Mesh Resolution  R = √(Area/π)  (log scale)",
        out / "mesh_resolution.tiff",
        cbar_label="Resolution (km)", cmap="turbo_r",
        is_elem=True, is_log=True, cbar_km=True,
        padding_lon=PADDING_LON, padding_lat=PADDING_LAT,
        dpi=DPI, quality=QUALITY, boundaries=boundaries,
    )
    print(f"  -> Saved mesh_resolution.jpg")

    # --- Vertical layers ---
    vgrid = fix / "vgrid.in"
    if vgrid.exists():
        print("  Computing vertical layers from vgrid.in ...")
        _, nvrt, nlayers = read_vgrid_layers(vgrid, np_nodes)
        make_plot(triang, nlayers.astype(float),
                  f"Number of Vertical Layers  (nvrt={nvrt})",
                  out / "vertical_layers.tiff",
                  lon_min, lon_max, lat_min, lat_max,
                  cmap="viridis",
                  cbar_label="Number of active layers",
                  boundaries=boundaries)
    else:
        print("  WARNING: vgrid.in not found in fix/, skipping vertical layers plot.")

    sentinel.touch()
    print(f"\n  All mesh diagnostic plots written to {out}")
    print(f"  Sentinel: {sentinel}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate mesh diagnostic plots from fix/ files")
    parser.add_argument("--config", required=True,
                        help="Path to config/ directory")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    inspect_mesh(cfg)
