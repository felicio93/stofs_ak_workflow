"""
models/schism/postprocess/collocate_argo.py
===========================================
Phase 5 step "collocate_argo" (interactive; runs in the swf_plot env).

Collocates SCHISM 3-D temperature and salinity profiles against downloaded
Argo float profiles using OCSTrack's object-oriented collocation engine
(``ocstrack.Collocation.Collocate`` with ``var_type='3D_Profile'``).

Inputs
------
  * Argo profiles   : M{ID}/obs/argo/{region}/processed/  (from download_argo)
  * SCHISM outputs  : R{ID}/R{ID}_{YYYYMM}/outputs/{temperature,salinity,
                      zCoordinates}_*.nc  (3-D New I/O output, one file per
                      variable per stack). hgrid.gr3 is symlinked into each
                      run directory by setup_run.

Because the workflow stores SCHISM output per-month (one R{ID}_{YYYYMM}/
directory each), and OCSTrack's ``SCHISM`` model class points at a single
run directory, this step loops over the run months, collocates each month
independently, then concatenates the per-month collocated NetCDFs into one
combined file per variable.

Outputs
-------
    M{ID}/P{ID}/P{ID}_collocate_argo/
        collocated_temperature_{YYYYMM}.nc     (per-month, temperature)
        collocated_salinity_{YYYYMM}.nc        (per-month, salinity)
        collocated_temperature.nc              (concatenated over all months)
        collocated_salinity.nc                 (concatenated over all months)
        collocate_argo.done                    (sentinel)

Prerequisites for the 3-D profile output to exist, param.nml must enable the
temperature, salinity and zCoordinates New I/O outputs (iof_hydro). If a
month's run directory has no temperature_*/zCoordinates_* stacks the month is
skipped with a warning.

Longitude convention
--------------------
OCSTrack collocates in geocentric (WGS-84 XYZ) space, so the Argo longitudes
(-180..180 from the GDAC) and the SCHISM mesh longitudes MUST share a frame.
The workflow mesh uses domain.yaml ``lon_reference`` ("360" for the Bering
Sea). Argo longitudes are converted to that frame with OCSTrack's
``convert_longitude`` before collocation.

Config (postprocess.yaml)
-------------------------
  argo_region              GDAC region (default pacific_ocean)
  collocate_argo_vars       list subset of [temperature, salinity]
                            (default both)
  collocate_argo_n_nearest  number of nearest mesh nodes (default 3)
  collocate_argo_temporal_interp  linear time interpolation (default true)
  collocate_argo_start / _end     narrow the collocation window
                                  (default: run start_date/end_date)
"""

import argparse
import sys
from pathlib import Path

from workflow.core.config import load_config, list_months, model_dir


# Model variable -> (New I/O file prefix, Argo comparison note). SCHISM New I/O
# writes one file per variable named <var>_<stack>.nc, plus zCoordinates_<stack>.nc.
VAR_PLAN = {
    "temperature": {"startswith": "temperature_"},
    "salinity":    {"startswith": "salinity_"},
}

ZCOR_VAR        = "zCoordinates"
ZCOR_STARTSWITH = "zCoordinates_"


def _out_dir(cfg) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_collocate_argo"


def _argo_processed_dir(cfg) -> Path:
    region = str(cfg.get("argo_region", "pacific_ocean"))
    return model_dir(cfg) / "obs" / "argo" / region / "processed"


def _window(cfg):
    start = cfg.get("collocate_argo_start") or cfg["start_date"]
    end   = cfg.get("collocate_argo_end")   or cfg["end_date"]
    return str(start), str(end)


def _month_bounds(ym: str):
    """Return (first_day, last_day_exclusive_next_month_first) ISO strings for
    the calendar month ym (YYYYMM), used to clip the collocation window to a
    single month's run directory."""
    from datetime import date
    from dateutil.relativedelta import relativedelta
    y, m = int(ym[:4]), int(ym[4:])
    first = date(y, m, 1)
    nxt   = first + relativedelta(months=1)
    return first.isoformat(), nxt.isoformat()


def _clip(a_start, a_end, b_start, b_end):
    """Intersect two [start, end] ISO date windows. Returns (start, end) or
    None if they do not overlap."""
    s = max(a_start, b_start)
    e = min(a_end, b_end)
    return (s, e) if s < e else None


