"""
models/schism/postprocess/plot_outputs.py
==========================================
Phase 5 step "plot_outputs" (SLURM array, one job per month).

For each month, reads SCHISM output NetCDF files from
R{ID}/R{ID}_YYYYMM/outputs/ and produces one animated GIF per variable
configured in postprocess.yaml. Each GIF shows the full temporal
evolution of the variable at the requested layer (surface, bottom, or
a specific sigma index) for that month.

SCHISM New I/O naming convention
---------------------------------
  out2d_{N}.nc           — 2D node-centred variables (elevation, met, etc.)
  temperature_{N}.nc     — 3D temperature (nodes × levels)
  salinity_{N}.nc        — 3D salinity
  horizontalVelX_{N}.nc  — 3D eastward velocity
  horizontalVelY_{N}.nc  — 3D northward velocity
  verticalVelocity_{N}.nc— 3D vertical velocity
  waterDensity_{N}.nc    — 3D water density
  diffusivity_{N}.nc     — 3D diffusivity
  viscosity_{N}.nc       — 3D viscosity

The mesh connectivity is read once from the first available out2d_{N}.nc
file (which always contains SCHISM_hgrid_face_nodes, SCHISM_hgrid_node_x,
SCHISM_hgrid_node_y, and depth).

Usage (called by SLURM via submit_plot_outputs.py):
    python -m workflow.models.schism.postprocess.plot_outputs
           --config <config_dir> --month YYYYMM
"""

import argparse
import gc
import sys
from pathlib import Path

from workflow.core.config import load_config, model_dir


# =============================================================================
# Mesh helpers
# =============================================================================

def _split_quads(face_nodes) -> "np.ndarray":
    """Split quad elements into two triangles. Returns int array (N_tri, 3)."""
    import numpy as np
    if face_nodes.shape[1] == 3:
        return face_nodes.astype(int)
    col4 = face_nodes[:, 3]
    with np.errstate(invalid="ignore"):
        is_tri = np.isnan(col4) | (col4 < 0)
    tris  = face_nodes[is_tri, :3]
    quads = face_nodes[~is_tri]
    if len(quads):
        tris = np.vstack([tris, quads[:, :3], quads[:, [0, 2, 3]]])
    return tris.astype(int)


def load_mesh(out2d_path: Path):
    """Return (x, y, triangles, depth, triangulation) from an out2d file.

    Uses matplotlib.tri.Triangulation (needed for tripcolor) so imports
    are deferred to keep this module lightweight at import time.
    """
    import xarray as xr
    import matplotlib.tri as mtri

    ds = xr.open_dataset(str(out2d_path), engine="h5netcdf",
                         drop_variables=_SAFE_DROP)
    raw  = np.nan_to_num(np.array(ds["SCHISM_hgrid_face_nodes"]), nan=0) - 1
    x    = np.array(ds["SCHISM_hgrid_node_x"])
    y    = np.array(ds["SCHISM_hgrid_node_y"])
    dep  = np.array(ds["depth"])
    ds.close()
    tris = _split_quads(raw)
    return x, y, tris, dep, mtri.Triangulation(x, y, tris)


# Variables that SCHISM puts in every output file but that we don't need
# for plotting — avoids unnecessary memory allocation.
_SAFE_DROP = [
    "vvel4.5", "uvel4.5",
    "vvel_bottom", "uvel_bottom", "vvel_surface", "uvel_surface",
    "salt_bottom", "temp_bottom",
    "precipitationRate", "evaporationRate",
    "windSpeedX", "windSpeedY", "windStressX", "windStressY",
    "dryFlagElement", "dryFlagSide", "dryFlagNode",
]


# =============================================================================
# Layer extraction
# =============================================================================

