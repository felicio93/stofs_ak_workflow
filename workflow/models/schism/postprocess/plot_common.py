"""
models/schism/postprocess/plot_common.py
========================================
Shared helpers for all Phase 5 SCHISM output plotting.

Supports both:
  - New I/O (SCHISM standalone): separate per-variable files
    (out2d_N.nc, temperature_N.nc, etc.)
  - Old I/O (UFS-SCHISM): combined per-stack files (schout_N.nc)
    with different variable names (elev, temp, salt, hvel, etc.)

The OUTPUT_FORMAT is detected automatically from the run directory:
  - If out2d_*.nc files exist -> New I/O
  - If schout_*.nc files exist -> Old I/O

Variable name mapping (old I/O -> new I/O equivalent):
  elev              -> elevation
  temp              -> temperature
  salt              -> salinity
  hvel (ivs=2)      -> horizontalVelX (component 0) / horizontalVelY (component 1)
  vertical_velocity -> verticalVelocity
  wind_speed (ivs=2)-> windSpeedX / windSpeedY
  wind_stress(ivs=2)-> windStressX / windStressY
  water_density     -> waterDensity
  TKE               -> turbulentKineticEner
  mixing_length     -> mixingLength
  air_pressure      -> airPressure
  air_temperature   -> airTemperature
  specific_humidity -> specificHumidity
  solar_radiation   -> solarRadiation
  sensible_flux     -> sensibleHeat
  latent_heat       -> latentHeat
  upward_longwave   -> upwardLongwave
  downward_longwave -> downwardLongwave
  total_heat_flux   -> totalHeat
  evaporation       -> evaporationRate
  precipitation     -> precipitationRate
"""

from pathlib import Path

# Variables present in every output file that we never plot.
# NOTE: Do NOT add variables that appear in plot_outputs_vars or
# diag_run_vars here — they must remain accessible.
SAFE_DROP = [
    "vvel4.5", "uvel4.5",
    "vvel_bottom", "uvel_bottom", "vvel_surface", "uvel_surface",
    "salt_bottom", "temp_bottom",
    "dryFlagElement", "dryFlagSide", "dryFlagNode",
]

# =============================================================================
# Old I/O variable name mapping
# =============================================================================

# Maps old I/O variable names to New I/O equivalent names used in
# postprocess.yaml plot_outputs_vars / diag_run_vars.
OLD_IO_VAR_MAP = {
    "elev":              "elevation",
    "temp":              "temperature",
    "salt":              "salinity",
    "vertical_velocity": "verticalVelocity",
    "water_density":     "waterDensity",
    "TKE":               "turbulentKineticEner",
    "mixing_length":     "mixingLength",
    "diffusivity":       "diffusivity",
    "viscosity":         "viscosity",
    "air_pressure":      "airPressure",
    "air_temperature":   "airTemperature",
    "specific_humidity": "specificHumidity",
    "solar_radiation":   "solarRadiation",
    "sensible_flux":     "sensibleHeat",
    "latent_heat":       "latentHeat",
    "upward_longwave":   "upwardLongwave",
    "downward_longwave": "downwardLongwave",
    "total_heat_flux":   "totalHeat",
    "evaporation":       "evaporationRate",
    "precipitation":     "precipitationRate",
    # Vector fields — split into components (handled specially in extract_oldio)
    "hvel":              "horizontalVel",    # -> X/Y components at index 0/1
    "wind_speed":        "windSpeed",        # -> X/Y components
    "wind_stress":       "windStress",       # -> X/Y components
}

# Reverse map: New I/O name -> old I/O name (for looking up in schout_*.nc)
NEW_TO_OLD_IO_VAR = {v: k for k, v in OLD_IO_VAR_MAP.items()}
# Add vector component mappings
NEW_TO_OLD_IO_VAR.update({
    "horizontalVelX": "hvel",       # component index 0
    "horizontalVelY": "hvel",       # component index 1
    "windSpeedX":     "wind_speed",
    "windSpeedY":     "wind_speed",
    "windStressX":    "wind_stress",
    "windStressY":    "wind_stress",
})

# Which component index (0=x, 1=y) for vector variables
VECTOR_COMPONENT = {
    "horizontalVelX": 0, "horizontalVelY": 1,
    "windSpeedX":     0, "windSpeedY":     1,
    "windStressX":    0, "windStressY":    1,
}

# =============================================================================
# Output format detection
# =============================================================================

def detect_output_format(outputs_dir: Path) -> str:
    """Detect whether outputs are New I/O or Old I/O.

    Returns:
        'new'  : New I/O (out2d_*.nc, temperature_*.nc, etc.)
        'old'  : Old I/O (schout_*.nc with all variables combined)
        'none' : No output files found
    """
    outputs_dir = Path(outputs_dir)
    if list(outputs_dir.glob("out2d_*.nc")):
        return "new"
    if list(outputs_dir.glob("schout_*.nc")):
        return "old"
    return "none"

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


