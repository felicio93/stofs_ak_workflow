"""
download_hycom.py
=================
Downloads daily HYCOM data (SSH, TS, UV) via OPeNDAP/THREDDS into the
project's raw/hycom/{ssh,ts,uv}/ directories.

Handles the FULL HYCOM server layout (2010 -> present):

  * GOFS 3.1 Reanalysis  (GLBv0.08/expt_53.X)     1994 -> 2015-12-31
      - single aggregation, combined variables, -180..180 grid (legacy)
  * GOFS 3.1 Analysis    (GLBv0.08/expt_57.2 ... 93.0)  2016 -> 2018-12-03
      - combined ts3z / uv3z
  * GOFS 3.1 Analysis    (GLBy0.08/expt_93.0)     2018-12-04 -> 2024-09-04
      - combined ts3z / uv3z, per-variable subpaths (/ssh /ts3z /uv3z)
      - ***ENDS 2024-09-04*** (requesting later dates returns STALE data!)
  * ESPC-D-V02           (ESPC-D-V02/{ssh,t3z,s3z,u3z,v3z}/YYYY)  2024-08-10 -> present
      - temperature/salinity in SEPARATE files (t3z, s3z)
      - u/v in SEPARATE files (u3z, v3z); year subpath

For TS and UV, both variables are fetched (from combined OR separate sources)
and merged into a single ts_YYYYMMDD.nc / uv_YYYYMMDD.nc so that the aggregate
step and the SCHISM Fortran tools see an identical format regardless of epoch.

Includes a per-month "stale data" sanity check: if the first and last day of a
month have identical field means, the data is almost certainly not being
downloaded correctly (e.g. an expired aggregation echoing its last timestep).

- Resume-safe (skips valid files, atomic finalize).
- Applies longitude reference frame handling based on domain.yaml.
- Fixes the time axis corrupted by ncpdq unpacking.

Usage (called by orchestrator.py, not directly):
    python download_hycom.py --config /path/to/M01/config
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

import yaml

from workflow.config import ProgressTracker


REQUIRED_TOOLS = ["ncks", "ncpdq", "ncap2", "ncrename", "ncatted", "ncwa"]
DTN_HOSTNAME_HINT = "dtn"

TDS = "https://tds.hycom.org/thredds/dodsC"


# =============================================================================
# HYCOM epoch table
# =============================================================================
# Each epoch entry is a dict describing where/how to fetch data for a date
# range. `last` is the inclusive last date (YYYYMMDD) the epoch covers; the
# final epoch uses None (open-ended). The first matching epoch (by date order)
# is used.
#
# `kind` selects how variables are located:
#   "combined": one base URL; TS from <base>/ts3z (vars water_temp,salinity),
#               UV from <base>/uv3z (vars water_u,water_v), SSH from <base>/ssh
#               (or the bare base for very old single-aggregation experiments,
#               controlled by `subpaths`).
#   "espc":     ESPC-D-V02 layout with SEPARATE files per variable and a /YYYY
#               year subpath: t3z (water_temp), s3z (salinity),
#               u3z (water_u), v3z (water_v), ssh (surf_el).
#
# `subpaths`: for "combined", whether variable subpaths (/ssh /ts3z /uv3z) are
#             appended to the base. Old GLBv single aggregations have none.
# =============================================================================
HYCOM_EPOCHS = [
    # GOFS 3.1 Reanalysis 53.X (single aggregation, no variable subpaths).
    # Covers up to 2015-12-31. Legacy -180..180 grid.
    {"last": "20151231", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_53.X/data/3hrly", "subpaths": False},

    # GOFS 3.1 Analysis on GLBv0.08 (combined vars, no per-var subpaths).
    {"last": "20170131", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_57.2", "subpaths": False},
    {"last": "20170531", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_92.8", "subpaths": False},
    {"last": "20170930", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_57.7", "subpaths": False},
    {"last": "20171231", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_92.9", "subpaths": False},

    # GLBv0.08/expt_93.0 (2018-01-01 .. 2018-12-03): per-variable subpaths.
    {"last": "20181203", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_93.0", "subpaths": True},

    # GLBy0.08/expt_93.0 (2018-12-04 .. 2024-09-04): per-variable subpaths.
    # NOTE: this aggregation ENDS 2024-09-04.
    {"last": "20240904", "kind": "combined",
     "base": f"{TDS}/GLBy0.08/expt_93.0", "subpaths": True},

    # ESPC-D-V02 (2024-08-10 .. present): separate files, /YYYY subpath.
    {"last": None, "kind": "espc",
     "base": f"{TDS}/ESPC-D-V02"},
]


def select_epoch(date_flat: str) -> dict:
    """Return the epoch dict whose range contains date_flat (YYYYMMDD)."""
    for epoch in HYCOM_EPOCHS:
        if epoch["last"] is None or date_flat <= epoch["last"]:
            return epoch
    raise ValueError(f"No HYCOM epoch found for date {date_flat}")


def source_urls(epoch: dict, product: str, year: int):
    """
    Return a list of (url, variables) tuples to fetch for a given product
    ("ssh"|"ts"|"uv") under a given epoch. Multiple tuples mean the variables
    live in separate source files and must be merged.
    """
    if epoch["kind"] == "espc":
        base = epoch["base"]
        if product == "ssh":
            return [(f"{base}/ssh/{year}", "surf_el")]
        if product == "ts":
            return [(f"{base}/t3z/{year}", "water_temp"),
                    (f"{base}/s3z/{year}", "salinity")]
        if product == "uv":
            return [(f"{base}/u3z/{year}", "water_u"),
                    (f"{base}/v3z/{year}", "water_v")]
    else:  # combined
        base = epoch["base"]
        sub = epoch["subpaths"]
        if product == "ssh":
            url = f"{base}/ssh" if sub else base
            return [(url, "surf_el")]
        if product == "ts":
            url = f"{base}/ts3z" if sub else base
            return [(url, "water_temp,salinity")]
        if product == "uv":
            url = f"{base}/uv3z" if sub else base
            return [(url, "water_u,water_v")]
    raise ValueError(f"Unknown product {product}")


# =============================================================================
# Config / environment / tool checks
# =============================================================================

def load_config(config_dir: Path) -> dict:
    cfg = {}
    for fname in ("project.yaml", "domain.yaml", "steps.yaml", "envs.yaml"):
        fpath = config_dir / fname
        if not fpath.exists():
            print(f"ERROR: Config file not found: {fpath}")
            sys.exit(1)
        with open(fpath) as f:
            data = yaml.safe_load(f)
            if data:
                cfg.update(data)
    return cfg


def run(cmd, check=True):
    print("  CMD:", " ".join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  STDERR: {result.stderr.strip()}")
        raise RuntimeError(f"Command failed with exit code {result.returncode}")
    return result


def is_complete_file(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def finalize_output(tmp_file: Path, final_file: Path):
    if not is_complete_file(tmp_file):
        raise RuntimeError(f"Temporary output was not created or is empty: {tmp_file}")
    tmp_file.replace(final_file)


def cleanup_files(*paths: Path):
    for path in paths:
        path.unlink(missing_ok=True)


def check_required_tools():
    missing = [t for t in REQUIRED_TOOLS if shutil.which(t) is None]
    if missing:
        print("ERROR: required command-line tools not found on PATH:")
        for t in missing:
            print(f"    - {t}")
        print("These are provided by NCO. Activate swf_main or 'module load nco'.")
        sys.exit(1)


def check_active_env(cfg: dict):
    expected = cfg.get("conda_envs", {}).get("download_hycom")
    if not expected:
        return
    active = os.environ.get("CONDA_DEFAULT_ENV")
    if active == expected:
        print(f"  Env check: active conda environment '{active}' matches config. OK.")
        return
    print(f"  {'='*56}")
    print(f"  WARNING: wrong conda environment for download_hycom.")
    print(f"     active:   '{active or '(none)'}'")
    print(f"     expected: '{expected}'")
    print(f"  Activate the correct environment and re-run:")
    print(f"     conda activate {expected}")
    print(f"  {'='*56}")


def check_dtn():
    hostname = socket.gethostname()
    if DTN_HOSTNAME_HINT in hostname.lower():
        print(f"  Host check: running on '{hostname}' (looks like a DTN). OK.")
        return
    if os.environ.get("ALLOW_NON_DTN") == "1":
        print(f"  Host check: '{hostname}' is not a DTN, but ALLOW_NON_DTN=1 set. Proceeding.")
        return
    print(f"ERROR: current host '{hostname}' does not look like a Data Transfer Node.")
    print("The HYCOM download needs external internet access (Hercules DTN only):")
    print("    ssh hercules-dtn.hpc.msstate.edu")
    print("    conda activate swf_main")
    print("    python orchestrator.py --run --config <config_dir>")
    print("Bypass on other systems with:  export ALLOW_NON_DTN=1")
    sys.exit(1)


def hours_since_2000(date_str: str) -> int:
    ref = date(2000, 1, 1)
    d = date.fromisoformat(date_str)
    return int((d - ref).total_seconds() // 3600)


# =============================================================================
# Low-level OPeNDAP fetch (single variable set from one source URL)
# =============================================================================

def _fetch_subset(date_str, url, variables, lon_min, lon_max,
                  lat_min, lat_max, lon_ref, out_file, tmp_dir, prefix) -> bool:
    """
    Fetch a lon/lat/time subset of `variables` from `url` into out_file,
    handling the longitude reference frame. Returns True/False.
    """
    if lon_ref not in ("360", "180"):
        print(f"  ERROR: unsupported lon_reference '{lon_ref}' (use '360' or '180')")
        return False

    # Attempt A: direct subsetting with target lon bounds
    cmd_a = ["ncks", "-O",
             "-d", f"lon,{lon_min},{lon_max}",
             "-d", f"lat,{lat_min},{lat_max}",
             "-d", f"time,{date_str}",
             "-v", variables, url, str(out_file)]
    result = subprocess.run(cmd_a, capture_output=True, text=True)
    if result.returncode == 0:
        return True

    # Attempt B: fetch without lon subset, shift to target convention, subset
    tmp_global = tmp_dir / f"{prefix}_global_{date_str.replace('-','')}.nc"
    tmp_shift  = tmp_dir / f"{prefix}_shift_{date_str.replace('-','')}.nc"
    cmd_b = ["ncks", "-O",
             "-d", f"lat,{lat_min},{lat_max}",
             "-d", f"time,{date_str}",
             "-v", variables, url, str(tmp_global)]
    result = subprocess.run(cmd_b, capture_output=True, text=True)
    if result.returncode != 0:
        tmp_global.unlink(missing_ok=True)
        return False

    shift_expr = ("where(lon<0) lon=lon+360" if lon_ref == "360"
                  else "where(lon>180) lon=lon-360")
    run(["ncap2", "-O", "-s", shift_expr, str(tmp_global), str(tmp_shift)])
    run(["ncks", "-O", "--msa", "-d", f"lon,{lon_min},{lon_max}",
         str(tmp_shift), str(out_file)])
    tmp_global.unlink(missing_ok=True)
    tmp_shift.unlink(missing_ok=True)
    return True


def _fetch_product_raw(product, date_str, epoch, lon_min, lon_max,
                       lat_min, lat_max, lon_ref, tmp_dir) -> Path:
    """
    Fetch all source pieces for a product and merge them into a single raw
    NetCDF (still packed shorts). Returns the merged raw file path, or raises.
    """
    date_flat = date_str.replace("-", "")
    year = int(date_flat[:4])
    pieces = source_urls(epoch, product, year)

    piece_files = []
    for idx, (url, variables) in enumerate(pieces):
        pf = tmp_dir / f"{product}_src{idx}_{date_flat}.nc"
        cleanup_files(pf)
        ok = _fetch_subset(date_str, url, variables, lon_min, lon_max,
                           lat_min, lat_max, lon_ref, pf, tmp_dir,
                           prefix=f"{product}{idx}")
        if not ok:
            # For ESPC near a year boundary, retry with the adjacent year.
            if epoch["kind"] == "espc":
                alt_year = year - 1 if int(date_flat[4:6]) == 1 else year + 1
                alt_pieces = source_urls(epoch, product, alt_year)
                url_alt, vars_alt = alt_pieces[idx]
                print(f"  {product.upper()}: retrying with year {alt_year} ...")
                ok = _fetch_subset(date_str, url_alt, vars_alt, lon_min, lon_max,
                                   lat_min, lat_max, lon_ref, pf, tmp_dir,
                                   prefix=f"{product}{idx}")
        if not ok:
            for f in piece_files:
                cleanup_files(f)
            raise RuntimeError(f"could not fetch {variables} from {url}")
        piece_files.append(pf)

    merged = tmp_dir / f"{product}_raw_{date_flat}.nc"
    cleanup_files(merged)
    if len(piece_files) == 1:
        piece_files[0].replace(merged)
    else:
        # Merge separate variable files (e.g. ESPC t3z + s3z) into one.
        shutil.copyfile(piece_files[0], merged)
        for pf in piece_files[1:]:
            run(["ncks", "-A", str(pf), str(merged)])
        for pf in piece_files:
            cleanup_files(pf)
    return merged


# =============================================================================
# Per-product processing (unpack, fix time, rename, cast) -> final file
# =============================================================================

def _process_and_finalize(product, raw_nc, date_str, out_file, tmp_dir):
    date_flat = date_str.replace("-", "")
    test1 = tmp_dir / f"{product}_t1_{date_flat}.nc"
    test2 = tmp_dir / f"{product}_t2_{date_flat}.nc"
    final_tmp = tmp_dir / f"{out_file.name}.tmp"
    cleanup_files(test1, test2, final_tmp)

    hrs = hours_since_2000(date_str)
    run(["ncpdq", "-O", "-U", str(raw_nc), str(test1)])
    run(["ncap2", "-O", "-s", f"time(:)={hrs}.0", str(test1), str(test1)])

    if product == "ssh":
        cast = "lat=float(lat); lon=float(lon); surf_el=float(surf_el);"
    elif product == "ts":
        cast = ("depth=float(depth); lat=float(lat); lon=float(lon); "
                "water_temp=float(water_temp); salinity=float(salinity);")
    else:  # uv
        cast = ("depth=float(depth); lat=float(lat); lon=float(lon); "
                "water_u=float(water_u); water_v=float(water_v);")
    run(["ncap2", "-O", "-s", cast, str(test1), str(test2)])

    run(["ncrename", "-O", "-d", "lon,xlon", "-d", "lat,ylat",
         "-v", "lon,xlon", "-v", "lat,ylat", str(test2)])
    if product == "ts":
        run(["ncatted", "-O", "-a", "units,water_temp,m,c,degC", str(test2)])

    run(["ncks", "-O", "--mk_rec_dmn", "time", str(test2), str(final_tmp)])
    finalize_output(final_tmp, out_file)
    cleanup_files(raw_nc, test1, test2, final_tmp)


def download_product(product, date_str, epoch, out_file,
                     lon_min, lon_max, lat_min, lat_max, lon_ref, tmp_dir) -> bool:
    raw = _fetch_product_raw(product, date_str, epoch, lon_min, lon_max,
                             lat_min, lat_max, lon_ref, tmp_dir)
    _process_and_finalize(product, raw, date_str, out_file, tmp_dir)
    return True


# =============================================================================
# Stale-data sanity check
# =============================================================================

def field_signature(nc_file: Path, varname: str, tmp_dir: Path):
    """
    Return the mean of `varname` over the file as a float, using ncap2/ncwa.
    Used to compare first vs last day of a month to detect stale/echoed data.
    """
    tmp = tmp_dir / f"sig_{nc_file.stem}.txt"
    # ncap2 to compute the average into a scalar, then ncks --  simpler: use
    # ncwa to average over all dims, then dump the value.
    avg = tmp_dir / f"avg_{nc_file.stem}.nc"
    cleanup_files(avg)
    try:
        run(["ncwa", "-O", "-y", "avg", "-v", varname, str(nc_file), str(avg)])
        out = run(["ncks", "-s", "%f", "-H", "-C", "-v", varname, str(avg)]).stdout
    finally:
        cleanup_files(avg, tmp)
    vals = [float(x) for x in out.split() if x.strip()]
    return vals[0] if vals else float("nan")


def stale_check_month(ym, ssh_dir, ts_dir, uv_dir, tmp_dir):
    """
    Compare the first and last downloaded day of a month. If the field means
    are identical (to within a tiny tolerance) the data is almost certainly
    stale (e.g. an expired aggregation echoing its final timestep).
    Returns a list of warning strings (empty if all good).
    """
    year, month = int(ym[:4]), int(ym[4:])
    ndays = monthrange(year, month)[1]
    first = f"{ym}01"
    last = f"{ym}{ndays:02d}"

    warnings = []
    checks = [("ssh", ssh_dir, "surf_el"),
              ("ts", ts_dir, "water_temp"),
              ("uv", uv_dir, "water_u")]
    for prod, vdir, var in checks:
        f0 = vdir / f"{prod}_{first}.nc"
        f1 = vdir / f"{prod}_{last}.nc"
        if not (f0.exists() and f1.exists()):
            continue
        try:
            s0 = field_signature(f0, var, tmp_dir)
            s1 = field_signature(f1, var, tmp_dir)
        except Exception as exc:
            warnings.append(f"[{ym} {prod}] stale-check could not run: {exc}")
            continue
        if s0 == s0 and s1 == s1 and abs(s0 - s1) < 1e-9:  # not NaN and equal
            warnings.append(
                f"[{ym} {prod}] STALE DATA SUSPECTED: {var} mean identical on "
                f"{first} and {last} ({s0:.6f}). The source aggregation may have "
                f"expired for this date range -- check the epoch mapping.")
    return warnings


# =============================================================================
# Main download loop
# =============================================================================

def run_download(cfg: dict):
    check_dtn()
    check_active_env(cfg)
    check_required_tools()

    pid = cfg["project_id"]
    project_dir = Path(cfg["project_dir"])
    start = date.fromisoformat(cfg["start_date"])
    end = date.fromisoformat(cfg["end_date"])
    lon_min = float(cfg["lon_min"]); lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"]); lat_max = float(cfg["lat_max"])
    lon_ref = str(cfg["lon_reference"])

    raw_dir = project_dir / f"M{pid}" / "raw" / "hycom"
    ssh_dir = raw_dir / "ssh"; ts_dir = raw_dir / "ts"; uv_dir = raw_dir / "uv"
    tmp_dir = raw_dir / "tmp"
    for d in [ssh_dir, ts_dir, uv_dir, tmp_dir]:
        d.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  HYCOM download: {start} -> {end}")
    print(f"  Domain: lon [{lon_min}, {lon_max}]  lat [{lat_min}, {lat_max}]")
    print(f"  Lon reference: {lon_ref}")
    print(f"  Raw output:    {raw_dir}")
    print(f"{'='*60}\n")

    products = [("ssh", ssh_dir), ("ts", ts_dir), ("uv", uv_dir)]

    total_days = (end - start).days + 1
    prog = ProgressTracker(total=total_days, label="HYCOM download")

    current = start
    failed_days = []
    months_seen = []

    while current <= end:
        date_str = current.isoformat()
        date_flat = current.strftime("%Y%m%d")
        ym = current.strftime("%Y%m")
        if ym not in months_seen:
            months_seen.append(ym)
        epoch = select_epoch(date_flat)

        print(f"\n--- {date_str}  (epoch base: {epoch['base']}) ---")

        for prod, vdir in products:
            out = vdir / f"{prod}_{date_flat}.nc"
            if is_complete_file(out):
                print(f"  {prod.upper()}: already exists, skipping.")
                continue
            if out.exists():
                print(f"  {prod.upper()}: incomplete file, re-downloading.")
                out.unlink()
            try:
                download_product(prod, date_str, epoch, out,
                                 lon_min, lon_max, lat_min, lat_max,
                                 lon_ref, tmp_dir)
                print(f"  {prod.upper()}: OK")
            except Exception as exc:
                print(f"  ERROR: {prod.upper()} failed for {date_str}: {exc}")
                failed_days.append((date_str, prod))

        prog.update(date_str)
        current += timedelta(days=1)

    # --- Stale-data sanity check per fully-downloaded month ---
    print(f"\n{'='*60}\n  Running stale-data sanity checks...\n{'='*60}")
    stale_warnings = []
    for ym in months_seen:
        stale_warnings += stale_check_month(ym, ssh_dir, ts_dir, uv_dir, tmp_dir)
    if stale_warnings:
        for w in stale_warnings:
            print("  WARNING: " + w)
    else:
        print("  Stale-data check passed (first vs last day differ).")

    # --- Summary ---
    print(f"\n{'='*60}")
    if not failed_days:
        print("  HYCOM download complete. No download failures.")
    else:
        print(f"  HYCOM download complete with {len(failed_days)} failure(s):")
        for d, var in failed_days:
            print(f"    {d}  {var}")
        print("  Re-run to retry (existing valid files are skipped).")
    if stale_warnings:
        print(f"  {len(stale_warnings)} STALE-DATA WARNING(S) above -- investigate!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download HYCOM data for SCHISM workflow")
    parser.add_argument("--config", required=True,
                        help="Path to the config/ directory")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    run_download(cfg)
