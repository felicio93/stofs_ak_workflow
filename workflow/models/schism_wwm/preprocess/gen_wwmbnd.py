"""
models/schism_wwm/preprocess/gen_wwmbnd.py
==========================================
Generate wwmbnd.gr3 from hgrid.gr3 for SCHISM+WWM.

wwmbnd.gr3 has the same format as hgrid.gr3 (standard SCHISM .gr3):
    title
    ne  np
    node_id  lon  lat  flag      (np lines)
    elem_id  nverts  n1 n2 [n3]  (ne lines)
    (no boundary block needed)

Flag values:
    0 = interior node (not on any boundary)
    2 = active wave boundary (Dirichlet) — wave energy prescribed
        Used for open ocean boundaries with LBCWA=T in wwminput.nml
        (parametric JONSWAP for closed-domain / first iteration)
    3 = Neumann boundary (zero gradient) — for fully closed domain

This script implements Option B (closed domain with parametric JONSWAP):
    - Open boundary nodes  → flag 2
    - All other nodes      → flag 0

The element connectivity block is copied verbatim from hgrid.gr3 since
wwmbnd.gr3 uses the same mesh as SCHISM.

Resume-safe: skips if fix/wwmbnd.gr3 already exists and is non-empty.

Usage (called interactively by SchismWwmDriver.preprocess):
    run_gen_wwmbnd(cfg)
"""

import sys
from pathlib import Path

from workflow.core.config import model_dir


# =============================================================================
# Main generator
# =============================================================================

def gen_wwmbnd(cfg: dict) -> bool:
    """
    Generate fix/wwmbnd.gr3 from fix/hgrid.gr3.

    Open boundary nodes (from hgrid.gr3 boundary block) receive flag 2.
    All other nodes receive flag 0.
    Element connectivity is copied verbatim from hgrid.gr3.

    Returns True on success, False on failure.
    """
    mdir   = model_dir(cfg)
    fix    = mdir / 'fix'
    hgrid  = fix / 'hgrid.gr3'
    wwmbnd = fix / 'wwmbnd.gr3'

    if wwmbnd.exists() and wwmbnd.stat().st_size > 0:
        print(f"  fix/wwmbnd.gr3 already exists ({wwmbnd.stat().st_size // 1024} KB), skipping.")
        return True

    if not hgrid.exists():
        print(f"  ERROR: fix/hgrid.gr3 not found: {hgrid}")
        return False

    print(f"\n{'=' * 60}")
    print(f"  gen_wwmbnd: generating fix/wwmbnd.gr3 from fix/hgrid.gr3")
    print(f"  Open boundary nodes → flag 2 (active, parametric JONSWAP)")
    print(f"  Interior nodes      → flag 0")
    print(f"{'=' * 60}\n")

    # ------------------------------------------------------------------
    # Pass 1: read hgrid.gr3 to collect node coords, open boundary
    # node IDs, and the raw element lines.
    # ------------------------------------------------------------------
    print("  Pass 1: reading hgrid.gr3 ...")

    open_bnd_nodes = set()   # 1-based node IDs on open boundaries
    node_lines     = []      # list of (node_id, lon, lat) tuples
    elem_lines     = []      # raw element lines (strings, verbatim copy)

    with open(hgrid, 'r') as f:
        # Title
        f.readline()

        # Header: ne np
        ne, np_nodes = map(int, f.readline().split())
        print(f"  ne={ne:,}  np={np_nodes:,}")

        # Node block
        node_lines = []
        for _ in range(np_nodes):
            parts = f.readline().split()
            node_lines.append((int(parts[0]),
                               float(parts[1]),
                               float(parts[2])))

        # Element block — store verbatim for output
        print("  Reading element block ...")
        elem_lines = []
        for _ in range(ne):
            elem_lines.append(f.readline())

        # Open boundary block
        line = f.readline()
        if not line:
            print("  WARNING: no boundary block found in hgrid.gr3.")
            print("           wwmbnd.gr3 will have all flags = 0.")
            nope = 0
        else:
            nope = int(line.split()[0])
            f.readline()   # total open boundary nodes (skip)
            for i in range(nope):
                nond = int(f.readline().split()[0])
                bnd_nodes = []
                for _ in range(nond):
                    bnd_nodes.append(int(f.readline().strip()))
                open_bnd_nodes.update(bnd_nodes)
                print(f"    Open boundary {i + 1}: {nond:,} nodes")

    print(f"  Total open boundary nodes: {len(open_bnd_nodes):,}")
    print(f"  Writing fix/wwmbnd.gr3 ...")

    # ------------------------------------------------------------------
    # Write wwmbnd.gr3
    # ------------------------------------------------------------------
    # Use a temporary file for atomic write
    tmp = wwmbnd.with_suffix('.gr3.tmp')
    tmp.unlink(missing_ok=True)

    with open(tmp, 'w') as out:
        # Title
        out.write("wwmbnd\n")

        # Header (same ne/np as hgrid)
        out.write(f"{ne} {np_nodes}\n")

        # Node block with flags
        n_active = 0
        for node_id, lon, lat in node_lines:
            flag = 2.0 if node_id in open_bnd_nodes else 0.0
            if flag == 2.0:
                n_active += 1
            out.write(f"{node_id} {lon} {lat} {flag:.1f}\n")

        # Element block (verbatim copy)
        for line in elem_lines:
            out.write(line)

        # wwmbnd.gr3 does NOT need a boundary block — WWM reads only
        # the node flags, not the SCHISM boundary connectivity.

    tmp.replace(wwmbnd)

    size_mb = wwmbnd.stat().st_size / 1024 / 1024
    print(f"\n  Written: {wwmbnd}  ({size_mb:.1f} MB)")
    print(f"  Nodes with flag=2 (active boundary): {n_active:,}")
    print(f"  Nodes with flag=0 (interior):        {np_nodes - n_active:,}")

    return True


# =============================================================================
# Entry point
# =============================================================================

def run_gen_wwmbnd(cfg: dict):
    """
    Generate fix/wwmbnd.gr3. Called by SchismWwmDriver.preprocess().
    This is a one-time step (fix/ file, not per-month).
    """
    ok = gen_wwmbnd(cfg)
    if not ok:
        print("  ERROR: gen_wwmbnd failed.")
        sys.exit(1)
    print("  gen_wwmbnd complete.")