def load_mesh(nc_path: Path):
    """Return (x, y, depth, triangulation, is_tri) from a SCHISM output file.

    Works for both New I/O (out2d_*.nc) and Old I/O (schout_*.nc) since both
    formats store the same mesh variables:
        SCHISM_hgrid_node_x / SCHISM_hgrid_node_y
        SCHISM_hgrid_face_nodes
        depth
    """
    import numpy as np
    import xarray as xr
    import matplotlib.tri as mtri

    ds  = xr.open_dataset(str(nc_path), drop_variables=SAFE_DROP)
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
    """Reduce a 3-D SCHISM field to 2-D by extracting a vertical layer.

    Works for both New I/O (time, node, level) and Old I/O
    (time, node, level) dimension ordering. The level dimension is identified
    by name containing 'vgrid' or 'layer'.

    layer_spec:
      "surface" -> last (shallowest) sigma level (-1)
      "bottom"  -> deepest wet sigma level per node
      int       -> explicit 0-based sigma index
    """
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


def extract_oldio_var(ds, new_varname: str, layer_spec):
    """Extract a variable from an old I/O schout_*.nc dataset.

    Handles:
      - Simple variable renames (e.g. elev -> elevation)
      - Vector component extraction (e.g. hvel[:,:,:,0] -> horizontalVelX)
      - Layer extraction for 3D variables

    Parameters
    ----------
    ds : xarray.Dataset
        Opened schout_*.nc dataset.
    new_varname : str
        New I/O variable name (as used in postprocess.yaml).
    layer_spec : str or int
        Layer specification ("surface", "bottom", or int index).

    Returns
    -------
    numpy.ndarray of shape (time, nodes) or (time, elements)
    """
    import numpy as np

    old_varname = NEW_TO_OLD_IO_VAR.get(new_varname)
    if old_varname is None:
        # Try direct match (variable already in old I/O name)
        if new_varname in ds:
            da = ds[new_varname]
            return extract_layer(da, layer_spec) if da.ndim > 2 else np.array(da)
        raise KeyError(
            f"Variable '{new_varname}' not found in old I/O dataset. "
            f"Available: {list(ds.data_vars)}"
        )

    if old_varname not in ds:
        raise KeyError(
            f"Old I/O variable '{old_varname}' (for '{new_varname}') "
            f"not found in dataset. Available: {list(ds.data_vars)}"
        )

    da = ds[old_varname]

    # Handle vector fields: shape is (time, nodes, levels, 2) for 3D
    # or (time, nodes, 2) for 2D
    comp_idx = VECTOR_COMPONENT.get(new_varname)
    if comp_idx is not None:
        arr = np.array(da)
        # Identify the 'two' dimension (size 2)
        two_ax = next((i for i, s in enumerate(arr.shape) if s == 2), None)
        if two_ax is None:
            raise ValueError(
                f"Expected a 'two' dimension in {old_varname} for "
                f"component extraction, got shape {arr.shape}"
            )
        # Extract the component
        arr = np.take(arr, comp_idx, axis=two_ax)
        # Now arr is (time, nodes) or (time, nodes, levels)
        # Wrap back into a simple array for extract_layer
        import xarray as xr
        # Rebuild dims without the 'two' axis
        new_dims = [d for i, d in enumerate(da.dims) if i != two_ax]
        da_comp = xr.DataArray(arr, dims=new_dims)
        return extract_layer(da_comp, layer_spec) if da_comp.ndim > 2 \
            else np.array(da_comp)

    # Simple scalar variable
    if da.ndim > 2:
        return extract_layer(da, layer_spec)
    return np.array(da)

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
    """Assemble sorted frame JPEGs into a GIF."""
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
        print(f"  Removed {len(frame_paths)} individual frames.")

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
    """Return sorted list of <prefix>_N.nc files in outputs_dir (by N).

    For old I/O, 'prefix' is always 'schout' regardless of variable name.
    For new I/O, prefix is the variable-specific file prefix.
    """
    files = list(Path(outputs_dir).glob(f"{prefix}_*.nc"))
    # Exclude per-rank files (schout_000000_1.nc has 6-digit rank prefix)
    files = [f for f in files
             if not re.search(r"_\d{6}_\d+\.nc$", f.name)]
    return sorted(files, key=stack_number)


def list_oldio_stacks(outputs_dir: Path):
    """Return sorted list of combined schout_N.nc files for old I/O.

    Excludes per-rank files (schout_000000_1.nc etc.).
    """
    import re as _re
    files = [f for f in Path(outputs_dir).glob("schout_*.nc")
             if not _re.search(r"_\d{6}_\d+\.nc$", f.name)]
    return sorted(files, key=stack_number)


# Need re for list_output_stacks
import re
