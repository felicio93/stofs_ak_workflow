"""
models/schism/postprocess/plot_common.py
========================================
Shared helpers for all Phase 5 SCHISM output plotting (diag_run, plot_outputs,
compare_sst).
"""

from pathlib import Path

# Variables present in every out2d file that are never configured as plot
# targets in postprocess.yaml. Dropping them reduces memory when opening
# NetCDFs with xarray.
#
# IMPORTANT: Do NOT add variables that appear in plot_outputs_vars or
# diag_run_vars here. precipitationRate, evaporationRate, windSpeedX,
# windSpeedY, windStressX, windStressY are valid plot targets and must NOT
# be dropped.
SAFE_DROP = [
    # Diagnostic velocity at specific sigma levels — use horizontalVelX/Y
    # from the 3D files with layer="surface"/"bottom" instead.
    "vvel4.5", "uvel4.5",
    "vvel_bottom", "uvel_bottom", "vvel_surface", "uvel_surface",
    # Bottom/surface scalar diagnostics — use 3D variables with layer selector.
    "salt_bottom", "temp_bottom",
    # Wetting/drying integer flag fields — not useful for standard plots.
    "dryFlagElement", "dryFlagSide", "dryFlagNode",
]

# =============================================================================
# Mesh
# =============================================================================

def split_quads(face_nodes):
    """Split quad elements into triangles for matplotlib Triangulation."""
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
    """Map an element-centered field onto the split triangulation."""
    import numpy as np
    v = np.asarray(values)
    tri_vals  = v[is_tri]
    quad_vals = v[~is_tri]
    return np.concatenate([tri_vals, quad_vals, quad_vals])


def load_mesh(out2d_path: Path):
    """Return (x, y, depth, triangulation, is_tri) from a SCHISM out2d_*.nc."""
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
    dep = np.where(np.isnan(dep), -9999.0, dep)
    return x, y, dep, mtri.Triangulation(x, y, tris), is_tri

# =============================================================================
# Vertical-layer extraction
# =============================================================================

def extract_layer(da, layer_spec):
    """Reduce a 3-D SCHISM field (time, node, level) to (time, node)."""
    import numpy as np

    dims     = list(da.dims)
    level_ax = next((i for i, d in enumerate(dims)
                     if "vgrid" in d.lower() or "layer" in d.lower()), None)
    arr = np.array(da)
    if level_ax is None:
        return arr

    time_ax = next((i for i, d in enumerate(dims)
                    if "time" in d.lower()), 0)

    if layer_spec == "surface":
        return np.take(arr, -1, axis=level_ax)

    if layer_spec == "bottom":
        first = np.take(arr, 0, axis=time_ax)
        lax   = level_ax - (1 if time_ax < level_ax else 0)
        nlev  = first.shape[lax]
        valid = np.isfinite(first)
        idx_shape = [1] * first.ndim
        idx_shape[lax] = nlev
        level_idx = np.arange(nlev).reshape(idx_shape)
        deepest = np.where(valid, level_idx, -1).max(axis=lax).clip(0)
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
# Frame rendering
# =============================================================================

def render_frame(triang, values, title, out_path, cbar_label,
                 cmap="jet", vmin=None, vmax=None,
                 depth=None, isobaths=(200, 2000),
                 dpi=150, quality=90, boundaries=None, is_elem=False):
    """Render one JPEG frame using the shared workflow style plus isobaths."""
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
    arr    = np.asarray(values)
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
        "loc":         str(entry.get("loc", "node")).lower(),
    }


def stack_number(nc_path: Path) -> int:
    """Return the trailing stack integer N from <prefix>_N.nc."""
    return int(Path(nc_path).stem.split("_")[-1])


def list_output_stacks(outputs_dir: Path, prefix: str):
    """Return sorted list of <prefix>_N.nc files in outputs_dir (by N)."""
    files = list(Path(outputs_dir).glob(f"{prefix}_*.nc"))
    return sorted(files, key=stack_number)