def _collocate_one_month(cfg, ym, var, win_start, win_end, out_dir):
    """Run OCSTrack collocation for a single variable in a single run month.

    Returns the path to the written per-month NetCDF, or None if skipped.
    """
    import numpy as np
    from ocstrack.Model.model import SCHISM
    from ocstrack.Observation.argofloat import ArgoData
    from ocstrack.Collocation.collocate import Collocate
    from ocstrack.utils import convert_longitude

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    rundir = mdir / f"R{pid}" / f"R{pid}_{ym}"
    outputs = rundir / "outputs"

    if not outputs.is_dir():
        print(f"  [{ym} {var}] no run outputs/ dir, skipping.")
        return None

    # Need the 3-D variable stacks AND matching zCoordinates stacks.
    startswith = VAR_PLAN[var]["startswith"]
    if not list(outputs.glob(f"{startswith}*.nc")):
        print(f"  [{ym} {var}] no {startswith}*.nc stacks (is iof output on?), skipping.")
        return None
    if not list(outputs.glob(f"{ZCOR_STARTSWITH}*.nc")):
        print(f"  [{ym} {var}] no {ZCOR_STARTSWITH}*.nc stacks, skipping.")
        return None
    if not (rundir / "hgrid.gr3").exists():
        print(f"  [{ym} {var}] no hgrid.gr3 in run dir, skipping.")
        return None

    # Clip the requested window to this month.
    m_start, m_end = _month_bounds(ym)
    clip = _clip(win_start, win_end, m_start, m_end)
    if clip is None:
        print(f"  [{ym} {var}] month outside collocation window, skipping.")
        return None
    c_start, c_end = clip

    # --- Load Argo profiles (all processed files) ---
    argo_dir = _argo_processed_dir(cfg)
    try:
        argo = ArgoData(str(argo_dir))
    except ValueError as exc:
        print(f"  [{ym} {var}] Argo load failed: {exc}")
        return None

    # Restrict Argo to this month's window (avoids reprocessing all profiles
    # against every month and keeps the per-month files disjoint in time).
    argo.filter_by_time(c_start, c_end)
    if argo.ds.sizes.get("JULD", 0) == 0:
        print(f"  [{ym} {var}] no Argo profiles in {c_start}..{c_end}, skipping.")
        return None

    # Match Argo longitudes to the mesh frame. domain lon_reference "360" means
    # the mesh is 0..360 (Greenwich origin); Argo is -180..180 -> mode 1.
    lon_ref = str(cfg.get("lon_reference", "360"))
    if lon_ref == "360":
        argo.lon = convert_longitude(argo.lon, mode=1)   # -180..180 -> 0..360

    # --- Build the OCSTrack SCHISM model for this run month ---
    model_dict = {
        "var":             var,
        "startswith":      startswith,
        "var_type":        "3D_Profile",
        "zcor_var":        ZCOR_VAR,
        "zcor_startswith": ZCOR_STARTSWITH,
    }
    model = SCHISM(
        rundir=str(rundir),
        model_dict=model_dict,
        start_date=np.datetime64(c_start),
        end_date=np.datetime64(c_end),
    )
    if not model.files:
        print(f"  [{ym} {var}] no model files overlap {c_start}..{c_end}, skipping.")
        return None

    n_nearest = int(cfg.get("collocate_argo_n_nearest", 3))
    temporal  = bool(cfg.get("collocate_argo_temporal_interp", True))

    out_nc = out_dir / f"collocated_{var}_{ym}.nc"
    print(f"  [{ym} {var}] collocating {argo.ds.sizes['JULD']} profile(s) "
          f"against {len(model.files)} model file(s) -> {out_nc.name}")

    coll = Collocate(
        model_run=model,
        observation=argo,
        n_nearest=n_nearest,
        temporal_interp=temporal,
    )
    ds = coll.run(output_path=str(out_nc))
    if ds is None or (hasattr(ds, "sizes") and ds.sizes.get("time", 0) == 0):
        print(f"  [{ym} {var}] collocation produced no matches.")
        # Remove an empty file if one was written.
        if out_nc.exists() and out_nc.stat().st_size == 0:
            out_nc.unlink(missing_ok=True)
        return None
    return out_nc


def _concat_months(var, per_month_files, out_dir):
    """Concatenate per-month collocated NetCDFs (along the 'time' dim) into one
    combined file per variable. Returns the combined path or None."""
    import xarray as xr

    if not per_month_files:
        return None
    combined = out_dir / f"collocated_{var}.nc"
    dsets = []
    for f in per_month_files:
        try:
            dsets.append(xr.open_dataset(str(f)))
        except (OSError, ValueError) as exc:
            print(f"  [concat {var}] could not open {f.name}: {exc}")
    if not dsets:
        return None
    try:
        merged = xr.concat(dsets, dim="time")
        merged = merged.sortby("time")
    except (ValueError, KeyError) as exc:
        print(f"  [concat {var}] concat failed ({exc}); "
              f"per-month files are still available.")
        for d in dsets:
            d.close()
        return None
    merged.to_netcdf(str(combined))
    for d in dsets:
        d.close()
    print(f"  [concat {var}] {len(per_month_files)} month(s) -> {combined.name}")
    return combined


