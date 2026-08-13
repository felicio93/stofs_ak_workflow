"""
models/schism/postprocess/plot_common.py
========================================
Shared helpers for all Phase 5 SCHISM output plotting (diag_run, plot_outputs,
compare_sst):

  * SCHISM New I/O mesh loading (split_quads + matplotlib Triangulation)
  * vertical-layer extraction from 3-D fields (surface / bottom / index)
  * a single-panel tripcolor frame renderer with the shared workflow style
    and 200/2000 m isobath overlay
  * GIF assembly with optional frame retention
  * variable-spec parsing from postprocess.yaml
  * output-file discovery / time indexing

All heavy imports (numpy, xarray, matplotlib, imageio) are deferred to call
time so that importing this module (e.g. by the launcher on a login node) is
cheap and does not require the plotting environment.
"""

from pathlib import Path


# SCHISM New I/O variables present in every output file that we never plot;
# dropping them keeps memory down when opening the NetCDFs.
SAFE_DROP = [
    "vvel4.5", "uvel4.5",
    "vvel_bottom", "uvel_bottom", "vvel_surface", "uvel_surface",
    "salt_bottom", "temp_bottom",
    "precipitationRate", "evaporationRate",
    "windSpeedX", "windSpeedY", "windStressX", "windStressY",
    "dryFlagElement", "dryFlagSide", "dryFlagNode",
]


# =============================================================================
# Mesh
# =============================================================================

def split_quads(face_nodes):
    """Split quad elements into triangles.

    Returns (tris, is_tri):
      tris   : int array (N_tri, 3) of triangle vertex indices. Pure triangles
               come first, then each quad split into two triangles
               (0,1,2) and (0,2,3).
      is_tri : bool array (N_elem,) — True for elements that were already
               triangles, False for quads. Used to expand element-centered
               fields onto the split triangulation (a quad's value is repeated
               for both of its child triangles).
    """
    import numpy as np
    fn = np.array(face_nodes)
    if fn.shape[1] == 3:
        n = fn.shape[0]
        return fn.astype(int), np.ones(n, dtype=bool)
    col4 = fn[:, 3]
    with np.errstate(invalid="ignore"):
        is_tri = np.isnan(col4) | (col4 < 0)
    tris  = fn[is_tri, :3]
    quads = fn[~is_tri]
    if len(quads):
        tris = np.vstack([tris, quads[:, :3], quads[:, [0, 2, 3]]])
    return tris.astype(int), is_tri


def expand_elem_values(values, is_tri):
    """Map an element-centered field onto the split triangulation.

    The triangulation orders faces as: [pure-triangle elems] + [quad tri #1] +
    [quad tri #2]. An element-centered value array (length N_elem) must be
    reordered to match: triangle-element values first, then each quad value
    repeated for its two child triangles.
    """
    import numpy as np
    v = np.asarray(values)
    tri_vals  = v[is_tri]
    quad_vals = v[~is_tri]
    return np.concatenate([tri_vals, quad_vals, quad_vals])


def load_mesh(out2d_path: Path):
    """Return (x, y, depth, triangulation, is_tri) from a SCHISM out2d_*.nc file.

    out2d files always carry SCHISM_hgrid_face_nodes / _node_x / _node_y /
    depth, so the mesh (and its 200/2000 m isobaths) can be built once from
    any out2d stack. `is_tri` is returned so element-centered variables can be
    expanded onto the split triangulation via expand_elem_values().

    The NetCDF engine is auto-selected by xarray (h5netcdf or netcdf4); SCHISM
    New I/O output is HDF5/NETCDF4 and readable by either.
    """
    import numpy as np
    import xarray as xr
    import matplotlib.tri as mtri

    ds  = xr.open_dataset(str(out2d_path), drop_variables=SAFE_DROP)
    raw = np.nan_to_num(np.array(ds["SCHISM_hgrid_face_nodes"]), nan=0) - 1
    x   = np.array(ds["SCHISM_hgrid_node_x"])
    y   = np.array(ds["SCHISM_hgrid_node_y"])
    dep = np.array(ds["depth"])
    ds.close()

    tris, is_tri = split_quads(raw)
    dep  = np.where(np.isnan(dep), -9999.0, dep)
    return x, y, dep, mtri.Triangulation(x, y, tris), is_tri


# =============================================================================
# Vertical-layer extraction
# =============================================================================

