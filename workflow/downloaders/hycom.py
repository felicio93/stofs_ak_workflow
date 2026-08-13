"""
downloaders/hycom.py
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

Usage (called by the SchismDriver, not directly):
    stofs-ak --run --only download_hycom --config /path/to/M01/config
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

from workflow.core.config import ProgressTracker, DebugLog


REQUIRED_TOOLS = ["ncks", "ncpdq", "ncap2", "ncrename", "ncatted", "ncwa"]
DTN_HOSTNAME_HINT = "dtn"

TDS = "https://tds.hycom.org/thredds/dodsC"

# Module-level debug log handle (set at the start of run_download). Command
# traces and stderr go here to keep the screen clean.
_DEBUG = None


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

    # ESPC-D-V02 (2024-08-10 .. present).
    # Uses per-day ARCHIVE files — NOT the annual aggregation (t3z/YYYY).
    # The annual aggregation is a rolling ~70-day window; requesting dates
    # outside that window silently returns the last available timestep, giving
    # stale warm-season data for winter months.
    #
    # Correct URL pattern (one file per day per variable, tau=0 analysis):
    #   {TDS}/datasets/ESPC-D-V02/data/archive/{YYYY}/
    #     US058GCOM-OPSnce.espc-d-031-hycom_fcst_glby008_{YYYYMMDD}12_t0000_{var}.nc
    #
    # Each file contains exactly ONE timestep so no -d time subsetting is used.
    {"last": None, "kind": "espc_archive",
     "base": f"{TDS}/datasets/ESPC-D-V02/data/archive"},
]

ESPC_ARCHIVE_PREFIX = "US058GCOM-OPSnce.espc-d-031-hycom_fcst_glby008"

# Map product to ESPC archive variable suffix
ESPC_ARCHIVE_VAR = {
    "ssh": ("ssh",  "surf_el"),
    "ts":  [("t3z", "water_temp"), ("s3z", "salinity")],
    "uv":  [("u3z", "water_u"),    ("v3z", "water_v")],
}


def select_epoch(date_flat: str) -> dict:
    """Return the epoch dict whose range contains date_flat (YYYYMMDD)."""
    for epoch in HYCOM_EPOCHS:
        if epoch["last"] is None or date_flat <= epoch["last"]:
            return epoch
    raise ValueError(f"No HYCOM epoch found for date {date_flat}")


def source_urls(epoch: dict, product: str, year: int, date_flat: str = ""):
    """
    Return a list of (url, variables) tuples to fetch for a given product.
    For espc_archive, date_flat (YYYYMMDD) is required to build the filename.
    """
    if epoch["kind"] == "espc_archive":
        base = epoch["base"]
        prefix = ESPC_ARCHIVE_PREFIX
        if product == "ssh":
            var_suffix, variables = ESPC_ARCHIVE_VAR["ssh"]
            fname = f"{prefix}_{date_flat}12_t0000_{var_suffix}.nc"
            return [(f"{base}/{year}/{fname}", variables)]
        pieces = ESPC_ARCHIVE_VAR[product]
        result = []
        for var_suffix, variables in pieces:
            fname = f"{prefix}_{date_flat}12_t0000_{var_suffix}.nc"
            result.append((f"{base}/{year}/{fname}", variables))
        return result
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


def _dbg(line: str):
    """Write a line to the debug log if it is open (no-op otherwise)."""
    if _DEBUG is not None:
        _DEBUG.write(line)


def run(cmd, check=True):
    """Run a command; echo it to the debug log (not the screen)."""
    cmd = [str(c) for c in cmd]
    _dbg("CMD: " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stderr.strip():
        _dbg("STDERR: " + result.stderr.strip())
    if check and result.returncode != 0:
        # Surface failures on screen too, and point to the debug log.
        print(f"  COMMAND FAILED (rc={result.returncode}): {' '.join(cmd)}")
        print(f"    {result.stderr.strip().splitlines()[-1] if result.stderr.strip() else '(no stderr)'}")
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
    from workflow.core.environment import check_active_env as _c
    _c(cfg, "download_hycom")


def check_dtn():
    from workflow.core.environment import check_dtn as _c
    _c("HYCOM download")


def hours_since_2000(date_str: str) -> int:
    ref = date(2000, 1, 1)
    d = date.fromisoformat(date_str)
    return int((d - ref).total_seconds() // 3600)


# =============================================================================
# Low-level OPeNDAP fetch (single variable set from one source URL)
# =============================================================================

def _fetch_subset(date_str, url, variables, lon_min, lon_max,
                  lat_min, lat_max, lon_ref, out_file, tmp_dir, prefix,
                  no_time_subset=False) -> bool:
    """
    Fetch a lon/lat subset of `variables` from `url` into out_file.
    If no_time_subset=True (ESPC archive files which contain exactly one
    timestep), skip the -d time dimension argument.
    """
    if lon_ref not in ("360", "180"):
        print(f"  ERROR: unsupported lon_reference '{lon_ref}' (use '360' or '180')")
        return False

    time_args = [] if no_time_subset else ["-d", f"time,{date_str}"]

    # Attempt A: direct subsetting with target lon bounds
    cmd_a = ["ncks", "-O",
             "-d", f"lon,{lon_min},{lon_max}",
             "-d", f"lat,{lat_min},{lat_max}"]
    cmd_a += time_args
    cmd_a += ["-v", variables, url, str(out_file)]
    _dbg("CMD: " + " ".join(cmd_a))
    result = subprocess.run(cmd_a, capture_output=True, text=True)
    if result.stderr.strip():
        _dbg("STDERR: " + result.stderr.strip())
    if result.returncode == 0:
        _dbg(f"fetched [{variables}] from {url} (direct)")
        return True

    err_a = result.stderr.strip().splitlines()
    err_a = err_a[-1] if err_a else "(no stderr)"
    _dbg(f"Attempt A failed for [{variables}] from {url}: {err_a}")
    _dbg("trying opposite-longitude-convention fallback...")

    # Attempt B: fetch without lon subset, shift to target convention, subset
    tmp_global = tmp_dir / f"{prefix}_global_{date_str.replace('-','')}.nc"
    tmp_shift  = tmp_dir / f"{prefix}_shift_{date_str.replace('-','')}.nc"
    cmd_b = ["ncks", "-O", "-d", f"lat,{lat_min},{lat_max}"]
    cmd_b += time_args
    cmd_b += ["-v", variables, url, str(tmp_global)]
    _dbg("CMD: " + " ".join(cmd_b))
    result = subprocess.run(cmd_b, capture_output=True, text=True)
    if result.stderr.strip():
        _dbg("STDERR: " + result.stderr.strip())
    if result.returncode != 0:
        err_b = result.stderr.strip().splitlines()
        err_b = err_b[-1] if err_b else "(no stderr)"
        print(f"       both fetch attempts failed for [{variables}] from {url}")
        print(f"         {err_b}")
        tmp_global.unlink(missing_ok=True)
        return False

    shift_expr = ("where(lon<0) lon=lon+360" if lon_ref == "360"
                  else "where(lon>180) lon=lon-360")
    run(["ncap2", "-O", "-s", shift_expr, str(tmp_global), str(tmp_shift)])
    run(["ncks", "-O", "--msa", "-d", f"lon,{lon_min},{lon_max}",
         str(tmp_shift), str(out_file)])
    tmp_global.unlink(missing_ok=True)
    tmp_shift.unlink(missing_ok=True)
    _dbg(f"fetched [{variables}] from {url} (lon-shifted)")
    return True


def _fetch_product_raw(product, date_str, epoch, lon_min, lon_max,
                       lat_min, lat_max, lon_ref, tmp_dir) -> Path:
    """
    Fetch all source pieces for a product and merge into a single raw NetCDF.
    Returns the merged raw file path, or raises on failure.
    """
    date_flat = date_str.replace("-", "")
    year = int(date_flat[:4])
    is_archive = epoch["kind"] == "espc_archive"
    pieces = source_urls(epoch, product, year, date_flat)
    # Archive files contain exactly one timestep -- no time subsetting needed.
    no_time = is_archive

    piece_files = []
    for idx, (url, variables) in enumerate(pieces):
        pf = tmp_dir / f"{product}_src{idx}_{date_flat}.nc"
        cleanup_files(pf)
        ok = _fetch_subset(date_str, url, variables, lon_min, lon_max,
                           lat_min, lat_max, lon_ref, pf, tmp_dir,
                           prefix=f"{product}{idx}",
                           no_time_subset=no_time)
        if not ok and is_archive:
            # Near a year boundary the file may be in the adjacent year.
            alt_year = year - 1 if int(date_flat[4:6]) == 1 else year + 1
            url_alt, vars_alt = source_urls(epoch, product, alt_year, date_flat)[idx]
            _dbg(f"retrying {product}/{variables} with year {alt_year}")
            ok = _fetch_subset(date_str, url_alt, vars_alt, lon_min, lon_max,
                               lat_min, lat_max, lon_ref, pf, tmp_dir,
                               prefix=f"{product}{idx}",
                               no_time_subset=no_time)
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
        # Merge separate variable files into one (e.g. t3z + s3z, u3z + v3z).
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
    Return a scalar signature (mean) of `varname` in the file, used to compare
    the first vs last day of a month to detect stale/echoed data.

    ncwa with no -a averages over ALL dimensions -> a single scalar. We then
    dump it and average whatever values are returned (robust to any residual
    degenerate dims).
    """
    avg = tmp_dir / f"avg_{nc_file.stem}.nc"
    cleanup_files(avg)
    try:
        run(["ncwa", "-O", "-y", "avg", "-v", varname, str(nc_file), str(avg)])
        out = run(["ncks", "-s", "%f ", "-H", "-C", "-v", varname, str(avg)]).stdout
    finally:
        cleanup_files(avg)
    vals = [float(x) for x in out.split() if x.strip()]
    if not vals:
        return float("nan")
    return sum(vals) / len(vals)