def _extract_layer(da, layer_spec):
    """Extract a 2-D (time, node) slice from a 3-D (time, node, level) array.

    layer_spec:
      "surface" — last (shallowest) sigma level, index -1
      "bottom"  — deepest wet sigma level per node (varies by location)
      int       — explicit 0-based sigma index
    """
    import numpy as np
    arr = np.array(da)  # shape: (time, node, level) or (time, level, node)

    # SCHISM New I/O: (time, nSCHISM_hgrid_node, nSCHISM_vgrid_layers)
    # dimension order may vary; find the level axis
    dims = list(da.dims)
    time_ax  = next((i for i, d in enumerate(dims) if "time" in d.lower()), 0)
    level_ax = next((i for i, d in enumerate(dims)
                     if "vgrid" in d.lower() or "layer" in d.lower()), -1)

    if level_ax == -1:
        # Not actually 3D — return as-is (shape: time, node)
        return arr

    if layer_spec == "surface":
        idx = -1
    elif layer_spec == "bottom":
        # Find deepest finite value along the level axis per node per timestep.
        # Use the first timestep's profile to determine the bottom level.
        sample = np.take(arr, 0, axis=time_ax)  # (node, level) or (level, node)
        valid  = np.isfinite(sample)
        # level_ax offset by 1 because time_ax was removed
        lax = level_ax - (1 if time_ax < level_ax else 0)
        nlevels = sample.shape[lax]
        depth_idx = np.where(
            valid.any(axis=lax),
            (np.where(valid, np.arange(nlevels).reshape(
                *([nlevels if i == lax else 1
                   for i in range(sample.ndim)])), -1)
             ).max(axis=lax),
            0
        )
        # Apply that static bottom-level index across all timesteps
        out = np.take(arr, depth_idx.clip(0), axis=level_ax)
        # np.take with array indices on one axis
        nt = arr.shape[time_ax]
        result = np.stack(
            [np.take(np.take(arr, t, axis=time_ax),
                     depth_idx.clip(0), axis=lax)
             for t in range(nt)], axis=0)
        return result
    else:
        idx = int(layer_spec)

    return np.take(arr, idx, axis=level_ax)


# =============================================================================
# GIF writer (same pattern as diagnostics/plot_hycom.py)
# =============================================================================

def _make_gif(frames, gif_path, fps):
    import imageio.v2 as imageio
    if not frames:
        print(f"  No frames for {gif_path.name}, skipping.")
        return
    images = [imageio.imread(str(f)) for f in frames]
    imageio.mimsave(str(gif_path), images, duration=1.0/fps, loop=0)
    for f in frames:
        Path(f).unlink(missing_ok=True)
    print(f"  GIF: {gif_path}")


# =============================================================================
# Main plotting worker for one month
# =============================================================================

