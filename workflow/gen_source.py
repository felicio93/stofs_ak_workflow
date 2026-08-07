"""
gen_source.py
=============
Phase 3 (interactive) — Generate SCHISM source.nc river forcing files from
GloFAS reanalysis data.

One source.nc per calendar month:
    I{ID}/I{ID}_YYYYMM/source.nc

Static inputs (place in fix/ once; only redo if mesh boundary changes):
    fix/source_glofas.csv   — GloFAS extraction points.
                              Columns: id, lon, lat
                              One row per river. lon/lat should coincide with
                              a GloFAS 0.05° grid node.

    fix/source_schism.csv   — Same rivers repositioned inside the SCHISM mesh.
                              Columns: id, lon, lat
                              id values must match source_glofas.csv exactly.

    fix/hgrid.gr3           — SCHISM unstructured mesh.

Per-year inputs (from download_glofas step):
    raw/glofas/{YYYY}/glofas_{YYYY}.nc
    Variable: avg_dis (m³/s), dimensions: (valid_time, latitude, longitude)

Output — source.nc format (SCHISM source_sink.nml reader):
    Dimensions:
        nsources          — number of unique SCHISM source elements
        nsinks            — 1  (dummy; SCHISM requires the sink block)
        time_vsource      — number of daily timesteps in the month
        time_msource      — same as time_vsource
        time_vsink        — same as time_vsource
        ntracers          — 2  (temperature, salinity)
        one               — 1  (scalar timestep storage)
    Variables:
        source_elem       (nsources)                — 1-based element IDs
        vsource           (time_vsource, nsources)  — discharge  m³/s
        msource           (time_msource, ntracers, nsources)
                                                    — T=-9999, S=0
        sink_elem         (nsinks)                  — dummy [1]
        vsink             (time_vsink, nsinks)       — zeros
        time_step_vsource (one)                     — 86400.0  (daily)
        time_step_msource (one)                     — 86400.0
        time_step_vsink   (one)                     — 86400.0

T/S treatment:
    Temperature : -9999.0  →  SCHISM uses ambient ocean temperature
    Salinity    :     0.0  →  freshwater

Resume-safe: months with an existing non-empty source.nc are skipped.
"""

import sys
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow.config import model_dir, list_months, ProgressTracker
from workflow.mesh_parser import read_element_centroids


# =============================================================================
# CSV readers
# =============================================================================

# Accepted column name variants (case-insensitive, stripped of whitespace)
_LON_ALIASES = {"lon", "longitude", "x", "long"}
_LAT_ALIASES = {"lat", "latitude",  "y"}
_ID_ALIASES  = {"id", "fid", "river_id", "riverid"}


def _match_col(fieldnames: list, aliases: set, label: str, file_name: str) -> str:
    """
    Return the actual column name from fieldnames whose lowercase value is in
    aliases. Exits with an error if no match is found.
    """
    for col in fieldnames:
        if col.strip().lower() in aliases:
            return col
    print(f"ERROR: {file_name}: could not find a {label} column.")
    print(f"  Accepted names: {sorted(aliases)}")
    print(f"  Found columns : {fieldnames}")
    sys.exit(1)


def _read_csv(path: Path, label: str):
    """
    Read a source CSV with header row containing id, lon, lat columns.
    Column names are matched case-insensitively and accept common variants:
        id        : id, fid, river_id, riverid
        longitude : lon, longitude, x, long
        latitude  : lat, latitude, y
    Returns a list of dicts: [{"id": int, "lon": float, "lat": float}, ...]
    """
    import csv
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        id_col  = _match_col(fieldnames, _ID_ALIASES,  "id",        path.name)
        lon_col = _match_col(fieldnames, _LON_ALIASES, "longitude", path.name)
        lat_col = _match_col(fieldnames, _LAT_ALIASES, "latitude",  path.name)

        for row in reader:
            rows.append({
                "id":  int(row[id_col]),
                "lon": float(row[lon_col]),
                "lat": float(row[lat_col]),
            })
    return rows


# =============================================================================
# GloFAS extraction
# =============================================================================

def _glofas_time_indices(nc_path: Path, year: int, month: int):
    """
    Return the integer indices into the valid_time axis of nc_path that
    correspond to the given year/month.
    """
    import netCDF4 as nc4
    with nc4.Dataset(nc_path) as ds:
        vt = ds.variables["valid_time"][:]
    times = [datetime.fromtimestamp(int(t), tz=timezone.utc) for t in vt]
    return [i for i, t in enumerate(times) if t.year == year and t.month == month]


