"""
models/schism/postprocess/diag_run.py
=====================================
Phase 5 "diag_run_plots" — per-output-file diagnostic frames DURING the run.

This is dispatched from auto_hotstart.py (Phase 4): whenever a new SCHISM
output stack file finishes being written (out2d_N.nc completes once
out2d_{N+1}.nc appears, or the run ends), auto_hotstart sbatch-submits one
diag_run job for stack N. This module then renders static diagnostic frames
(no GIF) for every timestep in that stack, for each configured variable, and
writes them to D{ID}/D{ID}_YYYYMM/diag/.

Configured in postprocess.yaml under `diag_run_vars`. Default set:
SSH, SST, SSS, surface U, surface V. 200/2000 m isobaths overlaid.

The intent is a quick health check of the running model — one image per
timestep per variable, browsable as the run progresses.

CLI (invoked by SLURM via diag_run.sbatch):
    python -m workflow.models.schism.postprocess.diag_run \
        --config <cfg> --month YYYYMM --stack N
"""

import argparse
import gc
from pathlib import Path

from workflow.core.config import load_config, model_dir
from workflow.models.schism.postprocess import plot_common as pc


def diag_stack(cfg, ym: str, stack: int):
    """Render diagnostic frames for every configured variable in one stack."""
    import numpy as np
    import xarray as xr
    from workflow.core.plot_style import read_mesh_boundaries

    pid     = cfg["project_id"]
    mdir    = model_dir(cfg)
    outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
    ddir    = mdir / f"D{pid}" / f"D{pid}_{ym}" / "diag"
    ddir.mkdir(parents=True, exist_ok=True)

    marker = ddir / f"diag_{stack}.done"
    if marker.exists():
        print(f"  diag {ym} stack {stack}: already done, skipping.")
        return

    var_cfgs = [pc.var_spec(v) for v in cfg.get("diag_run_vars", [])]
    if not var_cfgs:
        print("  diag_run_plots: no diag_run_vars configured. Skipping.")
        marker.touch()
        return

    dpi      = int(cfg.get("diag_run_dpi", 150))
    isobaths = cfg.get("isobaths", [200, 2000])
    if isobaths:
        isobaths = [float(v) for v in isobaths]

    # Mesh from this month's out2d (must exist before any 3D var is plotted).
    out2d = outputs / f"out2d_{stack}.nc"
    if not out2d.exists():
        # fall back to any out2d in this month
        stacks = pc.list_output_stacks(outputs, "out2d")
        if not stacks:
            print(f"  diag {ym} stack {stack}: no out2d file for mesh, skipping.")
            return
        out2d = stacks[0]
    x, y, depth, triang, is_tri = pc.load_mesh(out2d)

    boundaries = None
    for hp in (mdir / "fix" / "hgrid.ll", mdir / "fix" / "hgrid.gr3"):
        if hp.exists():
            try:
                boundaries = read_mesh_boundaries(hp)
            except Exception:
                boundaries = None
            break

    made = 0
    for vc in var_cfgs:
        prefix = vc["file_prefix"]
        name   = vc["var_name"]
        nc_path = outputs / f"{prefix}_{stack}.nc"
        if not nc_path.exists():
            print(f"    WARNING: {prefix}_{stack}.nc not found, skipping {name}.")
            continue

        ds = xr.open_dataset(str(nc_path), drop_variables=pc.SAFE_DROP)
        if name not in ds:
            ds.close()
            print(f"    WARNING: '{name}' not in {nc_path.name}, skipping "
                  f"(check var_name / file_prefix in postprocess.yaml).")
            continue

        is_elem = (vc["loc"] == "elem")

        arr = pc.extract_layer(ds[name], vc["layer"]) if vc["is_3d"] \
            else np.array(ds[name])
        times = ds["time"].values
        vmin, vmax = pc.robust_limits(arr, vc["vmin"], vc["vmax"])

        for t_idx in range(arr.shape[0]):
            vals = arr[t_idx]
            if getattr(vals, "ndim", 1) > 1:
                vals = vals.ravel()
            if is_elem:
                vals = pc.expand_elem_values(vals, is_tri)
            else:
                vals = vals[:len(x)]
            t = times[t_idx]
            ts = np.datetime_as_string(t, unit="h").replace(":", "").replace("-", "")
            fp = ddir / f"{name}__{ts}.jpg"
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
            made += 1
        ds.close()
        gc.collect()

    marker.touch()
    print(f"  diag {ym} stack {stack}: {made} frame(s) -> {ddir}")


def main():
    ap = argparse.ArgumentParser(description="SCHISM per-stack diagnostic frames")
    ap.add_argument("--config", required=True)
    ap.add_argument("--month",  required=True)
    ap.add_argument("--stack",  required=True, type=int)
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    diag_stack(cfg, args.month, args.stack)


if __name__ == "__main__":
    main()
