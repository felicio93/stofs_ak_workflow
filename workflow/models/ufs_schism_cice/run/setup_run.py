"""
models/ufs_schism_cice/run/setup_run.py
=======================================
Phase 4, step "setup_run" for UFS-SCHISM+CICE.

The UFS-SCHISM run directory (ufs_schism/run/setup_run.py) with
executables.ufs_schism_cice, plus:
  cice_grid/         -> fix/cice/ (grid, kmt, bathymetry, ESMF mesh)
  ice_in             -> I{ID}_{group}/ice_in (gen_ice_in)
  history/           CICE history output (iceh.*.nc)
  RESTART/           also holds the CICE restarts (iced.<date>.nc); the
                     next group's ice.restart_file is written by
                     auto_hotstart.py (chain_cice_restart)

Sentinel: R{ID}_{group_id}/setup_run.done
"""

from pathlib import Path

from workflow.models.ufs_schism.run.setup_run import _link, run_setup_run \
    as _ufs_setup_run
from workflow.models.ufs_schism.run.setup_run import FIX_LINKS  # noqa: F401
from workflow.models.ufs_schism_cice.preprocess.gen_cice_grid import PRODUCTS


def _datm_precip_ok(rdir: Path) -> bool:
    """The DATM ERA5 mode converts precipitation from m/h when its maximum is
    >= 0.01; the workflow's files are kg m-2 s-1. Warn when a file would be
    taken for accumulations."""
    try:
        import numpy as np
        import netCDF4 as nc4
    except ImportError:
        return True
    ok = True
    for f in sorted((rdir / "forcing").glob("datm_*.nc")):
        with nc4.Dataset(str(f)) as nc:
            if "PRATE_surface" not in nc.variables:
                continue
            mx = float(np.nanmax(nc.variables["PRATE_surface"][:]))
        if mx >= 0.01:
            print(f"  WARNING: {f.name}: maximum precipitation {mx:.4f} "
                  f"kg m-2 s-1 >= 0.01: the DATM ERA5 mode will treat the "
                  f"whole field as accumulated metres and convert it.")
            ok = False
    return ok


class CiceExtra:
    """Hooks for ufs_schism/run/setup_run.py."""

    def links(self, cfg, mdir, idir, rdir, group_index, group_id) -> bool:
        cdir = mdir / "fix" / "cice"
        missing = [p for p in PRODUCTS if not (cdir / p).exists()]
        if missing:
            print(f"  ERROR {group_id}: CICE grid files missing in {cdir}: "
                  f"{', '.join(missing)} (run gen_cice_grid, --phase setup)")
            return False
        _link(cdir, rdir / "cice_grid")
        if not (idir / "gen_ice_in.done").exists():
            print(f"  ERROR {group_id}: 'gen_ice_in' has not completed "
                  f"(missing {idir / 'gen_ice_in.done'}).")
            return False
        _link(idir / "ice_in", rdir / "ice_in")
        (rdir / "history").mkdir(exist_ok=True)
        (rdir / "RESTART").mkdir(exist_ok=True)
        if group_index == 0:
            init = str(cfg.get("ice_initial", "none") or "none")
            if init.lower() != "none" and not Path(init).exists():
                print(f"  ERROR {group_id}: ice_initial {init} not found.")
                return False
        _datm_precip_ok(rdir)
        return True

    def auto_hotstart(self, cfg) -> dict:
        return {"CHAIN_CICE_RESTART": bool(cfg.get("chain_cice_restart",
                                                   True))}


def run_setup_run(cfg: dict, config_dir=None):
    _ufs_setup_run(cfg, config_dir, exe_key="ufs_schism_cice",
                   extra=CiceExtra(), label="UFS-SCHISM+CICE")
