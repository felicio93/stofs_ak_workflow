import argparse
import sys
from pathlib import Path

import numpy as np
import netCDF4 as nc4

from workflow.core.config import load_config, model_dir

def _datm_in_path(cfg: dict, ym: str) -> Path:
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    subdir = str(cfg.get("datm_subdir", "forcing"))
    tmpl = str(cfg.get("datm_filename_template", "datm_{YYYYMM}.nc"))
    name = tmpl.replace("{YYYYMM}", ym)
    return mdir / f"I{pid}" / f"I{pid}_{ym}" / subdir / name

def _mesh_out_path(in_path: Path) -> Path:
    return in_path.parent / "datm_esmf_mesh.nc"

def _sentinel_path(out_path: Path) -> Path:
    return out_path.parent / "gen_esmf_mesh.done"

def _cell_edges(centers: np.ndarray) -> np.ndarray:
    centers = np.asarray(centers, dtype=float)
    if centers.size < 2:
        raise ValueError("need at least 2 grid points to build cell edges")
    edges = np.empty(centers.size + 1, dtype=float)
    edges[1:-1] = 0.5 * (centers[:-1] + centers[1:])
    edges[0] = centers[0] - 0.5 * (centers[1] - centers[0])
    edges[-1] = centers[-1] + 0.5 * (centers[-1] - centers[-2])
    return edges

def gen_esmf_mesh(cfg: dict, ym: str):
    datm_path = _datm_in_path(cfg, ym)
    out_path = _mesh_out_path(datm_path)
    sentinel = _sentinel_path(out_path)

    if sentinel.exists() and out_path.exists() and out_path.stat().st_size > 0:
        print(f"  gen_esmf_mesh: {ym} already complete. Skipping.")
        return

    if not datm_path.exists():
        print(f"ERROR: DATM forcing file not found: {datm_path}")
        sys.exit(1)

    print(f"--- gen_esmf_mesh {ym} -> {out_path} ---")

    with nc4.Dataset(datm_path) as ds:
        lon2d = ds.variables["longitude"][:]
        lat2d = ds.variables["latitude"][:]
    
    lons = lon2d[0, :]
    lats = lat2d[:, 0]

    nx = len(lons)
    ny = len(lats)
    n_elements = nx * ny
    lon_edges = _cell_edges(lons)
    lat_edges = _cell_edges(lats)
    nxe, nye = nx + 1, ny + 1
    n_nodes = nxe * nye

    with nc4.Dataset(out_path, "w", format="NETCDF4") as nc:
        nc.createDimension("nodeCount", n_nodes)
        nc.createDimension("elementCount", n_elements)
        nc.createDimension("maxNodePElement", 4)
        nc.createDimension("coordDim", 2)

        node_coords = nc.createVariable("nodeCoords", "f8", ("nodeCount", "coordDim"))
        node_coords.units = "degrees"
        node_coords[:] = np.column_stack([
            np.tile(lon_edges, nye),
            np.repeat(lat_edges, nxe)
        ])

        elem_conn = nc.createVariable("elementConn", "i4", ("elementCount", "maxNodePElement"))
        elem_conn.long_name = "Node indices that define the element connectivity"
        elem_conn.start_index = 1
        jj, ii = np.meshgrid(np.arange(ny), np.arange(nx), indexing="ij")
        sw = (jj * nxe + ii + 1).ravel()
        conn = np.empty((n_elements, 4), dtype=np.int32)
        conn[:, 0] = sw
        conn[:, 1] = sw + 1
        conn[:, 2] = sw + nxe + 1
        conn[:, 3] = sw + nxe
        elem_conn[:] = conn

        num_conn = nc.createVariable("numElementConn", "i4", ("elementCount",))
        num_conn[:] = 4

        elem_mask = nc.createVariable("elementMask", "i4", ("elementCount",))
        elem_mask[:] = np.ones(n_elements, dtype=np.int32)

        center_coords = nc.createVariable("centerCoords", "f8", ("elementCount", "coordDim"))
        center_coords.units = "degrees"
        center_coords[:] = np.column_stack([
            np.tile(lons, ny),
            np.repeat(lats, nx)
        ])

        nc.gridType = "unstructured"
        nc.title = "ESMF mesh for DATM atmospheric forcing"

    sentinel.touch()
    print(f"  Wrote {out_path} (elements={n_elements}, nodes={n_nodes})")
    print(f"  Sentinel: {sentinel}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate ESMF mesh file from DATM forcing grid")
    parser.add_argument("--config", required=True)
    parser.add_argument("--month", required=True, help="YYYYMM")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    gen_esmf_mesh(cfg, args.month)
