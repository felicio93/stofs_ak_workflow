"""
core/mesh_parser.py
==============
Shared SCHISM gr3 mesh parsing utilities.

All functions in this module work with any valid .gr3 file — hgrid.gr3,
hgrid.ll, estuary.gr3, shapiro.gr3, albedo.gr3, etc. The fourth column in
the node block is always treated as a generic scalar VALUE (depth, flag,
coefficient, etc.); the caller decides what it means.

gr3 file format:
    title
    ne  np
    node_id  lon  lat  value       (np lines)
    elem_id  nverts  n1 n2 n3 [n4] (ne lines)
    nope                            <- number of open boundaries
    neta                            <- total open boundary nodes (skipped)
      nond_i                        <- node count for open boundary i
      node_id                       <- one per line
      ...
    nland                           <- number of land/island boundaries
    ntotal                          <- total land boundary nodes (skipped)
      nond_j  type_flag             <- node count + type (0=land, 1=island)
      node_id                       <- one per line
      ...

Public API
----------
read_nodes(path)
    -> node_ids (np,), lons (np,), lats (np,), values (np,)

read_elements(path)
    -> elem_ids (ne,), connectivity list[(nverts,)]

read_element_centroids(path)
    -> elem_ids (ne,), centroids (ne, 2)  [lon, lat]

read_open_boundaries(path)
    -> list of (n_nodes, lons, lats)  one tuple per open boundary segment

read_land_boundaries(path)
    -> list of (lons, lats, type_flag)  one tuple per land/island boundary

read_mesh_boundaries(path)
    -> dict {
         "open":        [(lons, lats), ...],
         "land":        [(lons, lats), ...],
         "island":      [(lons, lats), ...],
         "mesh_extent": [lon_min, lon_max, lat_min, lat_max],
       }
"""

from pathlib import Path
import numpy as np


# =============================================================================
# Internal helpers
# =============================================================================

def _read_nodes_and_ne(f):
    """
    Read title, header, and node block from an already-open gr3 file.
    Returns (ne, node_ids, lons, lats, values).
    File pointer is left immediately after the last node line.
    """
    f.readline()                             # title (ignored)
    ne, np_nodes = map(int, f.readline().split())

    node_ids = np.empty(np_nodes, dtype=np.int64)
    lons     = np.empty(np_nodes, dtype=np.float64)
    lats     = np.empty(np_nodes, dtype=np.float64)
    values   = np.empty(np_nodes, dtype=np.float64)

    for i in range(np_nodes):
        parts       = f.readline().split()
        node_ids[i] = int(parts[0])
        lons[i]     = float(parts[1])
        lats[i]     = float(parts[2])
        values[i]   = float(parts[3])

    return ne, node_ids, lons, lats, values


def _skip_elements(f, ne):
    """Advance the file pointer past the ne element lines."""
    for _ in range(ne):
        f.readline()


def _read_boundary_block(f, lons, lats):
    """
    Read one boundary block (open or land) from the current file position.
    Returns (open_bnds, land_bnds, island_bnds) where each entry is a
    tuple of (lons_array, lats_array [, type_flag]).
    Expects the file pointer to be at the 'nope' line.
    """
    open_bnds   = []
    land_bnds   = []
    island_bnds = []

    # Build a 0-based lookup: node_id - 1 → index in lons/lats arrays.
    # node_ids are 1-based and dense, so direct index arithmetic works.
    def _coords(ids_arr):
        idx = ids_arr - 1        # 1-based → 0-based
        return lons[idx], lats[idx]

    # --- Open boundaries ---
    line = f.readline()
    if not line:
        return open_bnds, land_bnds, island_bnds
    nope = int(line.split()[0])
    f.readline()                 # total open bnd nodes (ignored)
    for _ in range(nope):
        nond    = int(f.readline().split()[0])
        ids_arr = np.array([int(f.readline()) for _ in range(nond)],
                           dtype=np.int64)
        lo, la  = _coords(ids_arr)
        open_bnds.append((lo, la))

    # --- Land / island boundaries ---
    line = f.readline()
    if not line:
        return open_bnds, land_bnds, island_bnds
    nland = int(line.split()[0])
    f.readline()                 # total land bnd nodes (ignored)
    for _ in range(nland):
        header    = f.readline().split()
        nond      = int(header[0])
        type_flag = int(header[1])
        ids_arr   = np.array([int(f.readline()) for _ in range(nond)],
                             dtype=np.int64)
        lo, la    = _coords(ids_arr)
        if type_flag == 0:
            land_bnds.append((lo, la))
        else:
            # Close island polygon
            lo = np.append(lo, lo[0])
            la = np.append(la, la[0])
            island_bnds.append((lo, la))

    return open_bnds, land_bnds, island_bnds


