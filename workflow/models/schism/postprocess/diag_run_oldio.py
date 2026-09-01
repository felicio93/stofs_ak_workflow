"""
models/schism/postprocess/diag_run_oldio.py
============================================
Phase 5 "diag_run_plots" worker for UFS-SCHISM old I/O (schout_N.nc).

Called by the SLURM job run_diag_oldio.sbatch AFTER combine_output11_MPI
has produced schout_N.nc from the per-rank partition files.

Steps performed by this script:
  1. Read schout_N.nc (combined global file)
  2. For each variable in diag_run_vars (from postprocess.yaml), render one
     JPEG diagnostic frame per timestep into D{ID}/D{ID}_YYYYMM/diag/
  3. Touch per-variable sentinel diag_oldio_{N}_{varname}.done
  4. When all variables are done, touch diag_oldio_{N}.done

The per-rank partition files (schout_??????_N.nc) are deleted by the SLURM
job script AFTER this script succeeds, not here.

CLI (invoked by run_diag_oldio.sbatch):
    python -m workflow.models.schism.postprocess.diag_run_oldio \\
        --config <cfg> --month YYYYMM --stack N --var VARNAME
"""

import argparse
import gc
from pathlib import Path

from workflow.core.config import load_config, model_dir
from workflow.models.schism.postprocess import plot_common as pc


def diag_stack_var_oldio(cfg: dict, ym: str, stack: int, varname: str):
    """Render diagnostic frames for one variable from one old I/O stack."""
    import numpy as np
    import xarray as xr
    from workflow.core.plot_style import read_mesh_boundaries

    pid     = cfg["project_id"]
    mdir    = model_dir(cfg)
    outputs = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs"
    ddir    = mdir / f"D{pid}" / f"D{pid}_{ym}" / "diag"
    ddir.mkdir(parents=True, exist_ok=True)

    # Sentinels
    var_sentinel   = ddir / f"diag_oldio_{stack}_{varname}.done"
    stack_sentinel = ddir / f"diag_oldio_{stack}.done"

    if var_sentinel.exists():
        print(f"  diag_oldio {ym} stack {stack} [{varname}]: "
              f"already done, skipping.")
        _maybe_write_stack_sentinel(cfg, ym, stack, ddir)
        return

    # Combined schout file for this stack
    schout_nc = outputs / f"schout_{stack}.nc"
    if not schout_nc.exists():
        print(f"  WARNING: {schout_nc.name} not found — "
              f"combine may not have finished yet. Skipping.")
        return

    # Find the variable config entry
    all_vars = [pc.var_spec(v) for v in cfg.get("diag_run_vars", [])]
    vc = next((v for v in all_vars if v["var_name"] == varname), None)
    if vc is None:
        print(f"  diag_oldio {ym} stack {stack}: '{varname}' not in "
              f"diag_run_vars. Skipping.")
        var_sentinel.touch()
        _maybe_write_stack_sentinel(cfg, ym, stack, ddir)
        return

    dpi      = int(cfg.get("diag_run_dpi", 150))
    isobaths = cfg.get("isobaths", [200, 2000])
    if isobaths:
        isobaths = [float(v) for v in isobaths]

    # Load mesh from the combined file (same structure as New I/O)
    x, y, depth, triang, is_tri = pc.load_mesh(schout_nc)

    # Load mesh boundaries for overlay
    boundaries = None
    for hp in (mdir / "fix" / "hgrid.ll", mdir / "fix" / "hgrid.gr3"):
        if hp.exists():
            try:
                boundaries = read_mesh_boundaries(hp)
            except Exception:
                boundaries = None
            break

    is_elem = (vc["loc"] == "elem")

    # Open the combined schout file
    ds    = xr.open_dataset(str(schout_nc), drop_variables=["zcor"])
    times = ds["time"].values
    ntime = len(times)

    # Extract all time steps for this variable using the old I/O adapter
    try:
        arr = _extract_oldio_all_times(ds, varname, vc, ntime)
    except (KeyError, Exception) as e:
        print(f"  WARNING: could not extract '{varname}' from "
              f"{schout_nc.name}: {e}")
        ds.close()
        var_sentinel.touch()
        _maybe_write_stack_sentinel(cfg, ym, stack, ddir)
        return

    ds.close()

    vmin, vmax = pc.robust_limits(arr, vc["vmin"], vc["vmax"])
    made = 0

    for t_idx in range(ntime):
        vals = arr[t_idx]
        if getattr(vals, "ndim", 1) > 1:
            vals = vals.ravel()
        if is_elem:
            vals = pc.expand_elem_values(vals, is_tri)
        else:
            vals = vals[:len(x)]

        t  = times[t_idx]
        ts = np.datetime_as_string(t, unit="h").replace(":", "").replace("-", "")
        fp = ddir / f"{varname}__{ts}.jpg"
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

    gc.collect()

    var_sentinel.touch()
    print(f"  diag_oldio {ym} stack {stack} [{varname}]: "
          f"{made} frame(s) -> {ddir}")
    _maybe_write_stack_sentinel(cfg, ym, stack, ddir)


def _extract_oldio_all_times(ds, new_varname: str, vc: dict,
                              ntime: int):
    """Extract all time steps for a variable from an old I/O dataset."""
    import numpy as np
    import xarray as xr

    result = []
    for t_idx in range(ntime):
        ds_t = ds.isel(time=t_idx)
        layer = vc["layer"] if vc["is_3d"] else None
        val   = pc.extract_oldio_var(ds_t, new_varname, layer or "surface")
        result.append(val)
    return np.stack(result, axis=0)


def _maybe_write_stack_sentinel(cfg: dict, ym: str, stack: int,
                                 ddir: Path):
    """Write diag_oldio_{stack}.done when all per-variable sentinels exist."""
    all_vars = [pc.var_spec(v) for v in cfg.get("diag_run_vars", [])]
    varnames = [v["var_name"] for v in all_vars]
    all_done = all(
        (ddir / f"diag_oldio_{stack}_{vn}.done").exists()
        for vn in varnames
    )
    sentinel = ddir / f"diag_oldio_{stack}.done"
    if all_done and not sentinel.exists():
        sentinel.touch()
        print(f"  diag_oldio {ym} stack {stack}: all variables complete "
              f"-> {sentinel.name}")


def main():
    ap = argparse.ArgumentParser(
        description="Old I/O diagnostic frames for one stack variable")
    ap.add_argument("--config", required=True)
    ap.add_argument("--month",  required=True, help="YYYYMM")
    ap.add_argument("--stack",  required=True, type=int)
    ap.add_argument("--var",    required=True,
                    help="Variable name (New I/O name, e.g. temperature)")
    args = ap.parse_args()
    cfg  = load_config(Path(args.config))
    diag_stack_var_oldio(cfg, args.month, args.stack, args.var)


if __name__ == "__main__":
    main()
