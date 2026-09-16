"""
models/ufs_schism/preprocess/gen_esmf_mesh.py
=============================================
Generate ESMF mesh file from the DATM atmospheric forcing grid.

Reads the DATM forcing NetCDF for one group, extracts the longitude /
latitude coordinate arrays, builds ESMF-format cell-centre and node
coordinates, and writes datm_esmf_mesh.nc alongside the forcing file.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

Sentinel: I{ID}_{group_id}/forcing/gen_esmf_mesh.done
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import netCDF4 as nc4

from workflow.core.config import (
    load_config,
    model_dir,
    list_groups,
)


# =============================================================================
# Path helpers
# =============================================================================

def _datm_nc_path(cfg: dict, group_id: str) -> Path:
    """Return the path to the DATM forcing NetCDF for this group."""
    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    subdir = str(cfg.get("datm_subdir", "forcing"))
    tmpl   = str(cfg.get("datm_filename_template",
                          "datm_{YYYYMM}.nc"))
    name   = tmpl.replace("{YYYYMM}", group_id)
    return (mdir / f"I{pid}" / f"I{pid}_{group_id}"
            / subdir / name)


def _mesh_out_path(in_path: Path) -> Path:
    return in_path.parent / "datm_esmf_mesh.nc"


def _sentinel_path(out_path: Path) -> Path:
    return out_path.parent / "gen_esmf_mesh.done"


# =============================================================================
# Grid helper
# =============================================================================

def _cell_edges(centers: np.ndarray) -> np.ndarray:
    """Compute cell-edge coordinates from cell-centre coordinates."""
    centers = np.asarray(centers, dtype=float)
    if centers.size < 2:
        raise ValueError(
            "need at least 2 grid points to build cell edges")
    edges        = np.empty(centers.size + 1, dtype=float)
    edges[1:-1]  = 0.5 * (centers[:-1] + centers[1:])
    edges[0]     = centers[0]  - 0.5 * (centers[1] - centers[0])
    edges[-1]    = centers[-1] + 0.5 * (centers[-1] - centers[-2])
    return edges


# =============================================================================
# Per-group entry point
# =============================================================================

def gen_esmf_mesh(cfg: dict, group_id: str):
    """Generate datm_esmf_mesh.nc for one group.

    Parameters
    ----------
    cfg      : merged workflow config dict
    group_id : group identifier (YYYYMM or YYYYMMDD)
    """
    datm_path = _datm_nc_path(cfg, group_id)
    out_path  = _mesh_out_path(datm_path)
    sentinel  = _sentinel_path(out_path)

    if (sentinel.exists()
            and out_path.exists()
            and out_path.stat().st_size > 0):
        print(f"  gen_esmf_mesh: {group_id} already complete. "
              f"Skipping.")
        return

    if not datm_path.exists():
        print(f"ERROR: DATM forcing file not found: {datm_path}")
        print(f"  Run gen_datm for group {group_id} first.")
        sys.exit(1)

    print(f"--- gen_esmf_mesh {group_id} -> {out_path} ---")

    with nc4.Dataset(str(datm_path)) as ds:
        lon2d = ds.variables["longitude"][:]
        lat2d = ds.variables["latitude"][:]

    # 1-D coordinate arrays from the 2-D grid
    lons = lon2d[0, :]
    lats = lat2d[:, 0]

    nx = len(lons)
    ny = len(lats)

    n_elements = nx * ny

    lon_edges = _cell_edges(lons)
    lat_edges = _cell_edges(lats)

    nxe = nx + 1
    nye = ny + 1
    n_nodes = nxe * nye

    with nc4.Dataset(str(out_path), "w",
                     format="NETCDF4") as nc:

        # --- Dimensions ---
        nc.createDimension("nodeCount",        n_nodes)
        nc.createDimension("elementCount",     n_elements)
        nc.createDimension("maxNodePElement",  4)
        nc.createDimension("coordDim",         2)

        # --- Node coordinates ---
        node_coords = nc.createVariable(
            "nodeCoords", "f8", ("nodeCount", "coordDim"))
        node_coords.units = "degrees"
        node_coords[:] = np.column_stack([
            np.tile(lon_edges, nye),
            np.repeat(lat_edges, nxe),
        ])

        # --- Element connectivity ---
        # South-West corner index (1-based) for each element
        jj, ii = np.meshgrid(
            np.arange(ny), np.arange(nx), indexing="ij")
        sw = (jj * nxe + ii + 1).ravel()

        conn = np.empty((n_elements, 4), dtype=np.int32)
        conn[:, 0] = sw            # SW
        conn[:, 1] = sw + 1        # SE
        conn[:, 2] = sw + nxe + 1  # NE
        conn[:, 3] = sw + nxe      # NW

        elem_conn = nc.createVariable(
            "elementConn", "i4",
            ("elementCount", "maxNodePElement"))
        elem_conn.long_name = (
            "Node indices that define the element connectivity")
        elem_conn.start_index = 1
        elem_conn[:] = conn

        # --- Number of nodes per element (always 4) ---
        num_conn = nc.createVariable(
            "numElementConn", "i4", ("elementCount",))
        num_conn[:] = 4

        # --- Element mask (all valid) ---
        elem_mask = nc.createVariable(
            "elementMask", "i4", ("elementCount",))
        elem_mask[:] = np.ones(n_elements, dtype=np.int32)

        # --- Element centre coordinates ---
        center_coords = nc.createVariable(
            "centerCoords", "f8",
            ("elementCount", "coordDim"))
        center_coords.units = "degrees"
        center_coords[:] = np.column_stack([
            np.tile(lons, ny),
            np.repeat(lats, nx),
        ])

        # --- Global attributes ---
        nc.gridType = "unstructured"
        nc.title    = "ESMF mesh for DATM atmospheric forcing"

    sentinel.touch()
    print(f"  Wrote {out_path}  "
          f"(elements={n_elements}, nodes={n_nodes})")
    print(f"  Sentinel: {sentinel}")


# =============================================================================
# Batch entry point (all groups)
# =============================================================================

def run_gen_esmf_mesh(cfg: dict):
    """Generate datm_esmf_mesh.nc for every group."""
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_esmf_mesh: {groups[0]} -> {groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"{'='*60}\n")

    for group_id in groups:
        try:
            gen_esmf_mesh(cfg, group_id)
        except SystemExit:
            failed.append(group_id)
        except Exception as exc:
            print(f"  ERROR {group_id}: {exc}")
            failed.append(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_esmf_mesh complete. No failures.")
    else:
        print(f"  gen_esmf_mesh complete with "
              f"{len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
    print(f"{'='*60}\n")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate ESMF mesh file from DATM "
                    "forcing grid for a given group.")
    parser.add_argument("--config", required=True)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument(
        "--group", dest="group_id",
        help="Group ID (YYYYMM or YYYYMMDD)")
    grp.add_argument(
        "--month", dest="group_id",
        help="Group ID — legacy alias for --group")
    args = parser.parse_args()
    cfg  = load_config(Path(args.config))
    gen_esmf_mesh(cfg, args.group_id)
