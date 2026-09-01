"""
models/schism/postprocess/plot_outputs.py
==========================================
Phase 5 step "plot_outputs" — full-run SCHISM field GIFs.

Supports both New I/O (SCHISM standalone) and Old I/O (UFS-SCHISM).
Output format is detected automatically from the run directory.
"""

import argparse
import gc
import sys
from pathlib import Path

from workflow.core.config import load_config, list_months, model_dir
from workflow.models.schism.postprocess import plot_common as pc

# =============================================================================
# Shared paths / mesh helpers
# =============================================================================

def _frames_dir(cfg) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_plot_outputs" / "frames"


def _gif_dir(cfg) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_plot_outputs"


def _find_any_output_file(cfg):
    """Return the first output file across all run months (for mesh loading).

    Works for both New I/O (returns first out2d_*.nc) and Old I/O
    (returns first combined schout_*.nc).
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    for ym in list_months(cfg):
        outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
        # New I/O
        stacks = pc.list_output_stacks(outputs, "out2d")
        if stacks:
            return stacks[0]
        # Old I/O
        stacks = pc.list_oldio_stacks(outputs)
        if stacks:
            return stacks[0]
    return None


def _in_date_range(cfg, t) -> bool:
    import numpy as np
    start = cfg.get("plot_outputs_start")
    end   = cfg.get("plot_outputs_end")
    if start:
        if np.datetime64(t) < np.datetime64(str(start)):
            return False
    if end:
        if np.datetime64(t) > np.datetime64(str(end)) + np.timedelta64(1, "D"):
            return False
    return True


def _on_cadence(t, every_hours: int) -> bool:
    import numpy as np
    if every_hours <= 0:
        return True
    hours = (np.datetime64(t) - np.datetime64(str(np.datetime64(t, "D")))) \
        / np.timedelta64(1, "h")
    return (round(float(hours)) % every_hours) == 0


def _load_boundaries(cfg):
    from workflow.core.plot_style import read_mesh_boundaries
    mdir = model_dir(cfg)
    for hp in (mdir / "fix" / "hgrid.ll", mdir / "fix" / "hgrid.gr3"):
        if hp.exists():
            try:
                return read_mesh_boundaries(hp)
            except Exception:
                return None
    return None

# =============================================================================
# Stage 1 — MPI parallel frame generation
# =============================================================================

def _build_file_task_list(cfg) -> list:
    """Return a list of (ym, fmt, stack_path) for every output file.

    fmt is 'new' or 'old' to indicate the output format.
    For new I/O: one task per variable-prefix file.
    For old I/O: one task per combined schout_N.nc file.
    """
    pid      = cfg["project_id"]
    mdir     = model_dir(cfg)
    # New I/O prefixes from config
    new_prefixes = list({pc.var_spec(v)["file_prefix"]
                         for v in cfg.get("plot_outputs_vars", [])
                         if pc.var_spec(v)["file_prefix"]})
    tasks = []
    for ym in list_months(cfg):
        outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
        if not outputs.is_dir():
            continue
        fmt = pc.detect_output_format(outputs)
        if fmt == "new":
            for prefix in new_prefixes:
                for nc in pc.list_output_stacks(outputs, prefix):
                    tasks.append((ym, "new", str(nc)))
        elif fmt == "old":
            for nc in pc.list_oldio_stacks(outputs):
                tasks.append((ym, "old", str(nc)))
    return tasks


def _render_file_newio(cfg, ym, nc_path, x, y, depth, triang, is_tri,
                       boundaries, fdir, var_cfgs, every_hours, dpi, isobaths):
    """Render frames from a New I/O file."""
    import numpy as np
    import xarray as xr

    nc_path = Path(nc_path)
    prefix  = "_".join(nc_path.stem.split("_")[:-1])

    relevant = [v for v in var_cfgs if v["file_prefix"] == prefix]
    if not relevant:
        return

    ds    = xr.open_dataset(str(nc_path), drop_variables=pc.SAFE_DROP)
    times = ds["time"].values

    for vc in relevant:
        name = vc["var_name"]
        if name not in ds:
            print(f"  [{ym} {nc_path.name}] '{name}' not in file, skipping.")
            continue
        is_elem = (vc["loc"] == "elem")
        arr     = pc.extract_layer(ds[name], vc["layer"]) if vc["is_3d"] \
                  else np.array(ds[name])
        vmin, vmax = pc.robust_limits(arr, vc["vmin"], vc["vmax"])

        for t_idx in range(arr.shape[0]):
            t = times[t_idx]
            if not _in_date_range(cfg, t) or not _on_cadence(t, every_hours):
                continue
            vals = arr[t_idx]
            if getattr(vals, "ndim", 1) > 1:
                vals = vals.ravel()
            if is_elem:
                vals = pc.expand_elem_values(vals, is_tri)
            else:
                vals = vals[:len(x)]
            ts = np.datetime_as_string(t, unit="h").replace(":", "").replace("-", "")
            fp = fdir / f"{name}__{ts}.jpg"
            title = f"{vc['label']} — {np.datetime_as_string(t, unit='h')}"
            if vc["is_3d"]:
                title += f" (layer: {vc['layer']})"
            pc.render_frame(
                triang, vals, title=title, out_path=fp,
                cbar_label=vc["label"], cmap=vc["cmap"],
                vmin=vmin, vmax=vmax,
                depth=depth, isobaths=isobaths,
                dpi=dpi, boundaries=boundaries, is_elem=is_elem,
            )
        gc.collect()

    ds.close()
    print(f"  [{ym} {nc_path.name}] frames written.")
    sys.stdout.flush()


def _render_file_oldio(cfg, ym, nc_path, x, y, depth, triang, is_tri,
                       boundaries, fdir, var_cfgs, every_hours, dpi, isobaths):
    """Render frames from an Old I/O schout_N.nc file.

    All variables are in a single file. Variable names differ from New I/O
    but the adapter in plot_common handles translation.
    """
    import numpy as np
    import xarray as xr

    nc_path = Path(nc_path)
    ds      = xr.open_dataset(str(nc_path), drop_variables=["zcor"])
    times   = ds["time"].values

    for vc in var_cfgs:
        name    = vc["var_name"]
        is_elem = (vc["loc"] == "elem")

        try:
            # Use adapter to extract variable (handles rename + vector components)
            arr = _extract_oldio_all_times(ds, name, vc["layer"] if vc["is_3d"]
                                           else None, times.shape[0])
        except KeyError as e:
            print(f"  [{ym} {nc_path.name}] {e}, skipping.")
            continue
        except Exception as e:
            print(f"  [{ym} {nc_path.name}] ERROR extracting '{name}': {e}")
            continue

        vmin, vmax = pc.robust_limits(arr, vc["vmin"], vc["vmax"])

        for t_idx in range(arr.shape[0]):
            t = times[t_idx]
            if not _in_date_range(cfg, t) or not _on_cadence(t, every_hours):
                continue
            vals = arr[t_idx]
            if getattr(vals, "ndim", 1) > 1:
                vals = vals.ravel()
            if is_elem:
                vals = pc.expand_elem_values(vals, is_tri)
            else:
                vals = vals[:len(x)]
            ts = np.datetime_as_string(t, unit="h").replace(":", "").replace("-", "")
            fp = fdir / f"{name}__{ts}.jpg"
            title = f"{vc['label']} — {np.datetime_as_string(t, unit='h')}"
            if vc["is_3d"]:
                title += f" (layer: {vc['layer']})"
            pc.render_frame(
                triang, vals, title=title, out_path=fp,
                cbar_label=vc["label"], cmap=vc["cmap"],
                vmin=vmin, vmax=vmax,
                depth=depth, isobaths=isobaths,
                dpi=dpi, boundaries=boundaries, is_elem=is_elem,
            )
        gc.collect()

    ds.close()
    print(f"  [{ym} {nc_path.name}] frames written.")
    sys.stdout.flush()


def _extract_oldio_all_times(ds, new_varname: str, layer_spec, ntime: int):
    """Extract a variable for all time steps from an old I/O dataset.

    Returns array of shape (ntime, nnodes) or (ntime, nelem).
    """
    import numpy as np

    result = []
    for t_idx in range(ntime):
        # Build a single-timestep slice for the adapter
        import xarray as xr
        ds_t = ds.isel(time=t_idx)
        val = pc.extract_oldio_var(ds_t, new_varname, layer_spec or "surface")
        result.append(val)
    return np.stack(result, axis=0)


def mpi_frames(cfg):
    """MPI parallel frame generation — one rank per output file."""
    from mpi4py import MPI
    import numpy as np

    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    size = comm.Get_size()

    if rank == 0:
        tasks = _build_file_task_list(cfg)
        print(f"  [rank 0] {len(tasks)} output file(s) distributed across "
              f"{size} rank(s).")
        sys.stdout.flush()
    else:
        tasks = None
    tasks = comm.bcast(tasks, root=0)

    if not tasks:
        if rank == 0:
            print("  plot_outputs: no output files found.")
        comm.Barrier()
        return

    out2d0 = _find_any_output_file(cfg)
    if out2d0 is None:
        if rank == 0:
            print("  plot_outputs: no output file found for mesh. Aborting.")
        comm.Barrier()
        return

    x, y, depth, triang, is_tri = pc.load_mesh(out2d0)
    boundaries  = _load_boundaries(cfg)
    var_cfgs    = [pc.var_spec(v) for v in cfg.get("plot_outputs_vars", [])]
    every_hours = int(cfg.get("plot_outputs_every_hours", 6))
    dpi         = int(cfg.get("plot_outputs_dpi", 150))
    isobaths    = cfg.get("isobaths", [200, 2000])
    if isobaths:
        isobaths = [float(v) for v in isobaths]

    fdir = _frames_dir(cfg)
    fdir.mkdir(parents=True, exist_ok=True)

    my_tasks = tasks[rank::size]
    for ym, fmt, nc_path in my_tasks:
        if fmt == "new":
            _render_file_newio(cfg, ym, nc_path,
                               x, y, depth, triang, is_tri,
                               boundaries, fdir, var_cfgs,
                               every_hours, dpi, isobaths)
        else:  # old I/O
            _render_file_oldio(cfg, ym, nc_path,
                               x, y, depth, triang, is_tri,
                               boundaries, fdir, var_cfgs,
                               every_hours, dpi, isobaths)

    comm.Barrier()
    if rank == 0:
        print("  plot_outputs MPI frames complete.")
        (_frames_dir(cfg).parent / ".frames_done").touch()
        sys.stdout.flush()


def frames_for_file(cfg, ym: str, prefix: str, stack: int):
    """Legacy single-file entry point (backward compatibility)."""
    out2d0 = _find_any_output_file(cfg)
    if out2d0 is None:
        print(f"  {ym} {prefix}_{stack}: no output file found, skipping.")
        return
    x, y, depth, triang, is_tri = pc.load_mesh(out2d0)
    boundaries  = _load_boundaries(cfg)
    var_cfgs    = [pc.var_spec(v) for v in cfg.get("plot_outputs_vars", [])]
    every_hours = int(cfg.get("plot_outputs_every_hours", 6))
    dpi         = int(cfg.get("plot_outputs_dpi", 150))
    isobaths    = cfg.get("isobaths", [200, 2000])
    if isobaths:
        isobaths = [float(v) for v in isobaths]
    fdir = _frames_dir(cfg)
    fdir.mkdir(parents=True, exist_ok=True)

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    nc_path = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs" / f"{prefix}_{stack}.nc"
    fmt = "old" if prefix == "schout" else "new"
    if fmt == "new":
        _render_file_newio(cfg, ym, nc_path,
                           x, y, depth, triang, is_tri,
                           boundaries, fdir, var_cfgs,
                           every_hours, dpi, isobaths)
    else:
        _render_file_oldio(cfg, ym, nc_path,
                           x, y, depth, triang, is_tri,
                           boundaries, fdir, var_cfgs,
                           every_hours, dpi, isobaths)


def assemble(cfg):
    fdir = _frames_dir(cfg)
    gdir = _gif_dir(cfg)
    gdir.mkdir(parents=True, exist_ok=True)

    fps         = int(cfg.get("plot_outputs_fps", 4))
    keep_frames = bool(cfg.get("keep_frames", True))

    var_cfgs = [pc.var_spec(v) for v in cfg.get("plot_outputs_vars", [])]
    made = 0
    for vc in var_cfgs:
        name = vc["var_name"]
        frames = sorted(fdir.glob(f"{name}__*.jpg"))
        if not frames:
            print(f"  {name}: no frames found, skipping GIF.")
            continue
        layer_tag = str(vc["layer"]) if vc["is_3d"] else "2d"
        gif = gdir / f"{name}_{layer_tag}.gif"
        pc.assemble_gif(frames, gif, fps=fps, keep_frames=keep_frames)
        made += 1

    if not keep_frames:
        try:
            fdir.rmdir()
        except OSError:
            pass

    (gdir / "plot_outputs.done").touch()
    print(f"  plot_outputs assembly complete: {made} GIF(s) in {gdir}")


def main():
    ap = argparse.ArgumentParser(description="SCHISM full-run field GIFs")
    sub = ap.add_subparsers(dest="stage", required=True)
    sub.add_parser("mpi-frames")
    pf = sub.add_parser("frames")
    pf.add_argument("--month",  required=True)
    pf.add_argument("--prefix", required=True)
    pf.add_argument("--stack",  required=True, type=int)
    sub.add_parser("assemble")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg  = load_config(Path(args.config))

    if args.stage == "mpi-frames":
        mpi_frames(cfg)
    elif args.stage == "frames":
        frames_for_file(cfg, args.month, args.prefix, args.stack)
    elif args.stage == "assemble":
        assemble(cfg)


if __name__ == "__main__":
    main()