def plot_outputs_month(cfg: dict, ym: str):
    """Generate output GIFs for one calendar month.

    Reads output files from R{ID}/R{ID}_YYYYMM/outputs/, writes GIFs to
    P{ID}/P{ID}_YYYYMM/.
    """
    import xarray as xr
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from workflow.core.plot_style import make_frame_tripcolor, read_mesh_boundaries

    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    rdir  = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
    pdir  = mdir / f"P{pid}" / f"P{pid}_{ym}"
    pdir.mkdir(parents=True, exist_ok=True)

    sentinel = pdir / "plot_outputs.done"
    if sentinel.exists():
        print(f"  plot_outputs: {ym} already complete. Skipping.")
        return

    var_cfgs = cfg.get("plot_outputs_vars", [])
    if not var_cfgs:
        print(f"  plot_outputs: no variables configured in postprocess.yaml. Skipping.")
        sentinel.touch()
        return

    stride   = int(cfg.get("plot_outputs_stride", 1))
    fps      = int(cfg.get("plot_outputs_fps", 4))
    dpi      = int(cfg.get("plot_outputs_dpi", 150))

    print(f"\n--- plot_outputs {ym} -> {pdir} ---")
    print(f"  outputs dir:  {rdir}")
    print(f"  stride:       {stride}  fps: {fps}  dpi: {dpi}")

    # --- Load mesh once from first available out2d file ---
    out2d_files = sorted(rdir.glob("out2d_*.nc"),
                         key=lambda p: int(p.stem.split("_")[1]))
    if not out2d_files:
        print(f"  ERROR: no out2d_*.nc files found in {rdir}. "
              f"Has the model run completed?")
        return
    x, y, tris, depth, triang = load_mesh(out2d_files[0])

    # --- Mesh boundaries (optional overlay) ---
    hgrid_ll  = mdir / "fix" / "hgrid.ll"
    hgrid_gr3 = mdir / "fix" / "hgrid.gr3"
    boundaries = None
    for hp in (hgrid_ll, hgrid_gr3):
        if hp.exists():
            try:
                boundaries = read_mesh_boundaries(hp)
            except Exception:
                pass
            break

    tmp_dir = pdir / "_frames_tmp"
    tmp_dir.mkdir(exist_ok=True)

    gif_count = 0

    for var_cfg in var_cfgs:
        prefix    = var_cfg.get("file_prefix", "")
        var_name  = var_cfg.get("var_name", "")
        label     = var_cfg.get("label", var_name)
        cmap      = var_cfg.get("cmap", "viridis")
        vmin_cfg  = var_cfg.get("vmin", None)
        vmax_cfg  = var_cfg.get("vmax", None)
        is_3d     = bool(var_cfg.get("is_3d", False))
        layer_spec= var_cfg.get("layer", "surface")
        isobaths  = var_cfg.get("isobaths", None)
        if isobaths is not None:
            isobaths = [float(v) for v in isobaths]

        # Collect and sub-sample output files for this variable
        nc_files = sorted(rdir.glob(f"{prefix}_*.nc"),
                          key=lambda p: int(p.stem.split("_")[1]))
        nc_files = nc_files[::stride]
        if not nc_files:
            print(f"  WARNING: no {prefix}_*.nc files found, skipping {var_name}.")
            continue

        print(f"  Variable: {var_name}  ({len(nc_files)} file(s), layer={layer_spec})")

        frames = []
        frame_idx = 0
        vmin_use, vmax_use = vmin_cfg, vmax_cfg

        for nc_path in nc_files:
            try:
                ds = xr.open_dataset(str(nc_path), engine="h5netcdf",
                                     drop_variables=_SAFE_DROP)
            except Exception as exc:
                print(f"    WARNING: could not open {nc_path.name}: {exc}")
                continue

            if var_name not in ds:
                ds.close()
                print(f"    WARNING: '{var_name}' not in {nc_path.name}, skipping.")
                continue

            da = ds[var_name]
            try:
                if is_3d:
                    values_all = _extract_layer(da, layer_spec)
                else:
                    values_all = np.array(da)

                times = ds["time"].values
                ds.close()

                for t_idx in range(values_all.shape[0]):
                    vals = values_all[t_idx]
                    if vals.ndim > 1:
                        vals = vals.ravel()[:len(x)]

                    # Determine color limits from first frame if auto
                    if vmin_use is None or vmax_use is None:
                        finite = vals[np.isfinite(vals)]
                        if finite.size > 0:
                            if vmin_use is None:
                                vmin_use = float(np.percentile(finite, 2))
                            if vmax_use is None:
                                vmax_use = float(np.percentile(finite, 98))
                        if vmin_use == vmax_use:
                            vmax_use = (vmin_use or 0.0) + 1e-6

                    # Format timestamp
                    try:
                        ts = str(np.datetime_as_string(times[t_idx], unit="h"))
                    except Exception:
                        ts = f"frame {frame_idx}"

                    title = f"{label} — {ts}"
                    if is_3d:
                        title += f" (layer: {layer_spec})"

                    fp = tmp_dir / f"{var_name}_frame_{frame_idx:05d}.jpg"
                    make_frame_tripcolor(
                        triang, vals, title=title, out_path=fp,
                        cbar_label=label, cmap=cmap,
                        vmin=vmin_use, vmax=vmax_use,
                        boundaries=boundaries, dpi=dpi,
                    )
                    frames.append(fp)
                    frame_idx += 1

            except Exception as exc:
                ds.close()
                print(f"    WARNING: error processing {nc_path.name}: {exc}")
                continue

            gc.collect()

        # Contour lines for depth isobaths (drawn on top of the last tripcolor
        # frame — we add them as overlays in make_frame_tripcolor via boundaries,
        # but isobaths are an extra layer). Since make_frame_tripcolor draws
        # boundaries internally, we add a separate depth-contour post-pass
        # by redrawing the last frame is unnecessary here; instead isobaths
        # are passed to make_frame_tripcolor below.
        # ── Actually: make_frame_tripcolor does not yet accept isobaths.
        # We extend it with a depth kwarg below. For now the overlay is
        # handled through the boundaries dict which covers coastlines only.
        # TODO: add isobath support to make_frame_tripcolor (separate PR).

        if frames:
            layer_tag = str(layer_spec) if is_3d else "2d"
            gif_name  = f"{var_name}_{layer_tag}_{ym}.gif"
            _make_gif(frames, pdir / gif_name, fps=fps)
            gif_count += 1
        else:
            print(f"  WARNING: no frames produced for {var_name}.")

        vmin_use, vmax_use = vmin_cfg, vmax_cfg  # reset for next variable
        gc.collect()

    # Clean up empty tmp dir
    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    if gif_count > 0:
        sentinel.touch()
        print(f"  {gif_count} GIF(s) written. Sentinel: {sentinel}")
    else:
        print(f"  WARNING: no GIFs produced for {ym}. Sentinel NOT written.")


# =============================================================================
# CLI entry point (invoked by SLURM via python -m ...plot_outputs)
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Plot SCHISM output GIFs for one month")
    parser.add_argument("--config", required=True,
                        help="Path to config/ directory")
    parser.add_argument("--month",  required=True,
                        help="YYYYMM string")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    plot_outputs_month(cfg, args.month)
