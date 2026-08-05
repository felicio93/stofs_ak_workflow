"""
gen_bctides.py
==============
Step: Generate bctides.in for each month (interactive, swf_main).

For each month, reads the open boundary node coordinates from fix/hgrid.gr3,
interpolates TPXO9 tidal amplitudes and phases to those nodes (using scipy
griddata, identical to pyschism), computes nodal factors and equilibrium
arguments, and writes I{ID}_YYYYMM/bctides.in.

The output is bit-for-bit equivalent to what pyschism's Bctides class would
produce for the same inputs. The TPXO reader and nodal-factor code are copied
verbatim from pyschism (MIT licence) with only the pyschism package import
replaced by local copies in workflow/tidal/.

TPXO9 file locations (same as pyschism defaults):
    ~/.local/share/tpxo/h_tpxo9.v1.nc   (elevation)
    ~/.local/share/tpxo/u_tpxo9.v1.nc   (velocity)
or via environment variables TPXO_ELEVATION / TPXO_VELOCITY.

Sentinel: I{ID}_YYYYMM/bctides.done (per month)
"""

import sys
from calendar import monthrange
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow.config import load_config, list_months, model_dir, ProgressTracker

ALL_TPXO9_CONSTITUENTS = [
    'M2', 'S2', 'N2', 'K2', 'K1', 'O1', 'P1', 'Q1',
    'Mm', 'Mf', 'M4', 'MN4', 'MS4', '2N2', 'S1'
]
MAJOR_CONSTITUENTS = ['Q1', 'O1', 'P1', 'K1', 'N2', 'M2', 'S2', 'K2']


# =============================================================================
# hgrid.gr3 open boundary reader
# =============================================================================

def read_open_boundaries(hgrid_path: Path):
    """
    Read hgrid.gr3 and return a list of (n_nodes, lon_array, lat_array)
    for each open boundary segment, in order.
    """
    with open(hgrid_path) as f:
        f.readline()            # title
        ne, np_nodes = map(int, f.readline().split())
        lons = np.empty(np_nodes)
        lats = np.empty(np_nodes)
        for i in range(np_nodes):
            parts = f.readline().split()
            lons[i] = float(parts[1])
            lats[i] = float(parts[2])
        # Skip elements
        for _ in range(ne):
            f.readline()
        # Open boundaries
        nope = int(f.readline().split()[0])
        neta = int(f.readline().split()[0])  # total open boundary nodes
        boundaries = []
        for _ in range(nope):
            nond = int(f.readline().split()[0])
            node_ids = []
            for _ in range(nond):
                node_ids.append(int(f.readline().strip()) - 1)  # 0-based
            node_ids = np.array(node_ids)
            boundaries.append((nond, lons[node_ids], lats[node_ids]))
    return boundaries


# =============================================================================
# bctides.in writer
# =============================================================================

def resolve_constituents(cfg: dict) -> list:
    """Resolve tidal_constituents config to an ordered list of names."""
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
        print(f"  ERROR: tidal_constituents must be 'major', 'all', or a list.")
        sys.exit(1)