def _extract_discharge(nc_path: Path, lon_target: float, lat_target: float,
                       time_idx: list) -> np.ndarray:
    """
    Extract avg_dis time series for the GloFAS cell nearest to
    (lon_target, lat_target), returning only the timesteps in time_idx.
    NaN → 0, negatives clamped to 0.
    """
    import netCDF4 as nc4
    with nc4.Dataset(nc_path) as ds:
        lon = ds.variables["longitude"][:]
        lat = ds.variables["latitude"][:]

        ilon = int(np.argmin(np.abs(lon - lon_target)))
        ilat = int(np.argmin(np.abs(lat - lat_target)))

        v  = ds.variables["avg_dis"]
        ts = np.ma.filled(v[time_idx, ilat, ilon], fill_value=np.nan)

    ts = np.where(np.isnan(ts), 0.0, ts)
    ts = np.maximum(ts, 0.0)
    return ts.astype(np.float64)


# =============================================================================
# source.nc writer
# =============================================================================

def _write_source_nc(out_path: Path, source_elem: np.ndarray,
                     vsource: np.ndarray, dt_sec: float = 86400.0):
    """Write a SCHISM-compatible source.nc."""
    import xarray as xr

    ntimesteps, nsources = vsource.shape

    # msource: T = -9999 (ambient), S = 0 (freshwater)
    msource = np.full((ntimesteps, 2, nsources), fill_value=-9999.0,
                      dtype=np.float64)
    msource[:, 1, :] = 0.0

    ds = xr.Dataset({
        "source_elem":        (["nsources"],                    source_elem.astype(np.int64)),
        "vsource":            (["time_vsource", "nsources"],    vsource),
        "msource":            (["time_msource", "ntracers", "nsources"], msource),
        "sink_elem":          (["nsinks"],                      np.ones(1, dtype=np.int64)),
        "vsink":              (["time_vsink", "nsinks"],        np.zeros((ntimesteps, 1))),
        "time_step_vsource":  (["one"],                         [dt_sec]),
        "time_step_msource":  (["one"],                         [dt_sec]),
        "time_step_vsink":    (["one"],                         [dt_sec]),
    })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    ds.to_netcdf(str(out_path), format="NETCDF4")


# =============================================================================
# Per-month processor
# =============================================================================

def _process_month(ym: str, cfg: dict, rivers: list,
                   elem_ids: np.ndarray, centroids: np.ndarray,
                   mdir: Path) -> bool:
    """
    Build source.nc for one calendar month (YYYYMM string).
    Returns True on success, False on failure.
    """
    from scipy.spatial import KDTree

    year  = int(ym[:4])
    month = int(ym[4:])
    ndays = monthrange(year, month)[1]

    pid    = cfg["project_id"]
    out_nc = mdir / f"I{pid}" / f"I{pid}_{ym}" / "source.nc"

    if out_nc.exists() and out_nc.stat().st_size > 0:
        print(f"  {ym}: source.nc already exists, skipping.")
        return True

    glofas_nc = mdir / "raw" / "glofas" / str(year) / f"glofas_{year}.nc"
    if not (glofas_nc.exists() and glofas_nc.stat().st_size > 0):
        print(f"  ERROR {ym}: {glofas_nc.name} not found — run download_glofas first.")
        return False

    # Which timesteps in the annual file belong to this month?
    time_idx = _glofas_time_indices(glofas_nc, year, month)
    if not time_idx:
        print(f"  ERROR {ym}: no GloFAS timesteps found for {year}-{month:02d}.")
        return False
    if len(time_idx) != ndays:
        print(f"  WARNING {ym}: expected {ndays} days, found {len(time_idx)} "
              f"in GloFAS — using available data.")

    ntimesteps = len(time_idx)

    # Match source_schism points → nearest SCHISM element (KDTree on centroids)
    tree = KDTree(centroids)
    schism_coords = np.array([[r["schism_lon"] % 360, r["schism_lat"]]
                               for r in rivers])
    dists, nearest_idx = tree.query(schism_coords)

    # Log distance for every river so the user can spot mismatches
    print(f"  {ym}: element matching distances (degrees):")
    for r, d, idx in zip(rivers, dists, nearest_idx):
        flag = "  *** LARGE ***" if d > 0.5 else ""
        print(f"    id={r['id']:>4d}  dist={d:.4f}°  elem={elem_ids[idx]}{flag}")

    for r, idx in zip(rivers, nearest_idx):
        r["schism_elem"] = int(elem_ids[idx])

    # Aggregate: unique SCHISM elements — sum if multiple rivers → same element
    unique_elems = np.unique([r["schism_elem"] for r in rivers])
    nsources     = len(unique_elems)
    vsource      = np.zeros((ntimesteps, nsources), dtype=np.float64)

    elem_to_idx = {e: i for i, e in enumerate(unique_elems)}
    for r in rivers:
        s_idx = elem_to_idx[r["schism_elem"]]
        ts    = _extract_discharge(glofas_nc,
                                   r["glofas_lon"] % 360,
                                   r["glofas_lat"],
                                   time_idx)
        vsource[:, s_idx] += ts

    _write_source_nc(out_nc, unique_elems, vsource)
    size_kb = out_nc.stat().st_size // 1024
    print(f"  {ym}: wrote source.nc — "
          f"{nsources} source elements, {ntimesteps} timesteps, {size_kb} KB")
    return True


