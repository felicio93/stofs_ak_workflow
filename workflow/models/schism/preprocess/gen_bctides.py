"""
models/schism/preprocess/gen_bctides.py
==============
Step: Generate bctides.in for each group (interactive, swf_main).

Works for all grouping modes (monthly, ndays/weekly/daily).
The tidal constituent computation (nodal factors, Greenwich equilibrium
arguments) uses the group start date and the group length in days.
"""

import sys
from pathlib import Path

import numpy as np

from workflow.core.config import (
    load_config,
    list_groups,
    model_dir,
    ProgressTracker,
    group_date_range,
    get_group_ndays,
)
from workflow.core.mesh_parser import read_open_boundaries

ALL_TPXO9_CONSTITUENTS = [
    'M2', 'S2', 'N2', 'K2', 'K1', 'O1', 'P1', 'Q1',
    'Mm', 'Mf', 'M4', 'MN4', 'MS4', '2N2', 'S1'
]
MAJOR_CONSTITUENTS = ['Q1', 'O1', 'P1', 'K1', 'N2', 'M2', 'S2', 'K2']


# =============================================================================
# bctides.in writer
# =============================================================================

def resolve_constituents(cfg: dict) -> list:
    spec = cfg.get("tidal_constituents", "major")
    if spec == "major":
        return MAJOR_CONSTITUENTS[:]
    elif spec == "all":
        return ALL_TPXO9_CONSTITUENTS[:]
    elif isinstance(spec, list):
        invalid = [c for c in spec if c not in ALL_TPXO9_CONSTITUENTS]
        if invalid:
            print(f"  ERROR: unknown constituent(s): {invalid}")
            print(f"  Available: {ALL_TPXO9_CONSTITUENTS}")
            sys.exit(1)
        return list(spec)
    else:
        print("  ERROR: tidal_constituents must be 'major', 'all', "
              "or a list.")
        sys.exit(1)