def _write_clean(combined_path: Path, dist_threshold_m: float) -> Path:
    """Filter the concatenated collocated NetCDF to keep only profiles whose
    nearest mesh node is within ``dist_threshold_m`` metres, and write a
    companion ``collocated_{var}_clean.nc`` file.

    The ``dist_deltas`` variable has shape (time, nearest_nodes) and stores
    the distance (metres) from each Argo profile to each of the k-nearest
    mesh nodes.  We keep a profile when its **minimum** across the
    ``nearest_nodes`` dimension is below the threshold.

    Parameters
    ----------
    combined_path : Path
        Path to the full concatenated file (``collocated_{var}.nc``).
    dist_threshold_m : float
        Maximum allowed distance in metres (default: 5 000 m = 5 km).

    Returns
    -------
    Path
        Path to the written clean file, or None if no profiles passed.
    """
    import xarray as xr

    clean_path = combined_path.parent / (
        combined_path.stem + "_clean" + combined_path.suffix
    )

    ds = xr.open_dataset(str(combined_path), engine="netcdf4")
    nearest_dist = ds["dist_deltas"].min(dim="nearest_nodes")
    mask = (nearest_dist.values < dist_threshold_m)

    n_total = int(ds.sizes["time"])
    n_keep  = int(mask.sum())
    n_drop  = n_total - n_keep

    if n_keep == 0:
        print(f"  [clean {combined_path.name}] WARNING: distance threshold "
              f"{dist_threshold_m/1000:.1f} km removes ALL {n_total} profiles. "
              f"Clean file not written.")
        ds.close()
        return None

    ds_clean = ds.isel(time=mask)
    ds_clean.attrs["distance_filter_m"] = dist_threshold_m
    ds_clean.attrs["profiles_total"]    = n_total
    ds_clean.attrs["profiles_kept"]     = n_keep
    ds_clean.attrs["profiles_dropped"]  = n_drop
    ds_clean.to_netcdf(str(clean_path))
    ds.close()

    print(f"  [clean {combined_path.name}] kept {n_keep}/{n_total} profiles "
          f"(dropped {n_drop} > {dist_threshold_m/1000:.1f} km) "
          f"-> {clean_path.name}")
    return clean_path



def run_collocate_argo(cfg: dict, config_dir=None):
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    argo_dir = _argo_processed_dir(cfg)
    if not argo_dir.is_dir() or not list(argo_dir.glob("*.nc")):
        print(f"ERROR: no processed Argo files in {argo_dir}")
        print("  Run download_argo first:  stofs-ak --run --phase postprocess "
              "--only download_argo --config <cfg>")
        return

    variables = cfg.get("collocate_argo_vars") or ["temperature", "salinity"]
    variables = [v for v in variables if v in VAR_PLAN]
    if not variables:
        print("ERROR: collocate_argo_vars has no known variables "
              "(expected temperature and/or salinity).")
        return

    win_start, win_end = _window(cfg)
    out_dir = _out_dir(cfg)
    out_dir.mkdir(parents=True, exist_ok=True)

    months = list_months(cfg)

    print(f"\n{'='*60}")
    print(f"  Argo collocation (SCHISM 3-D profiles vs Argo floats)")
    print(f"  Variables: {variables}")
    print(f"  Window: {win_start} -> {win_end}   {len(months)} run month(s)")
    print(f"  Argo:   {argo_dir}")
    print(f"  Output: {out_dir}")
    print(f"{'='*60}\n")

    any_output = False
    for var in variables:
        per_month = []
        for ym in months:
            try:
                nc = _collocate_one_month(cfg, ym, var, win_start, win_end,
                                          out_dir)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{ym} {var}] ERROR: {type(exc).__name__}: {exc}")
                nc = None
            if nc is not None:
                per_month.append(nc)
        combined = _concat_months(var, per_month, out_dir)
        if combined is not None:
            any_output = True
            # Write a distance-filtered clean file alongside the full one.
            dist_thresh = float(cfg.get("collocate_argo_dist_threshold_km", 5)) * 1000.0
            _write_clean(combined, dist_threshold_m=dist_thresh)

    if any_output:
        (out_dir / "collocate_argo.done").touch()
        print(f"\n{'='*60}")
        print(f"  Argo collocation complete. NetCDFs in {out_dir}")
        print(f"{'='*60}\n")
    else:
        print(f"\n{'='*60}")
        print("  Argo collocation produced no matches. Common causes:")
        print("    * sparse Argo coverage in the domain/period,")
        print("    * SCHISM 3-D T/S/zCoordinates output not enabled in param.nml,")
        print("    * collocation window does not overlap the run months.")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Collocate SCHISM T/S with Argo floats")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    run_collocate_argo(cfg, args.config)
