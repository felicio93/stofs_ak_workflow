"""
core/mesh_check.py
==================
Element orientation check of the mesh files in fix/.

SCHISM needs every element listed counter-clockwise and aborts with
"Negative elem. areas; check nonfatal_*" otherwise (after the whole
preprocessing has run). This module finds clockwise and zero-area elements
in hgrid.gr3 / hgrid.ll so the workflow can stop before anything is made.
It only reports: fixing the mesh is the user's job (regenerate it with the
mesh tool's counter-clockwise option).

  check_file(path)         -> dict with ne, np, clockwise, zero (0-based ids)
  check_mesh(fix, quiet)   -> list of error strings (empty when valid)
  require_valid_mesh(cfg)  -> exits with an error when the mesh is invalid

A passing result is remembered in fix/.mesh_check (file sizes and mtimes),
so later calls skip the read until hgrid.gr3 or hgrid.ll changes.
"""

import json
import sys
from pathlib import Path

import numpy as np

MESH_FILES = ("hgrid.gr3", "hgrid.ll")
_STAMP = ".mesh_check"


def _read_mesh(path):
    import pandas as pd
    with open(path) as f:
        f.readline()
        ne, np_ = (int(v) for v in f.readline().split()[:2])
    xy = pd.read_csv(path, sep=r"\s+", skiprows=2, nrows=np_, header=None,
                     usecols=[1, 2], engine="c").to_numpy(dtype="f8")
    el = pd.read_csv(path, sep=r"\s+", skiprows=2 + np_, nrows=ne,
                     header=None, names=range(6), engine="c")
    nv = el[1].to_numpy(dtype=np.int64)
    conn = el[[2, 3, 4, 5]].fillna(0).to_numpy(dtype=np.int64) - 1
    conn[nv == 3, 3] = -1
    return ne, np_, xy, conn


def signed_areas(x, y, conn, lonlat=False):
    """Shoelace signed area of each element (positive = counter-clockwise).

    conn is (ne, 4) 0-based with -1 in column 3 for triangles. Coordinates
    are taken relative to the first node of each element (precision with
    projected metres); with lonlat=True the longitude offsets are wrapped
    to [-180, 180) so elements across the dateline keep their sign.
    """
    n = np.where(conn[:, 3] >= 0, 4, 3)
    c = np.where(conn >= 0, conn, conn[:, :1])
    dx = x[c] - x[c[:, :1]]
    if lonlat:
        dx = (dx + 180.0) % 360.0 - 180.0
    dy = y[c] - y[c[:, :1]]
    area = np.zeros(len(conn))
    for k in range(4):
        k1 = np.where(k + 1 < n, (k + 1) % 4, 0)
        rows = np.arange(len(conn))
        term = dx[:, k] * dy[rows, k1] - dx[rows, k1] * dy[:, k]
        area += np.where(k < n, term, 0.0)
    return 0.5 * area


def check_file(path):
    path = Path(path)
    ne, np_, xy, conn = _read_mesh(path)
    area = signed_areas(xy[:, 0], xy[:, 1], conn,
                        lonlat=path.suffix == ".ll")
    return {"ne": ne, "np": np_,
            "clockwise": np.flatnonzero(area < 0),
            "zero": np.flatnonzero(area == 0)}


def _signature(fix):
    sig = {}
    for name in MESH_FILES:
        p = Path(fix) / name
        if p.exists():
            st = p.stat()
            sig[name] = [st.st_size, st.st_mtime_ns]
    return sig


def check_mesh(fix, quiet=False):
    """Check hgrid.gr3 and hgrid.ll of fix/; return the list of errors.

    Missing files are not reported here (check_fix lists them).
    """
    fix = Path(fix)
    sig = _signature(fix)
    if not sig:
        return []
    stamp = fix / _STAMP
    try:
        if json.loads(stamp.read_text()) == sig:
            if not quiet:
                print("  Mesh orientation: ok (checked before, files "
                      "unchanged)")
            return []
    except (OSError, ValueError):
        pass

    errors = []
    for name in sig:
        r = check_file(fix / name)
        cw, zero = r["clockwise"], r["zero"]
        if not quiet:
            print(f"  {name}: np={r['np']} ne={r['ne']}  clockwise: "
                  f"{len(cw)}  zero-area: {len(zero)}")
        if len(cw):
            errors.append(
                f"{name}: {len(cw)} of {r['ne']} elements are clockwise "
                f"(first ids: {' '.join(str(i + 1) for i in cw[:10])})")
        if len(zero):
            errors.append(
                f"{name}: {len(zero)} zero-area elements "
                f"(first ids: {' '.join(str(i + 1) for i in zero[:10])})")
    if not errors:
        try:
            stamp.write_text(json.dumps(sig))
        except OSError:
            pass
    return errors


def print_mesh_errors(errors):
    print("\n  ERROR: invalid mesh in fix/. SCHISM needs counter-clockwise, "
          "non-degenerate elements")
    print("  (it aborts with 'Negative elem. areas' otherwise).")
    for e in errors:
        print(f"    - {e}")
    print("  Regenerate the mesh with the elements ordered "
          "counter-clockwise,\n  then redo everything made from it "
          "(vgrid.in, partition.prop, property files).\n")


def require_valid_mesh(cfg):
    """Exit when fix/hgrid.gr3 or fix/hgrid.ll has bad elements."""
    from workflow.core.config import model_dir
    fix = model_dir(cfg) / "fix"
    print("[CHECK] mesh orientation")
    errors = check_mesh(fix)
    if errors:
        print_mesh_errors(errors)
        sys.exit(1)