# =============================================================================
# Main entry point
# =============================================================================

def run_gen_source(cfg: dict):
    import pandas as pd

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    fix  = mdir / "fix"

    # ------------------------------------------------------------------
    # Validate required files
    # ------------------------------------------------------------------
    csv_glofas = fix / "source_glofas.csv"
    csv_schism = fix / "source_schism.csv"
    hgrid      = fix / "hgrid.gr3"

    for p in [csv_glofas, csv_schism, hgrid]:
        if not p.exists():
            print(f"ERROR: required file not found: {p}")
            print("  Copy source_glofas.csv, source_schism.csv and hgrid.gr3 "
                  "into fix/ before running gen_source.")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Load and merge the two CSVs on 'id'
    # ------------------------------------------------------------------
    print("  Loading source CSVs...")
    gf_rows = _read_csv(csv_glofas, "source_glofas.csv")
    sc_rows = _read_csv(csv_schism,  "source_schism.csv")

    gf_df = pd.DataFrame(gf_rows).rename(columns={"lon": "glofas_lon",
                                                    "lat": "glofas_lat"})
    sc_df = pd.DataFrame(sc_rows).rename(columns={"lon": "schism_lon",
                                                    "lat": "schism_lat"})

    merged = gf_df.merge(sc_df, on="id", how="inner")

    if merged.empty:
        print("ERROR: no matching ids between source_glofas.csv and "
              "source_schism.csv — check that id values are identical.")
        sys.exit(1)

    n_gf   = len(gf_df)
    n_sc   = len(sc_df)
    n_ok   = len(merged)
    print(f"  source_glofas.csv : {n_gf} rivers")
    print(f"  source_schism.csv : {n_sc} rivers")
    print(f"  Matched on id     : {n_ok} rivers")

    if n_ok < n_gf:
        missing = set(gf_df["id"]) - set(merged["id"])
        print(f"  WARNING: ids in source_glofas but not source_schism: "
              f"{sorted(missing)}")
    if n_ok < n_sc:
        missing = set(sc_df["id"]) - set(merged["id"])
        print(f"  WARNING: ids in source_schism but not source_glofas: "
              f"{sorted(missing)}")

    rivers = merged.to_dict("records")

    # ------------------------------------------------------------------
    # Parse mesh centroids once — reused for all months
    # ------------------------------------------------------------------
    print(f"  Parsing element centroids from {hgrid.name} "
          f"(this may take a minute for large meshes)...")
    elem_ids, centroids = read_element_centroids(hgrid)
    print(f"  Mesh: {len(elem_ids):,} elements loaded.")

    # ------------------------------------------------------------------
    # Loop over months
    # ------------------------------------------------------------------
    months = list_months(cfg)
    prog   = ProgressTracker(total=len(months), label="gen_source")
    failed = []

    print(f"\n{'='*60}")
    print(f"  gen_source: {months[0]} -> {months[-1]}  ({len(months)} months)")
    print(f"  Rivers: {n_ok}  |  Output: I{pid}/I{pid}_YYYYMM/source.nc")
    print(f"{'='*60}\n")

    for ym in months:
        ok = _process_month(ym, cfg, rivers, elem_ids, centroids, mdir)
        if not ok:
            failed.append(ym)
        prog.update(ym)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_source complete. No failures.")
    else:
        print(f"  gen_source complete with {len(failed)} failure(s):")
        for m in failed:
            print(f"    {m}")
        print("  Re-run to retry (existing files are skipped).")
    print(f"{'='*60}\n")