def stale_check_month(ym, ssh_dir, ts_dir, uv_dir, tmp_dir):
    """
    Compare the first vs the last AVAILABLE day of a month. If the field means
    are identical (to within a tiny tolerance) the data is almost certainly
    stale (e.g. an expired aggregation echoing its final timestep).

    Works for partial months too: it uses the earliest and latest days that
    actually have files, and skips the check only if fewer than two days exist.
    Returns a list of warning strings (empty if all good).
    """
    year, month = int(ym[:4]), int(ym[4:])
    ndays = monthrange(year, month)[1]

    warnings = []
    checks = [("ssh", ssh_dir, "surf_el"),
              ("ts", ts_dir, "water_temp"),
              ("uv", uv_dir, "water_u")]
    for prod, vdir, var in checks:
        # Collect the days that actually have files this month.
        present = []
        for d in range(1, ndays + 1):
            flat = f"{ym}{d:02d}"
            f = vdir / f"{prod}_{flat}.nc"
            if f.exists() and f.stat().st_size > 0:
                present.append(f)
        if len(present) < 2:
            continue  # not enough days to compare
        f0, f1 = present[0], present[-1]
        try:
            s0 = field_signature(f0, var, tmp_dir)
            s1 = field_signature(f1, var, tmp_dir)
        except Exception as exc:
            warnings.append(f"[{ym} {prod}] stale-check could not run: {exc}")
            continue
        if s0 == s0 and s1 == s1 and abs(s0 - s1) < 1e-9:  # not NaN and equal
            warnings.append(
                f"[{ym} {prod}] STALE DATA SUSPECTED: {var} mean identical on "
                f"{f0.stem} and {f1.stem} ({s0:.6f}). The source aggregation may "
                f"have expired for this date range -- check the epoch mapping.")
    return warnings


