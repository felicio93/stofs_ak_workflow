#!/usr/bin/env python3
"""
utils/fix_element_orientation.py
================================
Find (and optionally fix) mesh elements whose nodes are listed clockwise.
SCHISM needs counter-clockwise elements and aborts with
"Negative elem. areas; check nonfatal_*" otherwise.

    python utils/fix_element_orientation.py fix/hgrid.gr3 fix/hgrid.ll
    python utils/fix_element_orientation.py fix/hgrid.gr3 fix/hgrid.ll --write

Without --write it only reports. With --write each file is kept as
<file>.orig (once) and rewritten with the node order of the clockwise
elements reversed (triangle n1 n2 n3 -> n1 n3 n2, quad n1 n2 n3 n4 ->
n1 n4 n3 n2). Node block, element ids and the boundary block are copied
unchanged, so files keyed by element number (tvd.prop, partition.prop,
source.nc) stay valid. Zero-area elements are reported, not changed.
"""

import argparse
import os
import shutil
import sys

import numpy as np


def _signed_areas(x, y, conn):
    """Shoelace signed area (in the file's coordinates) of each element;
    conn is (ne, 4) 0-based with -1 for triangles."""
    n = np.where(conn[:, 3] >= 0, 4, 3)
    area = np.zeros(len(conn))
    for k in range(4):
        a = conn[:, k]
        b = np.where(k + 1 < n, conn[:, (k + 1) % 4], conn[:, 0])
        use = (k < n) & (a >= 0)
        a = np.where(use, a, 0)
        b = np.where(use, b, 0)
        term = np.subtract(np.multiply(x[a], y[b]), np.multiply(x[b], y[a]))
        area += np.where(use, term, 0.0)
    return 0.5 * area


def check(path):
    import pandas as pd
    with open(path) as f:
        f.readline()
        ne, np_ = (int(v) for v in f.readline().split()[:2])
    nodes = pd.read_csv(path, sep=r"\s+", skiprows=2, nrows=np_, header=None,
                        usecols=[1, 2], engine="c").to_numpy(dtype="f8")
    el = pd.read_csv(path, sep=r"\s+", skiprows=2 + np_, nrows=ne,
                     header=None, names=range(6), engine="c")
    nv = el[1].to_numpy(dtype=np.int64)
    conn = el[[2, 3, 4, 5]].fillna(0).to_numpy(dtype=np.int64) - 1
    conn[nv == 3, 3] = -1
    area = _signed_areas(nodes[:, 0], nodes[:, 1], conn)
    return ne, np_, area


def rewrite(path, flip):
    """Rewrite element lines of the flagged elements (0-based indices)."""
    flip = set(int(i) for i in flip)
    tmp = path + ".tmp"
    with open(path) as src, open(tmp, "w") as out:
        out.write(src.readline())
        head = src.readline()
        out.write(head)
        ne, np_ = (int(v) for v in head.split()[:2])
        for _ in range(np_):
            out.write(src.readline())
        for i in range(ne):
            line = src.readline()
            if i in flip:
                p = line.split()
                k = int(p[1])
                nodes = p[2:2 + k]
                nodes = [nodes[0]] + nodes[1:][::-1]
                line = " ".join([p[0], p[1]] + nodes) + "\n"
            out.write(line)
        shutil.copyfileobj(src, out)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", help="hgrid.gr3 / hgrid.ll")
    ap.add_argument("--write", action="store_true",
                    help="fix the files (keeps <file>.orig)")
    args = ap.parse_args(argv)

    status = 0
    for path in args.files:
        ne, np_, area = check(path)
        neg = np.flatnonzero(area < 0)
        zero = np.flatnonzero(area == 0)
        print(f"{path}: np={np_} ne={ne}  clockwise elements: {len(neg)}  "
              f"zero-area elements: {len(zero)}")
        if len(neg):
            print("  first clockwise element ids:",
                  " ".join(str(i + 1) for i in neg[:10]))
        if len(zero):
            print("  zero-area element ids (not changed):",
                  " ".join(str(i + 1) for i in zero[:10]))
            status = 1
        if args.write and len(neg):
            orig = path + ".orig"
            if not os.path.exists(orig):
                shutil.copy2(path, orig)
                print(f"  original kept as {orig}")
            rewrite(path, neg)
            ne2, _, area2 = check(path)
            left = int((area2 < 0).sum())
            print(f"  rewritten: {left} clockwise element(s) left")
            if left:
                status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
