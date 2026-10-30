#!/usr/bin/env python3
"""
utils/check_mesh_orientation.py
===============================
Report clockwise and zero-area elements of SCHISM mesh files.
SCHISM needs counter-clockwise elements and aborts with
"Negative elem. areas; check nonfatal_*" otherwise.

    python utils/check_mesh_orientation.py fix/hgrid.gr3 fix/hgrid.ll

Exit status 1 when any file has bad elements. It does not change the
files: regenerate the mesh with its elements ordered counter-clockwise.
The same check runs in --check-fix and before the preprocess and run
phases (workflow/core/mesh_check.py).
"""

import argparse
import sys

from workflow.core.mesh_check import check_file


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", help="hgrid.gr3 / hgrid.ll")
    args = ap.parse_args(argv)

    status = 0
    for path in args.files:
        r = check_file(path)
        cw, zero = r["clockwise"], r["zero"]
        print(f"{path}: np={r['np']} ne={r['ne']}  clockwise elements: "
              f"{len(cw)}  zero-area elements: {len(zero)}")
        if len(cw):
            print("  first clockwise element ids:",
                  " ".join(str(i + 1) for i in cw[:10]))
        if len(zero):
            print("  first zero-area element ids:",
                  " ".join(str(i + 1) for i in zero[:10]))
        if len(cw) or len(zero):
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
