"""
downloaders/hycom.py
=================
Downloads daily HYCOM data (SSH, TS, UV) via OPeNDAP/THREDDS into the
project's raw/hycom/{ssh,ts,uv}/ directories.

Handles the FULL HYCOM server layout (2010 -> present):

  * GOFS 3.1 Reanalysis  (GLBv0.08/expt_53.X)     1994 -> 2015-12-31
  * GOFS 3.1 Analysis    (GLBv0.08/expt_57.2 ... 93.0)  2016 -> 2018-12-03
  * GOFS 3.1 Analysis    (GLBy0.08/expt_93.0)     2018-12-04 -> 2024-09-04
  * ESPC-D-V02           (ESPC-D-V02/{ssh,t3z,s3z,u3z,v3z}/YYYY)  2024-08-10 -> present

For TS and UV, both variables are fetched and merged into a single
ts_YYYYMMDD.nc / uv_YYYYMMDD.nc so that the aggregate step and the SCHISM
Fortran tools see an identical format regardless of epoch.

Includes per-month data quality checks:
  1. Constant-field detection with automatic interpolation repair.
  2. Stale-data check (first vs last day field mean comparison).

- Resume-safe (skips valid files, atomic finalize).
- Applies longitude reference frame handling based on domain.yaml.
- Fixes the time axis corrupted by ncpdq unpacking.
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import netCDF4 as nc4

# Use the shared load_config so model-specific YAML is always loaded.
from workflow.core.config import load_config, ProgressTracker, DebugLog

REQUIRED_TOOLS = ["ncks", "ncpdq", "ncap2", "ncrename", "ncatted", "ncwa"]
DTN_HOSTNAME_HINT = "dtn"

TDS = "https://tds.hycom.org/thredds/dodsC"

# Module-level debug log handle (set at the start of run_download).
_DEBUG = None

# =============================================================================
# HYCOM epoch table
# =============================================================================

HYCOM_EPOCHS = [
    {"last": "20151231", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_53.X/data/3hrly", "subpaths": False},
    {"last": "20170131", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_57.2", "subpaths": False},
    {"last": "20170531", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_92.8", "subpaths": False},
    {"last": "20170930", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_57.7", "subpaths": False},
    {"last": "20171231", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_92.9", "subpaths": False},
    {"last": "20181203", "kind": "combined",
     "base": f"{TDS}/GLBv0.08/expt_93.0", "subpaths": True},
    {"last": "20240904", "kind": "combined",
     "base": f"{TDS}/GLBy0.08/expt_93.0", "subpaths": True},
    {"last": None, "kind": "espc_archive",
     "base": f"{TDS}/datasets/ESPC-D-V02/data/archive"},
]

ESPC_ARCHIVE_PREFIX = "US058GCOM-OPSnce.espc-d-031-hycom_fcst_glby008"

ESPC_ARCHIVE_VAR = {
    "ssh": ("ssh",  "surf_el"),
    "ts":  [("t3z", "water_temp"), ("s3z", "salinity")],
    "uv":  [("u3z", "water_u"),    ("v3z", "water_v")],
}


def select_epoch(date_flat: str) -> dict:
    for epoch in HYCOM_EPOCHS:
        if epoch["last"] is None or date_flat <= epoch["last"]:
            return epoch
    raise ValueError(f"No HYCOM epoch found for date {date_flat}")


def source_urls(epoch: dict, product: str, year: int, date_flat: str = ""):
    if epoch["kind"] == "espc_archive":
        base   = epoch["base"]
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
    else:
        base = epoch["base"]
        sub  = epoch["subpaths"]
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
# Environment / tool checks
# =============================================================================

def _dbg(line: str):
    if _DEBUG is not None:
        _DEBUG.write(line)


def run(cmd, check=True):
    cmd = [str(c) for c in cmd]
    _dbg("CMD: " + " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stderr.strip():
        _dbg("STDERR: " + result.stderr.strip())
    if check and result.returncode != 0:
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
# Low-level OPeNDAP fetch
# =============================================================================

def _fetch_subset(date_str, url, variables, lon_min, lon_max,
                  lat_min, lat_max, lon_ref, out_file, tmp_dir, prefix,
                  no_time_subset=False) -> bool:
    if lon_ref not in ("360", "180"):
        print(f"  ERROR: unsupported lon_reference '{lon_ref}' (use '360' or '180')")
        return False

    time_args = [] if no_time_subset else ["-d", f"time,{date_str}"]

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
    date_flat = date_str.replace("-", "")
    year      = int(date_flat[:4])
    is_archive = epoch["kind"] == "espc_archive"
    pieces     = source_urls(epoch, product, year, date_flat)
    no_time    = is_archive

    piece_files = []
    for idx, (url, variables) in enumerate(pieces):
        pf = tmp_dir / f"{product}_src{idx}_{date_flat}.nc"
        cleanup_files(pf)
        ok = _fetch_subset(date_str, url, variables, lon_min, lon_max,
                           lat_min, lat_max, lon_ref, pf, tmp_dir,
                           prefix=f"{product}{idx}",
                           no_time_subset=no_time)
        if not ok and is_archive:
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
        shutil.copyfile(piece_files[0], merged)
        for pf in piece_files[1:]:
            run(["ncks", "-A", str(pf), str(merged)])
        for pf in piece_files:
            cleanup_files(pf)
    return merged

# =============================================================================
# Per-product processing -> final file
# =============================================================================

def _process_and_finalize(product, raw_nc, date_str, out_file, tmp_dir):
    date_flat = date_str.replace("-", "")
    test1     = tmp_dir / f"{product}_t1_{date_flat}.nc"
    test2     = tmp_dir / f"{product}_t2_{date_flat}.nc"
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
    else:
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
                     lon_min, lon_max, lat_min, lat_max,
                     lon_ref, tmp_dir) -> bool:
    raw = _fetch_product_raw(product, date_str, epoch, lon_min, lon_max,
                             lat_min, lat_max, lon_ref, tmp_dir)
    _process_and_finalize(product, raw, date_str, out_file, tmp_dir)
    return True

# =============================================================================
# Stale-data sanity check
# =============================================================================

def field_signature(nc_file: Path, varname: str, tmp_dir: Path):
    avg = tmp_dir / f"avg_{nc_file.stem}.nc"
    cleanup_files(avg)
    try:
        run(["ncwa", "-O", "-y", "avg", "-v", varname, str(nc_file), str(avg)])
        out = run(["ncks", "-s", "%f ", "-H", "-C", "-v", varname,
                   str(avg)]).stdout
    finally:
        cleanup_files(avg)
    vals = [float(x) for x in out.split() if x.strip()]
    if not vals:
        return float("nan")
    return sum(vals) / len(vals)


def stale_check_month(ym, ssh_dir, ts_dir, uv_dir, tmp_dir):
    year, month = int(ym[:4]), int(ym[4:])
    ndays = monthrange(year, month)[1]
    warnings = []
    checks = [("ssh", ssh_dir, "surf_el"),
              ("ts",  ts_dir,  "water_temp"),
              ("uv",  uv_dir,  "water_u")]
    for prod, vdir, var in checks:
        present = []
        for d in range(1, ndays + 1):
            flat = f"{ym}{d:02d}"
            f = vdir / f"{prod}_{flat}.nc"
            if f.exists() and f.stat().st_size > 0:
                present.append(f)
        if len(present) < 2:
            continue
        f0, f1 = present[0], present[-1]
        try:
            s0 = field_signature(f0, var, tmp_dir)
            s1 = field_signature(f1, var, tmp_dir)
        except Exception as exc:
            warnings.append(f"[{ym} {prod}] stale-check could not run: {exc}")
            continue
        if s0 == s0 and s1 == s1 and abs(s0 - s1) < 1e-9:
            warnings.append(
                f"[{ym} {prod}] STALE DATA SUSPECTED: {var} mean identical on "
                f"{f0.stem} and {f1.stem} ({s0:.6f}).")
    return warnings

# =============================================================================
# Data quality check and repair
# =============================================================================

_PRODUCT_VARS = {
    "ts":  (["water_temp", "salinity"], True),
    "uv":  (["water_u",    "water_v"],  True),
    "ssh": (["surf_el"],                False),
}

_CONSTANT_STD_THRESHOLD = 1e-6
_MAX_CONSECUTIVE_BAD    = 3


def check_constant_field(nc_file: Path, product: str) -> list:
    varnames, has_depth = _PRODUCT_VARS.get(product, ([], False))
    issues = []
    try:
        ds = nc4.Dataset(nc_file)
        try:
            for varname in varnames:
                if varname not in ds.variables:
                    continue
                data = ds.variables[varname][:]
                if data.ndim == 4:
                    data = data[0]
                elif data.ndim == 3:
                    data = data[0]
                if not isinstance(data, np.ma.MaskedArray):
                    fv = getattr(ds.variables[varname], '_FillValue', None)
                    if fv is not None:
                        data = np.ma.masked_equal(data, fv)
                    else:
                        data = np.ma.array(data)

                if has_depth and data.ndim == 3:
                    for k in range(data.shape[0]):
                        sl = data[k]
                        valid_pts = int(sl.count())
                        if valid_pts == 0:
                            continue
                        std = float(sl.std())
                        if std < _CONSTANT_STD_THRESHOLD:
                            issues.append(
                                f"{varname} level {k}: std={std:.2e}, "
                                f"mean={float(sl.mean()):.4f}, "
                                f"valid_pts={valid_pts}"
                            )
                else:
                    sl = data
                    valid_pts = int(sl.count())
                    if valid_pts > 0:
                        std = float(sl.std())
                        if std < _CONSTANT_STD_THRESHOLD:
                            issues.append(
                                f"{varname}: std={std:.2e}, "
                                f"mean={float(sl.mean()):.4f}, "
                                f"valid_pts={valid_pts}"
                            )
        finally:
            ds.close()
    except Exception as exc:
        issues.append(f"could not open/read file: {exc}")
    return issues


def _interp_nc(prev_file: Path, next_file: Path,
               bad_date: date, prev_date: date, next_date: date,
               product: str, out_file: Path):
    varnames, _ = _PRODUCT_VARS.get(product, ([], False))
    total_days  = (next_date - prev_date).days
    alpha = (bad_date - prev_date).days / total_days if total_days > 0 else 0.5

    shutil.copy2(prev_file, out_file)

    ds_out  = nc4.Dataset(out_file,  'r+')
    ds_prev = nc4.Dataset(prev_file, 'r')
    ds_next = nc4.Dataset(next_file, 'r')
    try:
        for varname in varnames:
            if varname not in ds_prev.variables:
                continue
            v_prev = ds_prev.variables[varname][:]
            v_next = ds_next.variables[varname][:]
            p = np.ma.filled(v_prev.astype(np.float64), np.nan)
            n = np.ma.filled(v_next.astype(np.float64), np.nan)
            interp = (1.0 - alpha) * p + alpha * n
            fv = getattr(ds_prev.variables[varname], '_FillValue', -30000.0)
            interp = np.where(np.isnan(interp), fv, interp)
            ds_out.variables[varname][:] = interp.astype(
                ds_prev.variables[varname].dtype)
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ds_out.repaired_by_workflow = (
            f"interpolated from {prev_file.name} (weight {1-alpha:.3f}) "
            f"and {next_file.name} (weight {alpha:.3f}) "
            f"on {timestamp} (UTC)"
        )
    finally:
        ds_out.close()
        ds_prev.close()
        ds_next.close()


def quality_check_and_repair_month(
        ym: str,
        ssh_dir: Path, ts_dir: Path, uv_dir: Path,
        tmp_dir: Path) -> bool:
    prod_dirs = {"ts": ts_dir, "uv": uv_dir, "ssh": ssh_dir}
    year, month = int(ym[:4]), int(ym[4:])
    ndays = monthrange(year, month)[1]
    all_ok = True

    for product, vdir in prod_dirs.items():
        month_files = []
        for d in range(1, ndays + 1):
            flat = f"{ym}{d:02d}"
            f = vdir / f"{product}_{flat}.nc"
            if f.exists() and f.stat().st_size > 0:
                month_files.append((date(year, month, d), f))
        if not month_files:
            continue

        bad_days = []
        for day_date, fpath in month_files:
            issues = check_constant_field(fpath, product)
            if issues:
                flat = day_date.strftime("%Y%m%d")
                msg = (f"WARNING: [{product.upper()} {flat}] "
                       f"corrupted/constant field in {fpath.name}:")
                print(f"  {msg}")
                _dbg(msg)
                for iss in issues:
                    print(f"    - {iss}")
                    _dbg(f"    - {iss}")
                bad_days.append((day_date, fpath, issues))

        if not bad_days:
            _dbg(f"  quality check [{product.upper()} {ym}]: all days clean.")
            continue

        runs = []
        current_run = [bad_days[0]]
        for bd in bad_days[1:]:
            if (bd[0] - current_run[-1][0]).days == 1:
                current_run.append(bd)
            else:
                runs.append(current_run)
                current_run = [bd]
        runs.append(current_run)

        fatal_runs = [r for r in runs if len(r) > _MAX_CONSECUTIVE_BAD]
        if fatal_runs:
            msg = (f"ERROR: [{product.upper()} {ym}] {len(fatal_runs)} run(s) of "
                   f"more than {_MAX_CONSECUTIVE_BAD} consecutive corrupted days.")
            print(f"\n  {'!'*58}")
            print(f"  {msg}")
            _dbg(msg)
            for r in fatal_runs:
                dates_str = ", ".join(d.strftime("%Y%m%d") for d, _, _ in r)
                detail = f"    Bad days ({len(r)}): {dates_str}"
                print(detail); _dbg(detail)
            print(f"  {'!'*58}\n")
            all_ok = False
            continue

        good_files = {d: f for d, f in month_files
                      if not any(d == bd[0] for bd in bad_days)}
        for offset in range(1, 8):
            for delta, prefix in [(-offset, "before"), (offset, "after")]:
                if delta < 0:
                    chk_d = date(year, month, 1) + timedelta(days=delta)
                else:
                    chk_d = date(year, month, ndays) + timedelta(days=delta)
                flat_c = chk_d.strftime("%Y%m%d")
                fc = vdir / f"{product}_{flat_c}.nc"
                if (fc.exists() and fc.stat().st_size > 0
                        and chk_d not in good_files):
                    if not check_constant_field(fc, product):
                        good_files[chk_d] = fc

        sorted_good = sorted(good_files.keys())

        for run_group in runs:
            for bad_date_val, bad_fpath, _ in run_group:
                flat = bad_date_val.strftime("%Y%m%d")
                prev_candidates = [d for d in sorted_good if d < bad_date_val]
                next_candidates = [d for d in sorted_good if d > bad_date_val]
                prev_date = prev_candidates[-1] if prev_candidates else None
                next_date = next_candidates[0]  if next_candidates else None

                if prev_date is None and next_date is None:
                    msg = (f"ERROR: [{product.upper()} {flat}] no good neighbor "
                           f"within 7 days — cannot repair.")
                    print(f"  {msg}"); _dbg(msg)
                    all_ok = False
                    continue

                if prev_date is None: prev_date = next_date
                if next_date is None: next_date = prev_date

                prev_file = good_files[prev_date]
                next_file = good_files[next_date]

                bad_backup = bad_fpath.with_suffix(".nc.bad")
                bad_fpath.rename(bad_backup)
                msg_rename = (f"  Renamed corrupted file: "
                              f"{bad_fpath.name} -> {bad_backup.name}")
                print(msg_rename); _dbg(msg_rename)

                try:
                    _interp_nc(prev_file, next_file,
                               bad_date_val, prev_date, next_date,
                               product, bad_fpath)
                    total = max((next_date - prev_date).days, 1)
                    w_prev = 1 - (bad_date_val - prev_date).days / total
                    w_next =     (bad_date_val - prev_date).days / total
                    msg_repair = (
                        f"  WARNING: [{product.upper()} {flat}] repaired by "
                        f"interpolation between {prev_file.name} "
                        f"(weight {w_prev:.3f}) and {next_file.name} "
                        f"(weight {w_next:.3f}). "
                        f"Original saved as {bad_backup.name}."
                    )
                    print(f"  {msg_repair}"); _dbg(msg_repair)
                except Exception as exc:
                    msg_err = (f"ERROR: [{product.upper()} {flat}] "
                               f"interpolation failed: {exc}")
                    print(f"  {msg_err}"); _dbg(msg_err)
                    if bad_backup.exists() and not bad_fpath.exists():
                        bad_backup.rename(bad_fpath)
                    all_ok = False

    return all_ok

# =============================================================================
# Main download loop
# =============================================================================

def run_download(cfg: dict):
    global _DEBUG
    check_dtn()
    check_active_env(cfg)
    check_required_tools()

    pid         = cfg["project_id"]
    project_dir = Path(cfg["project_dir"])
    start       = date.fromisoformat(cfg["start_date"])
    end         = date.fromisoformat(cfg["end_date"])

    STACK_PAD_DAYS = 6
    last_month_last_day = date(end.year, end.month,
                               monthrange(end.year, end.month)[1])
    download_end = last_month_last_day + timedelta(days=STACK_PAD_DAYS)
    lon_min = float(cfg["lon_min"]); lon_max = float(cfg["lon_max"])
    lat_min = float(cfg["lat_min"]); lat_max = float(cfg["lat_max"])
    lon_ref = str(cfg["lon_reference"])

    raw_dir = project_dir / f"M{pid}" / "raw" / "hycom"
    ssh_dir = raw_dir / "ssh"
    ts_dir  = raw_dir / "ts"
    uv_dir  = raw_dir / "uv"
    tmp_dir = raw_dir / "tmp"
    for d in [ssh_dir, ts_dir, uv_dir, tmp_dir]:
        d.mkdir(parents=True, exist_ok=True)

    _DEBUG = DebugLog(project_dir / f"M{pid}" / "logs", "download_hycom")

    print(f"\n{'='*60}")
    print(f"  HYCOM download: {start} -> {end}")
    print(f"  (+{STACK_PAD_DAYS}-day pad -> through {download_end})")
    print(f"  Domain: lon [{lon_min}, {lon_max}]  lat [{lat_min}, {lat_max}]")
    print(f"  Lon reference: {lon_ref}")
    print(f"  Raw output:    {raw_dir}")
    print(f"  Debug trace:   {_DEBUG.path}")
    print(f"{'='*60}\n")

    products     = [("ssh", ssh_dir), ("ts", ts_dir), ("uv", uv_dir)]
    total_days   = (download_end - start).days + 1
    prog         = ProgressTracker(total=total_days, label="HYCOM download")
    failed_days  = []

    def month_key(d):
        return d.strftime("%Y%m")

    current = start
    while current <= download_end:
        ym = month_key(current)
        yr, mo = int(ym[:4]), int(ym[4:])
        month_last_day = date(yr, mo, monthrange(yr, mo)[1])
        month_end = min(download_end, month_last_day)

        print(f"\n{'#'*60}\n#  Month {ym}  ({current} -> {month_end})\n{'#'*60}")

        day = current
        while day <= month_end:
            date_str  = day.isoformat()
            date_flat = day.strftime("%Y%m%d")
            epoch     = select_epoch(date_flat)

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

        print(f"\n  Quality check (constant-field detection) for {ym}...")
        _dbg(f"quality_check_and_repair_month: starting for {ym}")
        qc_ok = quality_check_and_repair_month(
            ym, ssh_dir, ts_dir, uv_dir, tmp_dir)
        if not qc_ok:
            print(f"\n{'='*60}")
            print(f"  STOPPING: month {ym} has unrecoverable data quality issues.")
            print(f"  See details above. Fix the bad raw files, then re-run:")
            print(f"    stofs-ak --run --only download_hycom --config <cfg>")
            if failed_days:
                print(f"\n  Note: {len(failed_days)} day/var download failure(s):")
                for d, var in failed_days:
                    print(f"    {d}  {var}")
            print(f"  Full command trace: {_DEBUG.path}")
            print(f"{'='*60}\n")
            _DEBUG.close()
            sys.exit(1)
        print(f"  Quality check passed for {ym}.")

        print(f"\n  Stale-data check for {ym}...")
        stale = stale_check_month(ym, ssh_dir, ts_dir, uv_dir, tmp_dir)
        if stale:
            print(f"\n{'='*60}")
            print(f"  STOPPING: month {ym} failed the stale-data check.")
            for w in stale:
                print("  WARNING: " + w)
            print("  Later dates from the same source would also be stale.")
            if failed_days:
                print(f"\n  Note: {len(failed_days)} day/var download failure(s):")
                for d, var in failed_days:
                    print(f"    {d}  {var}")
            print(f"  Full command trace: {_DEBUG.path}")
            print(f"{'='*60}\n")
            _DEBUG.close()
            sys.exit(1)
        print(f"  Stale-data check passed for {ym}.")

        current = month_last_day + timedelta(days=1)

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
    parser = argparse.ArgumentParser(
        description="Download HYCOM data for SCHISM workflow")
    parser.add_argument("--config", required=True,
                        help="Path to the config/ directory")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    run_download(cfg)
