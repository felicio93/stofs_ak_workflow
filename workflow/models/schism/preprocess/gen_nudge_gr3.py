"""
models/schism/preprocess/gen_nudge_gr3.py
=========================================
Step gen_nudge_gr3 (setup phase, once per project): fix/TEM_nudge.gr3 and
fix/SAL_nudge.gr3, the T/S nudging strength [1/s] in a zone along the open
boundaries (read by SCHISM with inu_tr = 2; gen_nudge writes TEM_nu.nc /
SAL_nu.nc for the nodes where it is > 0).

Same formula as STOFS-3D-Atlantic (stofs3d_setup ops/simple_tasks.py,
gen_nudge_coef): for every node, d = distance to the nearest open-boundary
node in degrees (lon/lat of hgrid.ll), and

    nudge = clip((1 - d / rlmax) * rnu_max, 0, rnu_max),
    rnu_max = 1 / (rnu_day * 86400)

so the strength is rnu_max on the boundary and falls linearly to 0 at rlmax
degrees. Settings (modelsetup.yaml):

    nudge_gr3:
      rlmax: 0.3          # zone width [degrees]
      rnu_day: 0.25       # strongest relaxation time on the boundary [days]
      boundaries: [1]     # optional: open boundaries to use (1-based; all)

Both files get the same values. Files already in fix/ are kept.
"""

import sys
from pathlib import Path

import numpy as np

from workflow.core.config import model_dir
from workflow.models.schism.preprocess.replicate_fix import (
    _write_gr3, read_gr3,
)


def open_boundary_nodes(hgrid: Path) -> list:
    """0-based node indices of each open boundary of hgrid.gr3."""
    with open(hgrid) as f:
        f.readline()
        ne, np_ = (int(v) for v in f.readline().split()[:2])
        for _ in range(np_ + ne):
            f.readline()
        line = f.readline()
        if not line.strip():
            return []
        nope = int(line.split()[0])
        f.readline()
        out = []
        for _ in range(nope):
            n = int(f.readline().split()[0])
            out.append(np.array([int(f.readline().split()[0]) - 1
                                 for _ in range(n)], dtype=np.int64))
    return out


def nudge_coef(x, y, bnd_nodes: list, rlmax: float, rnu_day: float):
    from scipy.spatial import cKDTree
    rnu_max = 1.0 / rnu_day / 86400.0
    coef = np.zeros(len(x))
    for ids in bnd_nodes:
        d, _ = cKDTree(np.c_[x[ids], y[ids]]).query(np.c_[x, y])
        coef = np.maximum(coef, np.clip((1.0 - d / rlmax) * rnu_max,
                                        0.0, rnu_max))
    return coef


def run_gen_nudge_gr3(cfg: dict):
    fix = model_dir(cfg) / "fix"
    outs = [fix / "TEM_nudge.gr3", fix / "SAL_nudge.gr3"]

    print(f"\n{'='*60}")
    print(f"  gen_nudge_gr3 for M{cfg['project_id']}")
    print(f"{'='*60}")
    if all(p.exists() for p in outs):
        print("  fix/TEM_nudge.gr3 and SAL_nudge.gr3 exist: kept.")
        return
    ncfg = cfg.get("nudge_gr3")
    if not isinstance(ncfg, dict):
        sys.exit("ERROR (gen_nudge_gr3): no nudge_gr3: block in "
                 "modelsetup.yaml (rlmax, rnu_day)")
    rlmax = float(ncfg.get("rlmax", 0))
    rnu_day = float(ncfg.get("rnu_day", 0))
    if rlmax <= 0 or rnu_day <= 0:
        sys.exit("ERROR (gen_nudge_gr3): nudge_gr3 rlmax and rnu_day must "
                 "be > 0")
    for name in ("hgrid.gr3", "hgrid.ll"):
        if not (fix / name).exists():
            sys.exit(f"ERROR (gen_nudge_gr3): fix/{name} not found")

    bnds = open_boundary_nodes(fix / "hgrid.gr3")
    if not bnds:
        sys.exit("ERROR (gen_nudge_gr3): hgrid.gr3 has no open boundary")
    want = ncfg.get("boundaries")
    if want:
        bad = [b for b in want if not (isinstance(b, int)
                                       and 1 <= b <= len(bnds))]
        if bad:
            sys.exit(f"ERROR (gen_nudge_gr3): boundaries {bad} not in "
                     f"1..{len(bnds)}")
        bnds = [bnds[b - 1] for b in want]

    mesh = read_gr3(fix / "hgrid.ll")
    coef = nudge_coef(mesh["x"], mesh["y"], bnds, rlmax, rnu_day)
    n_on = int((coef > 0).sum())
    print(f"  {len(bnds)} open boundary(ies), rlmax {rlmax} deg, rnu_day "
          f"{rnu_day} (max {coef.max():.3e} 1/s); {n_on} of {mesh['np']} "
          f"nodes in the nudging zone")
    for out in outs:
        if out.exists():
            print(f"  fix/{out.name} exists: kept.")
            continue
        _write_gr3(out, f"{out.name}: rlmax={rlmax} deg, rnu_day={rnu_day}",
                   mesh, coef, fix / "hgrid.ll")
        print(f"  Written: {out}")