def extract_layer(da, layer_spec):
    """Reduce a 3-D SCHISM field (time, node, level) to (time, node).

    layer_spec:
      "surface" -> last (shallowest) sigma level (-1)
      "bottom"  -> deepest wet sigma level per node
      int       -> explicit 0-based sigma index
    A field with no level dimension is returned unchanged.
    """
    import numpy as np

    dims = list(da.dims)
    level_ax = next((i for i, d in enumerate(dims)
                     if "vgrid" in d.lower() or "layer" in d.lower()), None)
    arr = np.array(da)
    if level_ax is None:
        return arr

    time_ax = next((i for i, d in enumerate(dims) if "time" in d.lower()), 0)

    if layer_spec == "surface":
        return np.take(arr, -1, axis=level_ax)

    if layer_spec == "bottom":
        # Determine, per node, the deepest finite level from the first frame.
        first = np.take(arr, 0, axis=time_ax)          # drop time
        lax   = level_ax - (1 if time_ax < level_ax else 0)
        nlev  = first.shape[lax]
        valid = np.isfinite(first)
        idx_shape = [1] * first.ndim
        idx_shape[lax] = nlev
        level_idx = np.arange(nlev).reshape(idx_shape)
        deepest = np.where(valid, level_idx, -1).max(axis=lax).clip(0)
        # Apply that static bottom index across all timesteps.
        nt = arr.shape[time_ax]
        return np.stack(
            [np.take_along_axis(
                np.take(arr, t, axis=time_ax),
                np.expand_dims(deepest, axis=lax), axis=lax
             ).squeeze(axis=lax)
             for t in range(nt)],
            axis=0,
        )

    return np.take(arr, int(layer_spec), axis=level_ax)


# =============================================================================
# Frame rendering (single tripcolor panel + isobaths)
# =============================================================================

def render_frame(triang, values, title, out_path, cbar_label,
                 cmap="jet", vmin=None, vmax=None,
                 depth=None, isobaths=(200, 2000),
                 dpi=150, quality=90, boundaries=None, is_elem=False):
    """Render one JPEG frame using the shared workflow style plus isobaths.

    Delegates the panel/colorbar/style/isobaths to
    core.plot_style.make_frame_tripcolor. Returns the written JPEG path.

    is_elem: True for element-centered fields (values already expanded onto the
    split triangulation via expand_elem_values) — drawn as facecolors.
    """
    import numpy as np
    from workflow.core.plot_style import make_frame_tripcolor

    return make_frame_tripcolor(
        triang, np.asarray(values), title=title, out_path=Path(out_path),
        cbar_label=cbar_label, cmap=cmap, vmin=vmin, vmax=vmax,
        dpi=dpi, quality=quality, boundaries=boundaries,
        depth=depth, isobaths=isobaths, is_elem=is_elem,
    )


def robust_limits(values, vmin, vmax):
    """Fill None color limits from the 2nd/98th percentiles of finite data."""
    import numpy as np
    arr = np.asarray(values)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return (0.0, 1.0)
    lo = float(np.percentile(finite, 2))  if vmin is None else vmin
    hi = float(np.percentile(finite, 98)) if vmax is None else vmax
    if lo == hi:
        hi = lo + 1e-6
    return (lo, hi)


# =============================================================================
# GIF assembly
# =============================================================================

def assemble_gif(frame_paths, gif_path: Path, fps: int = 4,
                 keep_frames: bool = True):
    """Assemble sorted frame JPEGs into a GIF. Optionally delete frames."""
    import imageio.v2 as imageio
    frame_paths = sorted(str(p) for p in frame_paths)
    if not frame_paths:
        print(f"  No frames for {gif_path.name}; skipping GIF.")
        return
    images = [imageio.imread(f) for f in frame_paths]
    imageio.mimsave(str(gif_path), images, duration=1.0 / max(fps, 1), loop=0)
    print(f"  GIF: {gif_path}  ({len(images)} frames)")
    if not keep_frames:
        for f in frame_paths:
            Path(f).unlink(missing_ok=True)
        print(f"  Removed {len(frame_paths)} individual frames "
              f"(keep_frames=false).")


# =============================================================================
# Variable-spec + output-file helpers
# =============================================================================

def var_spec(entry: dict) -> dict:
    """Normalise a postprocess.yaml variable entry with defaults."""
    return {
        "file_prefix": entry.get("file_prefix", ""),
        "var_name":    entry.get("var_name", ""),
        "label":       entry.get("label", entry.get("var_name", "")),
        "cmap":        entry.get("cmap", "jet"),
        "vmin":        entry.get("vmin", None),
        "vmax":        entry.get("vmax", None),
        "is_3d":       bool(entry.get("is_3d", False)),
        "layer":       entry.get("layer", "surface"),
        "loc":         str(entry.get("loc", "node")).lower(),  # node | elem
    }


def stack_number(nc_path: Path) -> int:
    """Return the trailing stack integer N from <prefix>_N.nc."""
    return int(Path(nc_path).stem.split("_")[-1])


def list_output_stacks(outputs_dir: Path, prefix: str):
    """Return sorted list of <prefix>_N.nc files in outputs_dir (by N)."""
    files = list(Path(outputs_dir).glob(f"{prefix}_*.nc"))
    return sorted(files, key=stack_number)
