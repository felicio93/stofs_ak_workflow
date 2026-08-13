"""
models/schism/postprocess/plot_outputs.py
==========================================
Phase 5 step "plot_outputs" — full-run SCHISM field GIFs.

Two-stage SLURM design (see submit_plot_outputs.py):
  Stage 1 (parallel array, one task per output file):
      This module's `frames_for_file()` reads one output stack file and
      renders JPEG frames for every configured variable at every timestep
      that lands on the requested every-X-hours cadence. Frames go to
      P{ID}/P{ID}_plot_outputs/frames/.
  Stage 2 (serial, afterok):
      `assemble()` collects the frames per variable into one GIF spanning
      the whole requested date range, then keeps or deletes the frames.

Because the run is monthly (R{ID}_YYYYMM/outputs/), a "file index" is a
(month, stack_number) pair. Stage 1's array manifest lists every output
file across all requested months so each array task handles exactly one
file. Stage 2 stitches across months into per-variable GIFs.

CLI:
    # one array task -> one output file
    python -m workflow.models.schism.postprocess.plot_outputs frames \
        --config <cfg> --month YYYYMM --prefix out2d --stack N
    # serial assembly
    python -m workflow.models.schism.postprocess.plot_outputs assemble \
        --config <cfg>
"""

import argparse
import gc
import sys
from pathlib import Path

from workflow.core.config import load_config, list_months, model_dir
from workflow.models.schism.postprocess import plot_common as pc


# =============================================================================
# Shared paths / mesh
# =============================================================================

def _frames_dir(cfg) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_plot_outputs" / "frames"


def _gif_dir(cfg) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_plot_outputs"


def _find_any_out2d(cfg):
    """Return the first out2d_*.nc across all run months (for mesh)."""
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    for ym in list_months(cfg):
        outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
        stacks = pc.list_output_stacks(outputs, "out2d")
        if stacks:
            return stacks[0]
    return None


def _in_date_range(cfg, t) -> bool:
    """Whether datetime64 t is within plot_outputs_start/end (inclusive)."""
    import numpy as np
    start = cfg.get("plot_outputs_start")
    end   = cfg.get("plot_outputs_end")
    if start:
        if np.datetime64(t) < np.datetime64(str(start)):
            return False
    if end:
        # include the whole end day
        if np.datetime64(t) > np.datetime64(str(end)) + np.timedelta64(1, "D"):
            return False
    return True


def _on_cadence(t, every_hours: int) -> bool:
    """Whether datetime64 t lands on the every-X-hours cadence from 00:00Z."""
    import numpy as np
    if every_hours <= 0:
        return True
    hours = (np.datetime64(t) - np.datetime64(str(np.datetime64(t, "D")))) \
        / np.timedelta64(1, "h")
    return (round(float(hours)) % every_hours) == 0


# =============================================================================
# Stage 1 — render frames for ONE output file
# =============================================================================

def frames_for_file(cfg, ym: str, prefix: str, stack: int):
    """Render frames for every configured variable in one output stack file."""
    import numpy as np
    import xarray as xr

    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
    nc_path = outputs / f"{prefix}_{stack}.nc"
    if not nc_path.exists():
        print(f"  {ym} {prefix}_{stack}: file not found, skipping.")
        return

    var_cfgs   = [pc.var_spec(v) for v in cfg.get("plot_outputs_vars", [])]
    var_cfgs   = [v for v in var_cfgs if v["file_prefix"] == prefix]
    if not var_cfgs:
        return  # this prefix isn't needed for any configured variable

    every_hours = int(cfg.get("plot_outputs_every_hours", 6))
    dpi         = int(cfg.get("plot_outputs_dpi", 150))
    isobaths    = cfg.get("isobaths", [200, 2000])
    if isobaths:
        isobaths = [float(v) for v in isobaths]

    # Mesh + boundaries (once)
    out2d0 = _find_any_out2d(cfg)
    if out2d0 is None:
        print(f"  {ym} {prefix}_{stack}: no out2d_*.nc found in any run directory. "
              f"Has the model run completed?")
        return
    x, y, depth, triang, is_tri = pc.load_mesh(out2d0)
    boundaries = _load_boundaries(cfg)

    fdir = _frames_dir(cfg)
    fdir.mkdir(parents=True, exist_ok=True)

    ds = xr.open_dataset(str(nc_path), drop_variables=pc.SAFE_DROP)
    times = ds["time"].values

    for vc in var_cfgs:
        name = vc["var_name"]
        if name not in ds:
            print(f"    WARNING: '{name}' not in {nc_path.name}, skipping "
                  f"(check var_name / file_prefix in postprocess.yaml).")
            continue

        is_elem = (vc["loc"] == "elem")

        arr = pc.extract_layer(ds[name], vc["layer"]) if vc["is_3d"] \
            else np.array(ds[name])

        vmin, vmax = pc.robust_limits(arr, vc["vmin"], vc["vmax"])

        for t_idx in range(arr.shape[0]):
            t = times[t_idx]
            if not _in_date_range(cfg, t) or not _on_cadence(t, every_hours):
                continue
            vals = arr[t_idx]
            if getattr(vals, "ndim", 1) > 1:
                vals = vals.ravel()
            # Node-centered fields have one value per node; element-centered
            # fields must be expanded onto the split triangulation.
            if is_elem:
                vals = pc.expand_elem_values(vals, is_tri)
            else:
                vals = vals[:len(x)]

            # Frame filename embeds a sortable absolute timestamp so Stage 2
            # can stitch across months/files in chronological order.
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
    print(f"  {ym} {prefix}_{stack}: frames written -> {fdir}")


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
# Stage 2 — assemble per-variable GIFs
# =============================================================================

def assemble(cfg):
    """Collect frames per variable into one GIF spanning the date range."""
    fdir = _frames_dir(cfg)
    gdir = _gif_dir(cfg)
    gdir.mkdir(parents=True, exist_ok=True)

    fps         = int(cfg.get("plot_outputs_fps", 4))
    keep_frames = bool(cfg.get("keep_frames", True))

    var_cfgs = [pc.var_spec(v) for v in cfg.get("plot_outputs_vars", [])]
    made = 0
    for vc in var_cfgs:
        name = vc["var_name"]
        # Frames are named "<var>__<timestamp>.jpg"; sort chronologically.
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


# =============================================================================
# CLI
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description="SCHISM full-run field GIFs")
    sub = ap.add_subparsers(dest="stage", required=True)

    pf = sub.add_parser("frames", help="render frames for one output file")
    pf.add_argument("--config", required=True)
    pf.add_argument("--month",  required=True)
    pf.add_argument("--prefix", required=True)
    pf.add_argument("--stack",  required=True, type=int)

    pa = sub.add_parser("assemble", help="assemble per-variable GIFs")
    pa.add_argument("--config", required=True)

    args = ap.parse_args()
    cfg = load_config(Path(args.config))

    if args.stage == "frames":
        frames_for_file(cfg, args.month, args.prefix, args.stack)
    elif args.stage == "assemble":
        assemble(cfg)


if __name__ == "__main__":
    main()
