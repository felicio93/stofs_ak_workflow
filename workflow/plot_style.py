"""
plot_style.py
=============
Shared plotting style and helper for all workflow plot scripts.

Style conventions:
  - Vertical colorbar on the RIGHT side
  - Title: 12pt bold, pad=10
  - Axis labels: "Longitude (°E)" / "Latitude (°N)", 10pt
  - Tick labels: 9pt
  - constrained_layout=True
  - Figure size derived from domain aspect ratio
  - JPEG output at specified DPI
  - Optional mesh boundary overlay:
      open boundaries  -> blue  (solid, lw=0.8)
      land boundaries  -> red   (solid, lw=0.4)
      island boundaries -> green (solid, lw=0.4)
"""

from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
from matplotlib.colors import LogNorm


# ─────────────────────────────────────────────
# Consistent defaults
# ─────────────────────────────────────────────
TITLE_FS  = 12
LABEL_FS  = 10
TICK_FS   = 9
CBAR_FS   = 9


# =============================================================================
# Mesh boundary reader
# =============================================================================

def read_mesh_boundaries(hgrid_path: Path) -> dict:
    """
    Read hgrid.gr3 (or hgrid.ll) and return all boundary polygons grouped by
    type.

    Returns:
        {
          "open":   [(lons, lats), ...],   # ocean open boundaries -> blue
          "land":   [(lons, lats), ...],   # land type 0 -> red
          "island": [(lons, lats), ...],   # island type 1 -> green
        }

    The hgrid.gr3 format after the element section:
        nope             <- number of open boundaries
        neta             <- total open bnd nodes (ignored)
          nond_i           <- node count for open bnd i
          node_id ...      <- one node per line
          ...
        nland            <- number of land boundaries
        ntotal           <- total land bnd nodes (ignored)
          nond_j type_flag <- node count + type (0=land, 1=island)
          node_id ...
          ...

        Closes each polygon by repeating the first node at the end.

    Also returns mesh_extent: [lon_min, lon_max, lat_min, lat_max] derived
    from all boundary node coordinates (tightest bounding box of the mesh).
    """
    with open(hgrid_path) as f:
        f.readline()                          # title
        ne, np_nodes = map(int, f.readline().split())

        # Read node coordinates
        lons = np.empty(np_nodes)
        lats = np.empty(np_nodes)
        for i in range(np_nodes):
            parts = f.readline().split()
            lons[i] = float(parts[1])
            lats[i] = float(parts[2])

        # Skip elements
        for _ in range(ne):
            f.readline()

        # --- Open boundaries (polylines, NOT closed) ---
        nope = int(f.readline().split()[0])
        f.readline()  # total open bnd nodes
        open_bnds = []
        for _ in range(nope):
            nond = int(f.readline().split()[0])
            ids  = [int(f.readline().strip()) - 1 for _ in range(nond)]
            ids_arr = np.array(ids)
            open_bnds.append((lons[ids_arr], lats[ids_arr]))

        # --- Land / island boundaries ---
        nland = int(f.readline().split()[0])
        f.readline()  # total land bnd nodes
        land_bnds   = []
        island_bnds = []
        for _ in range(nland):
            header = f.readline().split()
            nond      = int(header[0])
            type_flag = int(header[1])
            ids = [int(f.readline().strip()) - 1 for _ in range(nond)]
            ids_arr = np.array(ids)
            if type_flag == 0:
                # Mainland coast: polyline (NOT closed — open ends connect to
                # open boundaries or other land segments)
                land_bnds.append((lons[ids_arr], lats[ids_arr]))
            else:
                # Island: genuine closed loop — close it
                lo = np.append(lons[ids_arr], lons[ids_arr[0]])
                la = np.append(lats[ids_arr], lats[ids_arr[0]])
                island_bnds.append((lo, la))

    # Mesh extent from all node coordinates
    mesh_extent = [float(lons.min()), float(lons.max()),
                   float(lats.min()), float(lats.max())]

    result = {"open": open_bnds, "land": land_bnds, "island": island_bnds}
    result["mesh_extent"] = mesh_extent
    return result


