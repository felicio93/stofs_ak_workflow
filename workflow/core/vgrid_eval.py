"""
core/vgrid_eval.py
==================
Score candidate vertical grids on a mesh without writing vgrid.in.

    python -m workflow.core.vgrid_eval --hgrid M73/fix/hgrid.gr3 \
        --candidates candidates.yaml [--out table.txt]

candidates.yaml maps a name to a vgrid: block (the schema of
modelsetup.yaml, shallow: included); the first one is the reference for
the cost ratio:

    current:
      dz_bot_min: 0.5
      classes: [[1.0, 2, 2.0], [40.0, 15, 2.0], ...]
    hybrid:
      ...
      shallow: {depth: 3.0, classes: [[1.0, 3, 0.0001], [3.0, 7, 0.0001]]}

For each candidate it prints the numbers of core.vgrid.grid_diagnostics
(cost in prisms and relative to the reference, layer jumps between nodes,
hydrostatic consistency rx1, thinnest layer, shelf surface/bottom layer,
layers per depth band); with --maps a .gr3 of the largest rx1 at each node
per candidate. A candidate that fails (e.g. a node deeper than
its last class) is reported and skipped.
"""

import argparse
import sys
import time
from pathlib import Path

import yaml

from workflow.core.vgrid import (compute_grid, format_diagnostics,
                                 grid_diagnostics, read_hgrid)


def evaluate(hgrid: Path, candidates: dict, out=None, maps=None) -> list:
    t0 = time.time()
    print(f"Reading {hgrid} ...", flush=True)
    x, y, dp, i34, conn = read_hgrid(hgrid)
    if maps:
        Path(maps).mkdir(parents=True, exist_ok=True)
    print(f"  {dp.size} nodes, {len(conn)} elements, depth {dp.min():.2f} "
          f"to {dp.max():.2f} m ({time.time() - t0:.0f} s)", flush=True)
    results, ref = [], None
    text = [f"mesh {hgrid}: {dp.size} nodes, {len(conn)} elements"]
    for name, vcfg in candidates.items():
        t = time.time()
        try:
            kbp, sigma, info = compute_grid(dp, vcfg)
        except SystemExit as e:
            msg = f"\n== {name}: FAILED: {e}"
            print(msg, flush=True)
            text.append(msg)
            continue
        d = grid_diagnostics(dp, kbp, sigma, conn, maps=bool(maps))
        del sigma
        if maps:
            from workflow.core.vgrid import write_node_gr3
            f = Path(maps) / f"rx1_{name}.gr3"
            write_node_gr3(f, f"max rx1 at each node, candidate {name}", x, y,
                           d["rx1_node"], i34, conn)
            print(f"  wrote {f}", flush=True)
        if ref is None:
            ref = d["prisms"]
        block = (f"\n== {name}" + (f"  ({info['n_shallow']} nodes on the "
                                   f"shallow classes)" if info["n_shallow"]
                                   else "")
                 + f"   [{time.time() - t:.0f} s]\n"
                 + format_diagnostics(d, ref))
        print(block, flush=True)
        text.append(block)
        results.append((name, d))
    if out:
        Path(out).write_text("\n".join(text) + "\n")
        print(f"\nwrote {out}")
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hgrid", required=True, type=Path)
    ap.add_argument("--candidates", required=True, type=Path)
    ap.add_argument("--out", default=None)
    ap.add_argument("--maps", default=None,
                    help="folder for one rx1_<name>.gr3 per candidate "
                         "(max rx1 at each node, for QGIS)")
    a = ap.parse_args(argv)
    cands = yaml.safe_load(a.candidates.read_text())
    if not isinstance(cands, dict) or not cands:
        sys.exit("candidates file must map names to vgrid blocks")
    evaluate(a.hgrid, cands, a.out, a.maps)


if __name__ == "__main__":
    main()