# =============================================================================
# Public API
# =============================================================================

def read_nodes(path: Path):
    """
    Read node coordinates and scalar values from any .gr3 file.

    Returns
    -------
    node_ids : ndarray (np,)  int64   — 1-based node IDs
    lons     : ndarray (np,)  float64 — longitude (degrees east)
    lats     : ndarray (np,)  float64 — latitude  (degrees north)
    values   : ndarray (np,)  float64 — fourth column (depth, flag, etc.)
    """
    with open(path) as f:
        _, node_ids, lons, lats, values = _read_nodes_and_ne(f)
    return node_ids, lons, lats, values


def read_elements(path: Path):
    """
    Read element connectivity from a .gr3 file.

    Returns
    -------
    elem_ids     : ndarray (ne,)  int64    — 1-based element IDs
    connectivity : list of ndarray         — each entry is a 1-based node-ID
                   array of length 3 (triangle) or 4 (quad)
    """
    with open(path) as f:
        ne, _, _, _, _ = _read_nodes_and_ne(f)

        elem_ids     = np.empty(ne, dtype=np.int64)
        connectivity = []
        for i in range(ne):
            parts         = f.readline().split()
            elem_ids[i]   = int(parts[0])
            nverts        = int(parts[1])
            nodes         = np.array([int(x) for x in parts[2:2 + nverts]],
                                     dtype=np.int64)
            connectivity.append(nodes)

    return elem_ids, connectivity


def read_element_centroids(path: Path):
    """
    Compute element centroids from node coordinates.

    Centroid = arithmetic mean of the lon/lat of all vertices.
    Works correctly for the Alaska 0–360° domain; no circular-mean needed
    because the domain is far from the 0°/360° wrap.

    Returns
    -------
    elem_ids  : ndarray (ne,)    int64    — 1-based element IDs
    centroids : ndarray (ne, 2)  float64  — columns [lon, lat]
    """
    with open(path) as f:
        ne, _, lons, lats, _ = _read_nodes_and_ne(f)

        elem_ids  = np.empty(ne, dtype=np.int64)
        centroids = np.empty((ne, 2), dtype=np.float64)

        for i in range(ne):
            parts       = f.readline().split()
            elem_ids[i] = int(parts[0])
            nverts      = int(parts[1])
            idx         = np.array([int(x) - 1 for x in parts[2:2 + nverts]])
            centroids[i, 0] = lons[idx].mean()
            centroids[i, 1] = lats[idx].mean()

    return elem_ids, centroids


def read_open_boundaries(path: Path):
    """
    Read open boundary segments from a .gr3 file.

    Returns
    -------
    list of (n_nodes, lons, lats)
        One tuple per open boundary segment, in order.
        n_nodes : int
        lons    : ndarray (n_nodes,)  float64
        lats    : ndarray (n_nodes,)  float64
    """
    with open(path) as f:
        ne, _, lons, lats, _ = _read_nodes_and_ne(f)
        _skip_elements(f, ne)
        open_bnds, _, _ = _read_boundary_block(f, lons, lats)

    return [(len(lo), lo, la) for lo, la in open_bnds]


def read_land_boundaries(path: Path):
    """
    Read land and island boundary segments from a .gr3 file.

    Returns
    -------
    list of (lons, lats, type_flag)
        type_flag == 0  : mainland coast
        type_flag == 1  : island (polygon is closed — first == last point)
    """
    with open(path) as f:
        ne, _, lons, lats, _ = _read_nodes_and_ne(f)
        _skip_elements(f, ne)
        _, land_bnds, island_bnds = _read_boundary_block(f, lons, lats)

    result = []
    for lo, la in land_bnds:
        result.append((lo, la, 0))
    for lo, la in island_bnds:
        result.append((lo, la, 1))
    return result


def read_mesh_boundaries(path: Path) -> dict:
    """
    Read all boundary segments and mesh extent from a .gr3 file.

    Returns
    -------
    dict with keys:
        "open"        : list of (lons, lats)  — open boundary polylines
        "land"        : list of (lons, lats)  — mainland coast polylines
        "island"      : list of (lons, lats)  — island closed polygons
        "mesh_extent" : [lon_min, lon_max, lat_min, lat_max]
    """
    with open(path) as f:
        ne, _, lons, lats, _ = _read_nodes_and_ne(f)
        _skip_elements(f, ne)
        open_bnds, land_bnds, island_bnds = _read_boundary_block(f, lons, lats)

    mesh_extent = [float(lons.min()), float(lons.max()),
                   float(lats.min()),  float(lats.max())]

    return {
        "open":        open_bnds,
        "land":        land_bnds,
        "island":      island_bnds,
        "mesh_extent": mesh_extent,
    }
