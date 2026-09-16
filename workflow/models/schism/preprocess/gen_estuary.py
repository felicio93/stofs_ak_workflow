"""
models/schism/preprocess/gen_estuary.py
==============
Step A (interactive, run once before any SCHISM Fortran steps).

Does two things:
1. Reads fix/hgrid.gr3, applies the estuary depth threshold from domain.yaml,
   and writes fix/estuary.gr3 (0 = open ocean, 1 = estuary/shallow).
2. Generates gen_hot_from_nc.in into M{ID}/bin/ (this file does not
   contain a stack ceiling so it is safe to write once).

NOTE: gen_3Dth_from_nc.in and gen_nudge_from_nc.in are NO LONGER written
here. They are written per-group by gen_hycom_utils.py because they contain
the stack ceiling value which depends on the group length.

All outputs are skipped if they already exist (safe to re-run).

IMPORTANT NOTES printed at runtime:
  LON CONVENTION : The Fortran line 'lon=lon-360' is commented out in the
                   _noscaling executables. This is correct ONLY when both
                   hgrid.ll AND the HYCOM files use the same 0-360 convention.
  NO SCALING     : The _noscaling executables expect UNPACKED float data.
                   Our HYCOM files are unpacked at download (ncpdq -U).
                   DO NOT use the stock SCHISM executables.
"""

import sys
from pathlib import Path

import numpy as np

from workflow.core.config import model_dir

REMINDERS = """
  ╔══════════════════════════════════════════════════════════╗
  ║  IMPORTANT: SCHISM Fortran executable assumptions        ║
  ╠══════════════════════════════════════════════════════════╣
  ║  1. LON CONVENTION                                       ║
  ║     The line 'lon=lon-360' is COMMENTED OUT in the       ║
  ║     _noscaling executables. This is correct ONLY when    ║
  ║     both hgrid.ll AND the HYCOM files use the SAME       ║
  ║     longitude convention (both 0-360 for this project).  ║
  ║     If you change mesh or data source, verify this.      ║
  ║                                                          ║
  ║  2. NO SCALING (_noscaling executables)                  ║
  ║     The executables expect UNPACKED float variables.     ║
  ║     HYCOM files are unpacked at download (ncpdq -U).     ║
  ║     DO NOT use stock SCHISM executables -- they apply    ║
  ║     scale_factor/add_offset again -> completely wrong.   ║
  ║     Fill value detection: rjunk = -29999.0               ║
  ║                                                          ║
  ║  3. STACK CEILING (.in files)                            ║
  ║     gen_3Dth_from_nc.in and gen_nudge_from_nc.in are     ║
  ║     written per-group by gen_hycom_utils.py because they ║
  ║     encode the stack ceiling which depends on group      ║
  ║     length. Do NOT copy them from bin/ manually.         ║
  ╚══════════════════════════════════════════════════════════╝
"""


def _already_done(paths):
    """Return True if all paths exist and are non-empty."""
    return all(p.exists() and p.stat().st_size > 0 for p in paths)


def generate_estuary_gr3(hgrid_path: Path, out_path: Path,
                         threshold: float):
    """Read hgrid.gr3 and write estuary.gr3.

    Node value:
        1  if  depth <= threshold  (estuary / shallow)
        0  otherwise               (open ocean)
    """
    from workflow.core.mesh_parser import read_nodes

    print(f"  Reading {hgrid_path} ...")
    node_ids, lons, lats, depths = read_nodes(hgrid_path)
    np_nodes = len(node_ids)

    flags     = np.where(depths <= threshold, 1.0, 0.0)
    n_estuary = int((flags == 1).sum())
    print(f"  Depth threshold: {threshold} m  "
          f"-> {n_estuary:,} estuary nodes / {np_nodes:,} total")

    new_node_lines = [
        f"{node_ids[i]} {lons[i]} {lats[i]} {flags[i]:.1f}\n"
        for i in range(np_nodes)
    ]

    with open(hgrid_path) as f:
        lines = f.readlines()
    ne, _ = map(int, lines[1].split())
    elem_and_bnd_lines = lines[2 + np_nodes:]

    with open(out_path, "w") as f:
        f.write("estuary\n")
        f.write(f"{ne} {np_nodes}\n")
        f.writelines(new_node_lines)
        f.writelines(elem_and_bnd_lines)

    print(f"  Written: {out_path}")


def generate_hot_in(bin_dir: Path, cfg: dict):
    """Generate gen_hot_from_nc.in into bin_dir.

    This file does NOT contain the stack ceiling, so it is safe to
    write once and reuse across all groups.
    """
    et  = float(cfg.get("estuary_temp", 10.0))
    es  = float(cfg.get("estuary_sal",  0.0))
    ot  = float(cfg.get("outside_temp", 10.0))
    os_ = float(cfg.get("outside_sal",  0.0))
    nb  = int(cfg.get("nbin",    60000))
    mb  = int(cfg.get("mne_bin", 1600))

    hot_in = bin_dir / "gen_hot_from_nc.in"
    hot_content = (
        f"1                      "
        f"!1: include vel+elev in hotstart; 0: T,S only\n"
        f"{et} {es}              "
        f"!T,S for estuary points (estuary.gr3)\n"
        f"{ot} {os_}             "
        f"!T,S for nodes outside HYCOM grid\n"
        f"1                      !is_xy\n"
        f"{nb}                   !nbin\n"
        f"{mb}                   !mne_bin\n"
    )
    hot_in.write_text(hot_content)
    print(f"  Written: {hot_in}")


def run_gen_estuary(cfg: dict):
    print(REMINDERS)

    pid       = cfg["project_id"]
    mdir      = model_dir(cfg)
    fix_dir   = mdir / "fix"
    bin_dir   = mdir / "bin"
    threshold = float(cfg.get("estuary_depth_threshold", -10.0))

    hgrid   = fix_dir / "hgrid.gr3"
    estuary = fix_dir / "estuary.gr3"
    hot_in  = bin_dir / "gen_hot_from_nc.in"

    if not hgrid.exists():
        print(f"ERROR: {hgrid} not found. "
              "Copy hgrid.gr3 to fix/ first.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  gen_estuary for project M{pid}")
    print(f"  fix/  : {fix_dir}")
    print(f"  bin/  : {bin_dir}")
    print(f"{'='*60}\n")

    # --- estuary.gr3 ---
    if estuary.exists() and estuary.stat().st_size > 0:
        print("  fix/estuary.gr3 already exists, skipping.")
    else:
        generate_estuary_gr3(hgrid, estuary, threshold)

    # --- gen_hot_from_nc.in (no stack ceiling — safe to write once) ---
    if hot_in.exists() and hot_in.stat().st_size > 0:
        print("  bin/gen_hot_from_nc.in already exists, skipping.")
    else:
        print("\n  Generating gen_hot_from_nc.in ...")
        generate_hot_in(bin_dir, cfg)

    print(f"\n{'='*60}")
    print("  gen_estuary complete.")
    print("  NOTE: gen_3Dth_from_nc.in and gen_nudge_from_nc.in are")
    print("  written per-group when gen_3Dth / gen_nudge are submitted.")
    print("  Next: set gen_hotstart/gen_3Dth/gen_nudge = true in steps.yaml")
    print(f"{'='*60}\n")
