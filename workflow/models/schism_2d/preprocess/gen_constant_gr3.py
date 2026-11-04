"""
models/schism_2d/preprocess/gen_constant_gr3.py
===============================================
Phase 0 (interactive) — write spatially constant property files into fix/.

The files and values come from `constant_gr3` in schism_2d.yaml, e.g.

    constant_gr3:
      manning.gr3: 0.025          # Manning's n (param.nml nchi = -1)
      windrot_geo2proj.gr3: 0.0   # wind rotation angle (read when nws = 2)

Each file is fix/hgrid.gr3 with the node values replaced by the constant
(elements and boundaries copied). Files already in fix/ are never
overwritten: one with a different value is reported, so a hand-made
(spatially varying) file can take the place of the constant.
"""

import sys

from workflow.core.config import model_dir


def _gr3_constant(path):
    """The single node value of a gr3 file, or None if values vary."""
    from workflow.core.mesh_parser import read_nodes
    vals = read_nodes(path)[3]
    return float(vals[0]) if (vals == vals[0]).all() else None


def write_constant_gr3(hgrid, out, value: float):
    """Copy hgrid.gr3 to `out` with every node value set to `value`."""
    with open(hgrid) as src, open(out, "w") as dst:
        src.readline()
        header = src.readline()
        np_nodes = int(header.split()[1])
        dst.write(f"{out.name} constant {value}\n")
        dst.write(header)
        for _ in range(np_nodes):
            nid, x, y = src.readline().split()[:3]
            dst.write(f"{nid} {x} {y} {value}\n")
        for line in src:
            dst.write(line)


def run_gen_constant_gr3(cfg: dict):
    fix   = model_dir(cfg) / "fix"
    hgrid = fix / "hgrid.gr3"
    files = cfg.get("constant_gr3") or {}

    print(f"\n{'='*60}")
    print(f"  gen_constant_gr3 for M{cfg['project_id']}")
    print(f"{'='*60}")
    if not files:
        print("  constant_gr3 is empty in schism_2d.yaml; nothing to write.")
        return
    if not hgrid.exists():
        print(f"ERROR: {hgrid} not found.")
        sys.exit(1)

    for name, value in files.items():
        value = float(value)
        out = fix / name
        if out.exists():
            have = _gr3_constant(out)
            if have is None:
                print(f"  fix/{name} exists with varying values, kept "
                      f"(constant_gr3 asks for {value}).")
            elif have != value:
                print(f"  WARNING: fix/{name} exists with value {have}, but "
                      f"constant_gr3 asks for {value}. Kept; move it away "
                      f"to regenerate.")
            else:
                print(f"  fix/{name} already has value {value}, kept.")
            continue
        write_constant_gr3(hgrid, out, value)
        print(f"  Written: {out}  (value {value})")