# =============================================================================
# Main download loop
# =============================================================================

def run_download(cfg: dict):
    global _DEBUG
    check_dtn()
    check_active_env(cfg)
    check_required_tools()

    pid = cfg["project_id"]
    project_dir = Path(cfg["project_dir"])
    start = date.fromisoformat(cfg["start_date"])
    end = date.fromisoformat(cfg["end_date"])
    # Aggregation builds a 34-daily-record stack for EVERY project month,
    # starting at day 1 of the month (list_months includes the calendar month
    # of end_date). So the LAST project month's stack spans its day 1 through
    # ~day 34 (into the following month), regardless of where end_date falls
    # within that month. Extend the HYCOM download to the last project month's
    # last calendar day + 6 days to guarantee that 34-record window is fully
    # covered. (The 34-record ceiling satisfies SCHISM's nudging / *.th.nc
    # read-ahead: a 31-day run needs record index 33 <= 34.)
    STACK_PAD_DAYS = 6
    last_month_last_day = date(end.year, end.month,
                               monthrange(end.year, end.month)[1])
    download_end = last_month_last_day + timedelta(days=STACK_PAD_DAYS)
    lon_min = float(cfg["lon_min"]); lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"]); lat_max = float(cfg["lat_max"])
    lon_ref = str(cfg["lon_reference"])

    raw_dir = project_dir / f"M{pid}" / "raw" / "hycom"
    ssh_dir = raw_dir / "ssh"; ts_dir = raw_dir / "ts"; uv_dir = raw_dir / "uv"
    tmp_dir = raw_dir / "tmp"
    for d in [ssh_dir, ts_dir, uv_dir, tmp_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Full command trace goes to a debug log (keeps the screen clean).
    _DEBUG = DebugLog(project_dir / f"M{pid}" / "logs", "download_hycom")

    print(f"\n{'='*60}")
    print(f"  HYCOM download: {start} -> {end}")
    print(f"  (+{STACK_PAD_DAYS}-day pad for stack ceiling -> through {download_end})")
    print(f"  Domain: lon [{lon_min}, {lon_max}]  lat [{lat_min}, {lat_max}]")
    print(f"  Lon reference: {lon_ref}")
    print(f"  Raw output:    {raw_dir}")
    print(f"  Debug trace:   {_DEBUG.path}")
    print(f"{'='*60}\n")

    products = [("ssh", ssh_dir), ("ts", ts_dir), ("uv", uv_dir)]

    total_days = (download_end - start).days + 1
    prog = ProgressTracker(total=total_days, label="HYCOM download")

    failed_days = []

    # Group the date range into months and process one month at a time.
    # After a month finishes downloading, run the stale-data check on it
    # immediately; if it looks stale, STOP (later dates from the same expired
    # source would also be stale, so continuing wastes time).
    def month_key(d):
        return d.strftime("%Y%m")

    current = start
    while current <= download_end:
        ym = month_key(current)

        # Determine the last day of this month within the requested range.
        yr, mo = int(ym[:4]), int(ym[4:])
        month_last_day = date(yr, mo, monthrange(yr, mo)[1])
        month_end = min(download_end, month_last_day)

        print(f"\n{'#'*60}\n#  Month {ym}  ({current} -> {month_end})\n{'#'*60}")

        # --- Download every day of this month ---
        day = current
        while day <= month_end:
            date_str = day.isoformat()
            date_flat = day.strftime("%Y%m%d")
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
            day += timedelta(days=1)

        # --- Stale-data check for THIS month, before proceeding ---
        print(f"\n  Stale-data check for {ym}...")
        stale = stale_check_month(ym, ssh_dir, ts_dir, uv_dir, tmp_dir)
        if stale:
            print(f"\n{'='*60}")
            print(f"  STOPPING: month {ym} failed the stale-data check.")
            for w in stale:
                print("  WARNING: " + w)
            print("  Later dates from the same source would also be stale, so")
            print("  the download is halted. Investigate the epoch mapping for")
            print(f"  this date range, then re-run (completed months are kept).")
            if failed_days:
                print(f"\n  Note: {len(failed_days)} day/var download failure(s) so far:")
                for d, var in failed_days:
                    print(f"    {d}  {var}")
            print(f"  Full command trace: {_DEBUG.path}")
            print(f"{'='*60}\n")
            _DEBUG.close()
            sys.exit(1)
        print(f"  Stale-data check passed for {ym} (first vs last day differ).")

        # Advance to the first day of the next month.
        current = month_last_day + timedelta(days=1)

    # --- Summary ---
    print(f"\n{'='*60}")
    if not failed_days:
        print("  HYCOM download complete. No download failures.")
    else:
        print(f"  HYCOM download complete with {len(failed_days)} failure(s):")
        for d, var in failed_days:
            print(f"    {d}  {var}")
        print("  Re-run to retry (existing valid files are skipped).")
    print(f"  All months passed the stale-data check.")
    print(f"  Full command trace: {_DEBUG.path}")
    print(f"{'='*60}\n")

    _DEBUG.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download HYCOM data for SCHISM workflow")
    parser.add_argument("--config", required=True,
                        help="Path to the config/ directory")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    run_download(cfg)