def write_bctides(out_path: Path, start_date: datetime, rnday: int,
                  constituents: list, tides, boundaries: list,
                  flags: list, tobc: list, sobc: list,
                  cutoff_depth: float = 50.0,
                  add_earth_tidal: bool = True):
    """
    Write one bctides.in file. Matches pyschism output format exactly.

    tides: Tides object (from workflow/tidal/tides.py)
    boundaries: list of (n_nodes, lons, lats) per boundary
    flags: [[iettype,ifltype,itetype,isatype], ...] per boundary
    """
    from workflow.tidal.tpxo import TPXO

    # Resolve tidal data provider
    tpxo = TPXO()

    lines = []

    # Header: simulation start date
    lines.append(f"!{start_date.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    # --- Earth tidal potential section ---
    if add_earth_tidal:
        etp_constituents = [c for c in constituents
                            if tides.get_tidal_species_type(c) in (1, 2)]
        lines.append(f" {len(etp_constituents)}  {cutoff_depth:.3f} "
                     f"!number of earth tidal potential, cut-off depth for "
                     f"applying tidal potential")
        for c in etp_constituents:
            species  = tides.get_tidal_species_type(c)
            amp      = tides.get_amplitude(c, 'elevation')
            freq     = tides.get_frequency(c)
            nodal    = tides.get_nodal_factor(start_date, rnday, c)
            ear      = tides.get_earth_equilibrium_argument(start_date, rnday, c)
            lines.append(c)
            lines.append(f" {species} {amp:.6f} {freq:.9e} {nodal:7.5f}  {ear:.2f}")
    else:
        lines.append(f" 0  {cutoff_depth:.3f} !no earth tidal potential")

    # --- nbfr (number of tidal boundary forcing frequencies) ---
    lines.append(f"{len(constituents)} !nbfr")
    for c in constituents:
        freq  = tides.get_frequency(c)
        nodal = tides.get_nodal_factor(start_date, rnday, c)
        ear   = tides.get_earth_equilibrium_argument(start_date, rnday, c)
        lines.append(c)
        lines.append(f"  {freq:.9e}  {nodal:7.5f}  {ear:.2f}")

    # --- nope ---
    lines.append(f"{len(boundaries)} !nope")

    for ibnd, ((nond, lons, lats), flag) in enumerate(zip(boundaries, flags)):
        iettype, ifltype, itetype, isatype = flag
        flag_str = " ".join(str(f) for f in flag)
        lines.append(f"{nond} {flag_str} !open bnd {ibnd+1}")

        vertices = np.column_stack([lons, lats])

        # --- Elevation ---
        if iettype in (3, 5):
            for c in constituents:
                amp, phase = tpxo.get_elevation(c, vertices)
                lines.append(c)
                for i in range(nond):
                    lines.append(f"{amp[i]: .6f} {phase[i]: .6f}")

        # --- Velocity ---
        if ifltype in (3, 5):
            for c in constituents:
                uamp, uphase, vamp, vphase = tpxo.get_velocity(c, vertices)
                lines.append(c)
                for i in range(nond):
                    lines.append(f"{uamp[i]: .6f} {uphase[i]: .6f} "
                                 f"{vamp[i]: .6f} {vphase[i]: .6f}")

        # --- T/S nudging ---
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
    hgrid = fix / "hgrid.ll"   # lon/lat coordinates
    if not hgrid.exists():
        hgrid = fix / "hgrid.gr3"   # fallback
    if not hgrid.exists():
        print(f"ERROR: hgrid.ll (or hgrid.gr3) not found in fix/")
        sys.exit(1)

    constituents  = resolve_constituents(cfg)
    flags         = cfg.get("open_boundary_flags", [[5,5,4,4],[5,5,4,4]])
    tobc          = cfg.get("tobc",  [0.01] * len(flags))
    sobc          = cfg.get("sobc",  [0.01] * len(flags))
    cutoff        = float(cfg.get("tidal_cutoff_depth", 50.0))
    months        = list_months(cfg)

    print(f"\n{'='*60}")
    print(f"  gen_bctides for M{pid}")
    print(f"  Constituents: {constituents}")
    print(f"  Boundaries: {len(flags)}  flags: {flags}")
    print(f"  Months: {months[0]} -> {months[-1]}")
    print(f"{'='*60}\n")

    # Read open boundary nodes once (same for all months)
    print(f"  Reading open boundaries from {hgrid.name} ...")
    boundaries = read_open_boundaries(hgrid)
    print(f"  Found {len(boundaries)} open boundary segment(s):")
    for i, (n, lons, lats) in enumerate(boundaries):
        print(f"    Boundary {i+1}: {n} nodes")

    if len(boundaries) != len(flags):
        print(f"  ERROR: {len(boundaries)} boundaries in hgrid but "
              f"{len(flags)} flag sets in schism.yaml")
        sys.exit(1)

    prog   = ProgressTracker(total=len(months), label="gen_bctides")
    failed = []

    for ym in months:
        year  = int(ym[:4])
        month = int(ym[4:])
        idir  = mdir / f"I{pid}" / f"I{pid}_{ym}"
        idir.mkdir(parents=True, exist_ok=True)
        sentinel = idir / "bctides.done"

        if sentinel.exists():
            print(f"  {ym}: already done, skipping.")
            prog.update(ym)
            continue

        print(f"\n--- {ym} ---")
        start_date = datetime(year, month, 1, 0, 0, 0)
        ndays      = monthrange(year, month)[1]

        # Build Tides object for this month's start date
        tides = Tides(tidal_database='tpxo', constituents=constituents)

        try:
            write_bctides(
                out_path    = idir / "bctides.in",
                start_date  = start_date,
                rnday       = ndays,
                constituents= constituents,
                tides       = tides,
                boundaries  = boundaries,
                flags       = flags,
                tobc        = tobc,
                sobc        = sobc,
                cutoff_depth= cutoff,
            )
            sentinel.touch()
            print(f"  Written: {idir/'bctides.in'}")
        except Exception as exc:
            print(f"  ERROR for {ym}: {exc}")
            failed.append(ym)

        prog.update(ym)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_bctides complete. No failures.")
    else:
        print(f"  gen_bctides complete with {len(failed)} failure(s):")
        for m in failed:
            print(f"    {m}")
    print(f"{'='*60}\n")
