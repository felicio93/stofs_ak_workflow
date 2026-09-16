"""
models/schism/preprocess/gen_source.py
=============
Phase 3 (interactive) — Generate SCHISM source.nc river forcing files from
GloFAS reanalysis data.

One source.nc per group:
    I{ID}/I{ID}_{group_id}/source.nc

Static inputs (place in fix/ once):
    fix/source_glofas.csv   — GloFAS extraction points.
                              Columns: id, lon, lat
    fix/source_schism.csv   — Same rivers repositioned inside the SCHISM mesh.
                              Columns: id, lon, lat
    fix/hgrid.gr3           — SCHISM unstructured mesh.

Per-year inputs (from download_glofas step):
    raw/glofas/{YYYY}/glofas_{YYYY}.nc
    Variable: avg_dis (m³/s), dimensions: (valid_time, latitude, longitude)

Stack length
------------
Every source.nc is built to stack_ceiling(cfg, group_id) daily records
(group length + 3 read-ahead buffer) so SCHISM's source/sink reader never
runs out of records.

Output — source.nc format:
    Dimensions:
        nsources          — number of unique SCHISM source elements
        nsinks            — 1  (dummy)
        time_vsource      — stack_ceiling daily timesteps
        time_msource      — same
        time_vsink        — same
        ntracers          — 2  (temperature, salinity)
        one               — 1
    Variables:
        source_elem       (nsources)
        vsource           (time_vsource, nsources)   m³/s
        msource           (time_msource, ntracers, nsources)
                          T=-9999 (ambient), S=0 (freshwater)
        sink_elem         (nsinks)                   dummy [1]
        vsink             (time_vsink, nsinks)        zeros
        time_step_vsource (one)                       86400.0
        time_step_msource (one)                       86400.0
        time_step_vsink   (one)                       86400.0

Resume-safe: groups with an existing non-empty source.nc are skipped.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from workflow.core.config import (
    model_dir,
    list_groups,
    ProgressTracker,
    stack_ceiling,
    group_date_range,
    get_group_ndays,
)
from workflow.core.mesh_parser import read_element_centroids


# =============================================================================
# CSV readers
# =============================================================================

_LON_ALIASES = {"lon", "longitude", "x", "long"}
_LAT_ALIASES = {"lat", "latitude",  "y"}
_ID_ALIASES  = {"id", "fid", "river_id", "riverid"}


def _match_col(fieldnames: list, aliases: set, label: str,
               file_name: str) -> str:
    for col in fieldnames:
        if col.strip().lower() in aliases:
            return col
    print(f"ERROR: {file_name}: could not find a {label} column.")
    print(f"  Accepted names: {sorted(aliases)}")
    print(f"  Found columns : {fieldnames}")
    sys.exit(1)


def _read_csv(path: Path, label: str):
    import csv
    rows = []
    with open(path, newline="") as f:
        reader    = csv.DictReader(f)
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

def _glofas_dates_for_stack(cfg: dict, group_id: str) -> list:
    """Return a list of date objects covering the stack window.

    Starts on the first day of the group and runs for
    stack_ceiling(cfg, group_id) consecutive days.
    The tail spills into the following group / month / year as needed.
    """
    ceiling     = stack_ceiling(cfg, group_id)
    group_start, _ = group_date_range(cfg, group_id)
    return [group_start + timedelta(days=i) for i in range(ceiling)]


def _glofas_index_for_date(nc_path: Path, target: date):
    """Return the integer index into valid_time whose UTC date equals target,
    or None if not found."""
    import netCDF4 as nc4
    with nc4.Dataset(nc_path) as ds:
        vt = ds.variables["valid_time"][:]
    for i, t in enumerate(vt):
        dt = datetime.fromtimestamp(int(t), tz=timezone.utc)
        if (dt.year == target.year and dt.month == target.month
                and dt.day == target.day):
            return i
    return None


def _build_stack_time_map(cfg: dict, group_id: str, mdir: Path):
    """Build the ordered list of (glofas_nc_path, time_index) for the
    stack window.

    Returns (time_map, npad) where:
        time_map : list of (Path, int) or None (missing -> pad-repeated)
        npad     : number of trailing days that had to be pad-repeated
    """
    dates    = _glofas_dates_for_stack(cfg, group_id)
    time_map = []
    npad     = 0
    for d in dates:
        gnc = mdir / "raw" / "glofas" / str(d.year) / f"glofas_{d.year}.nc"
        if gnc.exists() and gnc.stat().st_size > 0:
            idx = _glofas_index_for_date(gnc, d)
            if idx is not None:
                time_map.append((gnc, idx))
                continue
        time_map.append(None)
        npad += 1
    return time_map, npad


def _extract_discharge(time_map: list, lon_target: float,
                       lat_target: float) -> np.ndarray:
    """Extract avg_dis time series for the cell nearest to (lon, lat)."""
    import netCDF4 as nc4

    _cell_cache = {}

    def _cell(nc_path):
        key = str(nc_path)
        if key not in _cell_cache:
            with nc4.Dataset(nc_path) as ds:
                lon = ds.variables["longitude"][:]
                lat = ds.variables["latitude"][:]
                ilon = int(np.argmin(np.abs(lon - lon_target)))
                ilat = int(np.argmin(np.abs(lat - lat_target)))
            _cell_cache[key] = (ilon, ilat)
        return _cell_cache[key]

    out  = np.zeros(len(time_map), dtype=np.float64)
    last = 0.0
    for k, entry in enumerate(time_map):
        if entry is None:
            out[k] = last
            continue
        nc_path, t_idx = entry
        ilon, ilat = _cell(nc_path)
        with nc4.Dataset(nc_path) as ds:
            val = np.ma.filled(
                ds.variables["avg_dis"][t_idx, ilat, ilon],
                fill_value=np.nan)
        val = float(val)
        if np.isnan(val) or val < 0.0:
            val = 0.0
        out[k] = val
        last   = val
    return out


# =============================================================================
# source.nc writer
# =============================================================================

def _write_source_nc(out_path: Path, source_elem: np.ndarray,
                     vsource: np.ndarray, dt_sec: float = 86400.0):
    import xarray as xr

    ntimesteps, nsources = vsource.shape

    msource = np.full((ntimesteps, 2, nsources), fill_value=-9999.0,
                      dtype=np.float64)
    msource[:, 1, :] = 0.0   # salinity = 0 (freshwater)

    ds = xr.Dataset({
        "source_elem":       (["nsources"],
                              source_elem.astype(np.int64)),
        "vsource":           (["time_vsource", "nsources"],
                              vsource),
        "msource":           (["time_msource", "ntracers", "nsources"],
                              msource),
        "sink_elem":         (["nsinks"],
                              np.ones(1, dtype=np.int64)),
        "vsink":             (["time_vsink", "nsinks"],
                              np.zeros((ntimesteps, 1))),
        "time_step_vsource": (["one"], [dt_sec]),
        "time_step_msource": (["one"], [dt_sec]),
        "time_step_vsink":   (["one"], [dt_sec]),
    })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    ds.to_netcdf(str(out_path), format="NETCDF4")


# =============================================================================
# Per-group processor
# =============================================================================

def _process_group(group_id: str, cfg: dict, rivers: list,
                   elem_ids: np.ndarray, centroids: np.ndarray,
                   mdir: Path) -> bool:
    """Build source.nc for one group. Returns True on success."""
    from scipy.spatial import KDTree

    gstart, gend = group_date_range(cfg, group_id)
    pid          = cfg["project_id"]
    out_nc       = mdir / f"I{pid}" / f"I{pid}_{group_id}" / "source.nc"
    ceil         = stack_ceiling(cfg, group_id)

    if out_nc.exists() and out_nc.stat().st_size > 0:
        print(f"  {group_id}: source.nc already exists, skipping.")
        return True

    # Check GloFAS file for the group start year
    year     = gstart.year
    glofas_nc = mdir / "raw" / "glofas" / str(year) / f"glofas_{year}.nc"
    if not (glofas_nc.exists() and glofas_nc.stat().st_size > 0):
        print(f"  ERROR {group_id}: {glofas_nc.name} not found — "
              f"run download_glofas first.")
        return False

    time_map, npad = _build_stack_time_map(cfg, group_id, mdir)
    n_real = sum(1 for e in time_map if e is not None)

    if n_real == 0:
        print(f"  ERROR {group_id}: no GloFAS timesteps found.")
        return False

    ndays_group = get_group_ndays(cfg, group_id)
    if n_real < ndays_group:
        print(f"  WARNING {group_id}: only {n_real} of {ndays_group} "
              f"in-group GloFAS days were found; missing days pad-repeated.")
    if npad > 0:
        print(f"  {group_id}: {ceil}-record stack — "
              f"{ceil - npad} from GloFAS, {npad} pad-repeated.")

    # Match source_schism points -> nearest SCHISM element
    tree          = KDTree(centroids)
    schism_coords = np.array([[r["schism_lon"] % 360, r["schism_lat"]]
                               for r in rivers])
    dists, nearest_idx = tree.query(schism_coords)

    print(f"  {group_id}: element matching distances (degrees):")
    for r, d, idx in zip(rivers, dists, nearest_idx):
        flag = "  *** LARGE ***" if d > 0.5 else ""
        print(f"    id={r['id']:>4d}  dist={d:.4f}°  "
              f"elem={elem_ids[idx]}{flag}")

    for r, idx in zip(rivers, nearest_idx):
        r["schism_elem"] = int(elem_ids[idx])

    # Aggregate per unique SCHISM element
    unique_elems = np.unique([r["schism_elem"] for r in rivers])
    nsources     = len(unique_elems)
    vsource      = np.zeros((ceil, nsources), dtype=np.float64)
    elem_to_idx  = {e: i for i, e in enumerate(unique_elems)}

    for r in rivers:
        s_idx = elem_to_idx[r["schism_elem"]]
        ts    = _extract_discharge(time_map,
                                   r["glofas_lon"] % 360,
                                   r["glofas_lat"])
        vsource[:, s_idx] += ts

    _write_source_nc(out_nc, unique_elems, vsource)
    size_kb = out_nc.stat().st_size // 1024
    print(f"  {group_id}: wrote source.nc — "
          f"{nsources} source elements, {ceil} timesteps, {size_kb} KB")
    return True


# =============================================================================
# Main entry point
# =============================================================================

def run_gen_source(cfg: dict):
    import pandas as pd

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    fix  = mdir / "fix"

    # --- Validate required files ---
    csv_glofas = fix / "source_glofas.csv"
    csv_schism = fix / "source_schism.csv"
    hgrid      = fix / "hgrid.gr3"

    for p in [csv_glofas, csv_schism, hgrid]:
        if not p.exists():
            print(f"ERROR: required file not found: {p}")
            print("  Copy source_glofas.csv, source_schism.csv and "
                  "hgrid.gr3 into fix/ before running gen_source.")
            sys.exit(1)

    # --- Load and merge the two CSVs on 'id' ---
    print("  Loading source CSVs...")
    gf_rows = _read_csv(csv_glofas, "source_glofas.csv")
    sc_rows = _read_csv(csv_schism,  "source_schism.csv")

    gf_df = pd.DataFrame(gf_rows).rename(
        columns={"lon": "glofas_lon", "lat": "glofas_lat"})
    sc_df = pd.DataFrame(sc_rows).rename(
        columns={"lon": "schism_lon", "lat": "schism_lat"})

    merged = gf_df.merge(sc_df, on="id", how="inner")

    if merged.empty:
        print("ERROR: no matching ids between source_glofas.csv and "
              "source_schism.csv — check that id values are identical.")
        sys.exit(1)

    n_gf = len(gf_df); n_sc = len(sc_df); n_ok = len(merged)
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

    # --- Parse mesh centroids once ---
    print(f"  Parsing element centroids from {hgrid.name} ...")
    elem_ids, centroids = read_element_centroids(hgrid)
    print(f"  Mesh: {len(elem_ids):,} elements loaded.")

    # --- Loop over groups ---
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")

    prog   = ProgressTracker(total=len(groups), label="gen_source")
    failed = []

    print(f"\n{'='*60}")
    print(f"  gen_source: {groups[0]} -> {groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"  Rivers: {n_ok}  |  "
          f"Output: I{pid}/I{pid}_{{group_id}}/source.nc")
    print(f"{'='*60}\n")

    for group_id in groups:
        ok = _process_group(group_id, cfg, rivers,
                            elem_ids, centroids, mdir)
        if not ok:
            failed.append(group_id)
        prog.update(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_source complete. No failures.")
    else:
        print(f"  gen_source complete with {len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
        print("  Re-run to retry (existing files are skipped).")
    print(f"{'='*60}\n")
