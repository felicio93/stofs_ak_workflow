"""
gen_estuary.py
==============
Step A (interactive, run once before any SCHISM Fortran steps).

Does three things:
1. Reads fix/hgrid.gr3, applies the estuary depth threshold from domain.yaml,
   and writes fix/estuary.gr3 (0 = open ocean, 1 = estuary/shallow).
2. Generates the three Fortran .in control files into M{ID}/bin/:
       gen_hot_from_nc.in
       gen_3Dth_from_nc.in
       gen_nudge_from_nc.in
3. Prints important reminders about the lon convention and no-scaling
   assumption that must be respected when running the Fortran executables.

All outputs are skipped if they already exist (safe to re-run).

IMPORTANT NOTES printed at runtime:
  LON CONVENTION : The Fortran line 'lon=lon-360' is commented out in the
                   _noscaling executables.  This is correct ONLY when both
                   hgrid.ll AND the HYCOM files use the same 0-360 convention.
  NO SCALING     : The _noscaling executables expect UNPACKED float data.
                   Our HYCOM files are unpacked at download (ncpdq -U).
                   DO NOT use the stock SCHISM executables -- they apply
                   scale_factor/add_offset a second time, giving wrong values.
                   rjunk = -29999.0 (fill-value detection for unpacked data).
"""

import sys
from pathlib import Path

from workflow.config import model_dir

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
  ╚══════════════════════════════════════════════════════════╝
"""


def _already_done(paths):
    """Return True if all paths exist and are non-empty."""
    return all(p.exists() and p.stat().st_size > 0 for p in paths)


def generate_estuary_gr3(hgrid_path: Path, out_path: Path, threshold: float):
    """
    Read hgrid.gr3 and write estuary.gr3 with the same node/element
    connectivity but with depth replaced by:
        1  if  depth <= threshold  (estuary / shallow)
        0  otherwise               (open ocean)

    gr3 format:
        line 1: comment (title)
        line 2: ne np
        lines 3..np+2: node_id  x  y  depth
        lines np+3..: element connectivity (unchanged)
    """
    print(f"  Reading {hgrid_path} ...")
    with open(hgrid_path) as f:
        lines = f.readlines()

    title = lines[0]
    ne, np_nodes = map(int, lines[1].split())

    node_lines = lines[2: 2 + np_nodes]
    elem_lines = lines[2 + np_nodes:]

    n_estuary = 0
    new_node_lines = []
    for line in node_lines:
        parts = line.split()
        nid, x, y, depth = parts[0], parts[1], parts[2], float(parts[3])
        flag = 1 if depth <= threshold else 0
        if flag == 1:
            n_estuary += 1
        new_node_lines.append(f"{nid} {x} {y} {float(flag):.1f}\n")

    print(f"  Depth threshold: {threshold} m  "
          f"-> {n_estuary:,} estuary nodes / {np_nodes:,} total")

    with open(out_path, "w") as f:
        f.write("estuary\n")
        f.write(f"{ne} {np_nodes}\n")
        f.writelines(new_node_lines)
        f.writelines(elem_lines)

    print(f"  Written: {out_path}")


def generate_in_files(bin_dir: Path, cfg: dict):
    """Generate the three Fortran .in control files into bin_dir."""

    et  = float(cfg.get("estuary_temp", 10.0))
    es  = float(cfg.get("estuary_sal",  0.0))
    ot  = float(cfg.get("outside_temp", 10.0))
    os_ = float(cfg.get("outside_sal",  0.0))
    dt  = float(cfg.get("hycom_dt",     86400.0))
    ns  = int(cfg.get("nudge_stride",   1))
    nb  = int(cfg.get("nbin",           60000))
    mb  = int(cfg.get("mne_bin",        1600))
    obs = cfg.get("open_boundaries", [1, 2])
    nob = len(obs)
    obs_str = " ".join(str(i) for i in obs)

    # --- gen_hot_from_nc.in ---
    hot_in = bin_dir / "gen_hot_from_nc.in"
    hot_content = (
        f"1                      !1: include vel+elev in hotstart; 0: T,S only\n"
        f"{et} {es}              !T,S for estuary points (estuary.gr3)\n"
        f"{ot} {os_}             !T,S for nodes outside HYCOM grid\n"
        f"1                      !is_xy\n"
        f"{nb}                   !nbin\n"
        f"{mb}                   !mne_bin\n"
    )
    hot_in.write_text(hot_content)
    print(f"  Written: {hot_in}")

    # --- gen_3Dth_from_nc.in ---
    th_in = bin_dir / "gen_3Dth_from_nc.in"
    th_content = (
        f"{ot} {os_}             !T,S for nodes outside HYCOM grid\n"
        f"{dt}                   !time step in .nc [sec]\n"
        f"{nob} {obs_str}        !# of open bnds; list of IDs\n"
        f"32                     !# of days needed (fixed ceiling; file ends naturally)\n"
        f"1                      !# of HYCOM stacks\n"
    )
    th_in.write_text(th_content)
    print(f"  Written: {th_in}")

    # --- gen_nudge_from_nc.in ---
    nu_in = bin_dir / "gen_nudge_from_nc.in"
    nu_content = (
        f"0                      !inu_or_surf (0=nudging output; 1=surface restore)\n"
        f"{ot} {os_}             !T,S for nodes outside HYCOM grid\n"
        f"{dt} {ns}              !time step in .nc [sec]; output stride\n"
        f"1                      !# of nc files (stacks)\n"
    )
    nu_in.write_text(nu_content)
    print(f"  Written: {nu_in}")


def run_gen_estuary(cfg: dict):
    print(REMINDERS)

    pid       = cfg["project_id"]
    mdir      = model_dir(cfg)
    fix_dir   = mdir / "fix"
    bin_dir   = mdir / "bin"
    threshold = float(cfg.get("estuary_depth_threshold", 3.0))

    hgrid   = fix_dir / "hgrid.gr3"
    estuary = fix_dir / "estuary.gr3"
    hot_in  = bin_dir / "gen_hot_from_nc.in"
    th_in   = bin_dir / "gen_3Dth_from_nc.in"
    nu_in   = bin_dir / "gen_nudge_from_nc.in"

    if not hgrid.exists():
        print(f"ERROR: {hgrid} not found. Copy hgrid.gr3 to fix/ first.")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  gen_estuary for project M{pid}")
    print(f"  fix/  : {fix_dir}")
    print(f"  bin/  : {bin_dir}")
    print(f"{'='*60}\n")

    # --- estuary.gr3 ---
    if estuary.exists() and estuary.stat().st_size > 0:
        print(f"  fix/estuary.gr3 already exists, skipping.")
    else:
        generate_estuary_gr3(hgrid, estuary, threshold)

    # --- .in files ---
    if _already_done([hot_in, th_in, nu_in]):
        print(f"  bin/*.in files already exist, skipping.")
    else:
        print(f"\n  Generating Fortran .in control files ...")
        generate_in_files(bin_dir, cfg)

    print(f"\n{'='*60}")
    print("  gen_estuary complete.")
    print("  Next: set gen_hotstart/gen_3Dth/gen_nudge = true in steps.yaml")
    print(f"{'='*60}\n")