def _draw_boundaries(ax, boundaries: dict):
    """Overlay mesh boundaries on an axes (open=blue, land=red, island=green)."""
    if boundaries is None:
        return
    for lo, la in boundaries.get("open",   []):
        ax.plot(lo, la, color="blue",  lw=0.8, zorder=5)
    for lo, la in boundaries.get("land",   []):
        ax.plot(lo, la, color="red",   lw=0.4, zorder=5)
    for lo, la in boundaries.get("island", []):
        ax.plot(lo, la, color="green", lw=0.4, zorder=5)


# =============================================================================
# Figure helpers
# =============================================================================

def _aspect_figsize(lon_min, lon_max, lat_min, lat_max,
                    panel_h=5.0, max_w=9.0, cbar_room=1.0):
    """Return (fig_width, fig_height) for one panel + right colorbar."""
    lon_span = max(lon_max - lon_min, 1e-6)
    lat_span = max(lat_max - lat_min, 1e-6)
    panel_w  = float(np.clip(panel_h * lon_span / lat_span, 3.0, max_w))
    return panel_w + cbar_room, panel_h


def make_frame(lon2d, lat2d, values, title, date_str, cmap, vmin, vmax,
               cbar_label, out_path, dpi=150, quality=85,
               is_log=False, cbar_km=False, cbar_extend_minmax=False,
               boundaries=None):
    """
    Save one JPEG frame with the shared workflow style:
      - vertical colorbar on the right
      - axis labels Longitude/Latitude
      - aspect-driven figure size
      - extent from mesh boundaries ± padding (if boundaries provided),
        otherwise from the data grid bounds
      - optional mesh boundary overlay
    """
    # Use mesh extent + padding when available, else fall back to data bounds
    if boundaries is not None and "mesh_extent" in boundaries:
        ext = boundaries["mesh_extent"]
        lon_min = ext[0] - PADDING_LON; lon_max = ext[1] + PADDING_LON
        lat_min = ext[2] - PADDING_LAT; lat_max = ext[3] + PADDING_LAT
    else:
        lon_min, lon_max = float(lon2d.min()), float(lon2d.max())
        lat_min, lat_max = float(lat2d.min()), float(lat2d.max())
    fw, fh = _aspect_figsize(lon_min, lon_max, lat_min, lat_max)

    fig, ax = plt.subplots(figsize=(fw, fh), constrained_layout=True)

    kw = {"cmap": cmap, "shading": "auto", "rasterized": True}
    if is_log:
        pos = values[values > 0]
        lo  = float(np.percentile(pos, 2))  if vmin is None else vmin
        hi  = float(np.percentile(pos, 98)) if vmax is None else vmax
        kw["norm"] = LogNorm(vmin=lo, vmax=hi)
    else:
        if vmin is not None: kw["vmin"] = vmin
        if vmax is not None: kw["vmax"] = vmax

    pcm = ax.pcolormesh(lon2d, lat2d, values, **kw)

    cbar = fig.colorbar(pcm, ax=ax, location="right",
                        pad=0.02, fraction=0.046, shrink=0.9)
    cbar.set_label(cbar_label, fontsize=CBAR_FS)
    cbar.ax.tick_params(labelsize=TICK_FS)

    if cbar_km:
        cbar.formatter = matplotlib.ticker.FuncFormatter(
            lambda x, _: f"{x/1000:.1f}")
        cbar.update_ticks()

    if cbar_extend_minmax:
        clim = pcm.get_clim()
        cbar.ax.text(0.5, 1.02, f"{clim[1]:.1f}",
                     ha="center", va="bottom", fontsize=7,
                     transform=cbar.ax.transAxes)
        cbar.ax.text(0.5, -0.02, f"{clim[0]:.1f}",
                     ha="center", va="top", fontsize=7,
                     transform=cbar.ax.transAxes)

    _draw_boundaries(ax, boundaries)

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_xlabel("Longitude (°E)", fontsize=LABEL_FS)
    ax.set_ylabel("Latitude (°N)", fontsize=LABEL_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.set_aspect("equal")

    full_title = f"{date_str} — {title}" if date_str else title
    ax.set_title(full_title, fontsize=TITLE_FS, fontweight="bold", pad=10)

    fig.savefig(out_path, dpi=dpi, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": quality})
    plt.close(fig)


def make_frame_tripcolor(triangulation, values, title, out_path,
                         cbar_label=None, cmap="viridis",
                         vmin=None, vmax=None, is_elem=False,
                         is_log=False, cbar_km=False,
                         cbar_ticks=None, cbar_extend_minmax=False,
                         padding_lon=5.0, padding_lat=2.0,
                         dpi=300, quality=92, boundaries=None):
    """
    Save one JPEG frame using tripcolor (for mesh/unstructured grids).
    Same style as make_frame: vertical colorbar on the right.
    Extent uses mesh node bounds + padding (consistent with make_frame).
    Optional mesh boundary overlay.
    """
    mesh_lon_min = float(triangulation.x.min())
    mesh_lon_max = float(triangulation.x.max())
    mesh_lat_min = float(triangulation.y.min())
    mesh_lat_max = float(triangulation.y.max())
    fw, fh = _aspect_figsize(mesh_lon_min, mesh_lon_max,
                              mesh_lat_min, mesh_lat_max)

    fig, ax = plt.subplots(figsize=(fw, fh), constrained_layout=True)

    kw = {"cmap": cmap, "rasterized": True}
    if is_log:
        pos = values[values > 0]
        lo  = float(np.percentile(pos, 2))  if vmin is None else vmin
        hi  = float(np.percentile(pos, 98)) if vmax is None else vmax
        kw["norm"] = LogNorm(vmin=lo, vmax=hi)
    else:
        if vmin is not None: kw["vmin"] = vmin
        if vmax is not None: kw["vmax"] = vmax

    if is_elem:
        pcm = ax.tripcolor(triangulation, facecolors=values, **kw)
    else:
        pcm = ax.tripcolor(triangulation, values, shading="flat", **kw)

    cbar = fig.colorbar(pcm, ax=ax, location="right",
                        pad=0.02, fraction=0.046, shrink=0.9)
    if cbar_label:
        cbar.set_label(cbar_label, fontsize=CBAR_FS)
    cbar.ax.tick_params(labelsize=TICK_FS)

    if cbar_km:
        cbar.formatter = matplotlib.ticker.FuncFormatter(
            lambda x, _: f"{x/1000:.1f}")
        cbar.update_ticks()

    if cbar_ticks is not None:
        cbar.set_ticks(cbar_ticks)

    if cbar_extend_minmax:
        clim = pcm.get_clim()
        cbar.ax.text(0.5, 1.02, f"{clim[1]:.1f}",
                     ha="center", va="bottom", fontsize=7,
                     transform=cbar.ax.transAxes)
        cbar.ax.text(0.5, -0.02, f"{clim[0]:.1f}",
                     ha="center", va="top", fontsize=7,
                     transform=cbar.ax.transAxes)

    _draw_boundaries(ax, boundaries)

    ax.set_xlim(mesh_lon_min - padding_lon, mesh_lon_max + padding_lon)
    ax.set_ylim(mesh_lat_min - padding_lat, mesh_lat_max + padding_lat)
    ax.set_xlabel("Longitude (°E)", fontsize=LABEL_FS)
    ax.set_ylabel("Latitude (°N)", fontsize=LABEL_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=TITLE_FS, fontweight="bold", pad=10)

    jpeg_path = out_path.with_suffix(".jpg")
    fig.savefig(jpeg_path, dpi=dpi, format="jpeg",
                bbox_inches="tight", pil_kwargs={"quality": quality})
    plt.close(fig)
    return jpeg_path