def write_bctides(out_path: Path, start_date, rnday: int,
                  constituents: list, tides, boundaries: list,
                  flags: list, tobc: list, sobc: list,
                  cutoff_depth: float = 50.0,
                  add_earth_tidal: bool = True):
    """Write one bctides.in file. Matches pyschism output format exactly."""
    from datetime import datetime
    from workflow.tidal.tpxo import TPXO

    # Accept both date and datetime objects
    if not isinstance(start_date, datetime):
        start_date = datetime(start_date.year, start_date.month,
                              start_date.day, 0, 0, 0)

    tpxo  = TPXO()
    lines = []

    lines.append(
        f"!{start_date.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    if add_earth_tidal:
        etp_constituents = [
            c for c in constituents
            if tides.get_tidal_species_type(c) in (1, 2)
        ]
        lines.append(
            f" {len(etp_constituents)}  {cutoff_depth:.3f} "
            f"!number of earth tidal potential, cut-off depth for "
            f"applying tidal potential")
        for c in etp_constituents:
            species  = tides.get_tidal_species_type(c)
            amp      = tides.get_tidal_potential_amplitude(c)
            freq     = tides.get_orbital_frequency(c)
            nodal    = tides.get_nodal_factor(start_date, rnday, c)
            ear      = tides.get_greenwich_factor(start_date, rnday, c)
            lines.append(c)
            lines.append(
                f" {species} {amp:.6f} {freq:.9e} "
                f"{nodal:7.5f}  {ear:.2f}")
    else:
        lines.append(
            f" 0  {cutoff_depth:.3f} !no earth tidal potential")

    lines.append(f"{len(constituents)} !nbfr")
    for c in constituents:
        freq  = tides.get_orbital_frequency(c)
        nodal = tides.get_nodal_factor(start_date, rnday, c)
        ear   = tides.get_greenwich_factor(start_date, rnday, c)
        lines.append(c)
        lines.append(f"  {freq:.9e}  {nodal:7.5f}  {ear:.2f}")

    lines.append(f"{len(boundaries)} !nope")

    for ibnd, ((nond, lons, lats), flag) in enumerate(
            zip(boundaries, flags)):
        iettype, ifltype, itetype, isatype = flag
        flag_str = " ".join(str(f) for f in flag)
        lines.append(f"{nond} {flag_str} !open bnd {ibnd+1}")

        vertices = np.column_stack([lons, lats])

        if iettype in (3, 5):
            for c in constituents:
                amp, phase = tpxo.get_elevation(c, vertices)
                lines.append(c)
                for i in range(nond):
                    lines.append(f"{amp[i]: .6f} {phase[i]: .6f}")

        if ifltype in (3, 5):
            for c in constituents:
                uamp, uphase, vamp, vphase = tpxo.get_velocity(
                    c, vertices)
                lines.append(c)
                for i in range(nond):
                    lines.append(
                        f"{uamp[i]: .6f} {uphase[i]: .6f} "
                        f"{vamp[i]: .6f} {vphase[i]: .6f}")

        if itetype != 0:
            lines.append(f"{tobc[ibnd]} !nudging factor for T")
        if isatype != 0:
            lines.append(f"{sobc[ibnd]} !nudging factor for S")

    out_path.write_text("\n".join(lines) + "\n")


# =============================================================================
# Main
# =============================================================================

def run_gen_bctides(cfg: dict):
    from workflow.tidal.tides import Tides

    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    fix   = mdir / "fix"

    # Prefer hgrid.ll for open boundary coordinates; fall back to hgrid.gr3
    hgrid = fix / "hgrid.ll"
    if not hgrid.exists():
        hgrid = fix / "hgrid.gr3"
    if not hgrid.exists():
        print("ERROR: hgrid.ll (or hgrid.gr3) not found in fix/")
        sys.exit(1)

    constituents = resolve_constituents(cfg)
    flags        = cfg.get("open_boundary_flags",
                           [[5, 5, 4, 4], [5, 5, 4, 4]])
    tobc         = cfg.get("tobc",  [0.01] * len(flags))
    sobc         = cfg.get("sobc",  [0.01] * len(flags))
    cutoff       = float(cfg.get("tidal_cutoff_depth", 50.0))
    groups       = list_groups(cfg)
    grouping     = cfg.get("grouping", "monthly")

    print(f"\n{'='*60}")
    print(f"  gen_bctides for M{pid}")
    print(f"  Grouping     : {grouping}"
          + (f"  (group_ndays={cfg['group_ndays']})"
             if grouping == "ndays" else ""))
    print(f"  Constituents : {constituents}")
    print(f"  Boundaries   : {len(flags)}  flags: {flags}")
    print(f"  Groups       : {groups[0]} -> {groups[-1]}")
    print(f"{'='*60}\n")

    # Read open boundary nodes once — same mesh for all groups
    print(f"  Reading open boundaries from {hgrid.name} ...")
    boundaries = read_open_boundaries(hgrid)
    print(f"  Found {len(boundaries)} open boundary segment(s):")
    for i, (n, lons, lats) in enumerate(boundaries):
        print(f"    Boundary {i+1}: {n} nodes")

    if len(boundaries) != len(flags):
        print(f"  ERROR: {len(boundaries)} boundaries in hgrid but "
              f"{len(flags)} flag sets in schism.yaml")
        sys.exit(1)

    prog   = ProgressTracker(total=len(groups), label="gen_bctides")
    failed = []

    for group_id in groups:
        idir     = mdir / f"I{pid}" / f"I{pid}_{group_id}"
        idir.mkdir(parents=True, exist_ok=True)
        sentinel = idir / "bctides.done"

        if sentinel.exists():
            print(f"  {group_id}: already done, skipping.")
            prog.update(group_id)
            continue

        print(f"\n--- {group_id} ---")

        gstart, _ = group_date_range(cfg, group_id)
        ndays     = get_group_ndays(cfg, group_id)

        tides = Tides(tidal_database='tpxo',
                      constituents=constituents)

        try:
            write_bctides(
                out_path     = idir / "bctides.in",
                start_date   = gstart,
                rnday        = ndays,
                constituents = constituents,
                tides        = tides,
                boundaries   = boundaries,
                flags        = flags,
                tobc         = tobc,
                sobc         = sobc,
                cutoff_depth = cutoff,
            )
            sentinel.touch()
            print(f"  Written: {idir / 'bctides.in'}  "
                  f"(start={gstart}, rnday={ndays})")
        except Exception as exc:
            print(f"  ERROR for {group_id}: {exc}")
            failed.append(group_id)

        prog.update(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_bctides complete. No failures.")
    else:
        print(f"  gen_bctides complete with {len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
    print(f"{'='*60}\n")
