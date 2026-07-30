"""
download_hycom.py
=================
Downloads daily HYCOM GOFS 3.1 data (SSH, TS, UV) via OPeNDAP/THREDDS
into the project's raw/hycom/{ssh,ts,uv}/ directories.

- Skips days that have already been downloaded (resume-safe)
- Handles the full HYCOM experiment epoch sequence (2018-present)
- Applies longitude reference frame correction based on domain.yaml
- Processes each daily file (unpack, fix time axis, rename dims/vars, cast to float32)

Usage (called by orchestrator.py, not directly):
    python download_hycom.py --config /path/to/M01/config
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml


# Command-line tools required by the download/processing steps.
REQUIRED_TOOLS = ["ncks", "ncpdq", "ncap2", "ncrename", "ncatted"]

# Substring that identifies a Data Transfer Node on Hercules. The HYCOM
# OPeNDAP download requires external internet access, which is only available
# on the DTN (e.g. hercules-dtn.hpc.msstate.edu), not the regular login/compute
# nodes.
DTN_HOSTNAME_HINT = "dtn"


# =============================================================================
# HYCOM GOFS 3.1 experiment epoch table
# Source: https://www.hycom.org/dataserver/gofs-3pt1/analysis
# Each entry: (last_date_inclusive, url_base)
# The list is checked in order; the first matching epoch is used.
# =============================================================================
HYCOM_EPOCHS = {
    "ssh": [
        ("20170131", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_57.2"),
        ("20170531", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_92.8"),
        ("20170930", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_57.7"),
        ("20171231", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_92.9"),
        ("20181203", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_93.0/ssh"),
        (None,       "https://tds.hycom.org/thredds/dodsC/GLBy0.08/expt_93.0/ssh"),
    ],
    "ts": [
        ("20170131", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_57.2"),
        ("20170531", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_92.8"),
        ("20170930", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_57.7"),
        ("20171231", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_92.9"),
        ("20181203", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_93.0/ts3z"),
        (None,       "https://tds.hycom.org/thredds/dodsC/GLBy0.08/expt_93.0/ts3z"),
    ],
    "uv": [
        ("20170131", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_57.2"),
        ("20170531", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_92.8"),
        ("20170930", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_57.7"),
        ("20171231", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_92.9"),
        ("20181203", "https://tds.hycom.org/thredds/dodsC/GLBv0.08/expt_93.0/uv3z"),
        (None,       "https://tds.hycom.org/thredds/dodsC/GLBy0.08/expt_93.0/uv3z"),
    ],
}


# =============================================================================
# Helpers
# =============================================================================

def load_config(config_dir: Path) -> dict:
    """Load and merge all YAML config files from the config directory."""
    cfg = {}
    for fname in ("project.yaml", "domain.yaml", "steps.yaml", "envs.yaml"):
        fpath = config_dir / fname
        if not fpath.exists():
            print(f"ERROR: Config file not found: {fpath}")
            sys.exit(1)
        with open(fpath) as f:
            cfg.update(yaml.safe_load(f))
    return cfg


def get_epoch_url(product: str, date_flat: str) -> str:
    """Return the correct HYCOM THREDDS URL for a given product and date."""
    for cutoff, url in HYCOM_EPOCHS[product]:
        if cutoff is None or date_flat <= cutoff:
            return url
    # Should never reach here
    raise ValueError(f"Could not determine HYCOM epoch for date {date_flat}")


def run(cmd: list, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command, printing it first for transparency."""
    print("  CMD:", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  STDERR: {result.stderr.strip()}")
        raise RuntimeError(f"Command failed with exit code {result.returncode}")
    return result


def is_complete_file(path: Path) -> bool:
    """Return True if a final output file exists and is non-empty."""
    return path.exists() and path.stat().st_size > 0


def finalize_output(tmp_file: Path, final_file: Path):
    """Atomically move a non-empty temp output into its final location."""
    if not is_complete_file(tmp_file):
        raise RuntimeError(f"Temporary output was not created or is empty: {tmp_file}")
    tmp_file.replace(final_file)


def cleanup_files(*paths: Path):
    """Best-effort cleanup of temporary files."""
    for path in paths:
        path.unlink(missing_ok=True)


def check_required_tools():
    """
    Verify that all required NCO tools are available on PATH before starting.
    Exits with a clear message if any are missing (e.g. an unloaded module),
    rather than crashing mid-download with a raw FileNotFoundError.
    """
    missing = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
    if missing:
        print("ERROR: the following required command-line tools were not found on PATH:")
        for tool in missing:
            print(f"    - {tool}")
        print("These are provided by NCO. On Hercules, load the module first, e.g.:")
        print("    module load nco")
        sys.exit(1)


def check_active_env(cfg: dict):
    """
    Warn if the active conda environment does not match the one configured for
    the download_hycom step in envs.yaml.

    Phase 1 downloads run INSIDE the environment the user activated on the DTN
    (e.g. `conda activate hycom_env`). We do not switch environments here; we
    only verify that the expected env is active so a wrong-env mistake is
    caught early. This is a soft warning, not a hard failure.
    """
    expected = cfg.get("conda_envs", {}).get("download_hycom")
    if not expected:
        return  # nothing configured to verify against

    active = os.environ.get("CONDA_DEFAULT_ENV")
    if active is None:
        print(f"  WARNING: no active conda environment detected, but envs.yaml "
              f"expects '{expected}' for download_hycom.")
    elif active != expected:
        print(f"  WARNING: active conda environment is '{active}', but envs.yaml "
              f"expects '{expected}' for download_hycom.")
        print(f"           If this is intentional you can ignore this. Otherwise:")
        print(f"           conda activate {expected}")
    else:
        print(f"  Env check: active conda environment '{active}' matches config. OK.")


def check_dtn():
    """
    Warn if we do not appear to be on a Data Transfer Node.

    The HYCOM OPeNDAP download requires external internet access, which on
    Hercules is only available from the DTN (hercules-dtn.hpc.msstate.edu).
    Regular login and compute nodes cannot reach tds.hycom.org, so the
    download would fail with connection errors.

    This is a soft check: set ALLOW_NON_DTN=1 to bypass it (e.g. if running
    on another system whose transfer nodes are not named 'dtn').
    """
    hostname = socket.gethostname()
    if DTN_HOSTNAME_HINT in hostname.lower():
        print(f"  Host check: running on '{hostname}' (looks like a DTN). OK.")
        return

    if os.environ.get("ALLOW_NON_DTN") == "1":
        print(f"  Host check: '{hostname}' is not a DTN, but ALLOW_NON_DTN=1 is set. "
              f"Proceeding anyway.")
        return

    print(f"ERROR: current host '{hostname}' does not look like a Data Transfer Node.")
    print("The HYCOM download needs external internet access, which on Hercules")
    print("is only available on the DTN. Connect to it first, then re-run:")
    print("    ssh hercules-dtn.hpc.msstate.edu")
    print("    conda activate hycom_env   # or your download environment")
    print("    python orchestrator.py --run --config <config_dir>")
    print()
    print("If you are on a system whose transfer node is not named 'dtn',")
    print("bypass this check by setting:  export ALLOW_NON_DTN=1")
    sys.exit(1)


def hours_since_2000(date_str: str) -> int:
    """
    Compute hours elapsed between 2000-01-01 00:00 UTC and date_str (YYYY-MM-DD).
    Used to fix the time axis corrupted by ncpdq unpacking.
    """
    ref = date(2000, 1, 1)
    d = date.fromisoformat(date_str)
    return int((d - ref).total_seconds() // 3600)


# =============================================================================
# Per-variable download + processing functions
# =============================================================================

def download_ssh(date_str: str, url: str, out_file: Path,
                 lon_min: float, lon_max: float,
                 lat_min: float, lat_max: float,
                 lon_ref: str, tmp_dir: Path) -> bool:
    """
    Download and process one day of SSH data.
    Returns True on success, False on failure.
    """
    date_flat = date_str.replace('-', '')
    raw_nc    = tmp_dir / f"ssh_raw_{date_flat}.nc"
    test1_nc  = tmp_dir / f"ssh_test1_{date_flat}.nc"
    test2_nc  = tmp_dir / f"ssh_test2_{date_flat}.nc"
    final_tmp = tmp_dir / f"{out_file.name}.tmp"
    cleanup_files(raw_nc, test1_nc, test2_nc, final_tmp)

    success = _download_with_lon_fallback(
        date_str=date_str,
        url=url,
        variables="surf_el",
        lon_min=lon_min, lon_max=lon_max,
        lat_min=lat_min, lat_max=lat_max,
        lon_ref=lon_ref,
        out_file=raw_nc,
        tmp_dir=tmp_dir,
        prefix="ssh",
    )
    if not success:
        return False

    hrs = hours_since_2000(date_str)

    # Unpack
    run(["ncpdq", "-O", "-U", str(raw_nc), str(test1_nc)])
    # Fix time axis, cast to float32
    run(["ncap2", "-O", "-s", f"time(:)={hrs}.0", str(test1_nc), str(test1_nc)])
    run(["ncap2", "-O", "-s",
         "lat=float(lat); lon=float(lon); surf_el=float(surf_el);",
         str(test1_nc), str(test2_nc)])
    # Rename lat/lon dims and vars
    run(["ncrename", "-O",
         "-d", "lon,xlon", "-d", "lat,ylat",
         "-v", "lon,xlon", "-v", "lat,ylat",
         str(test2_nc)])
    # Make time a record dimension
    run(["ncks", "-O", "--mk_rec_dmn", "time", str(test2_nc), str(final_tmp)])
    finalize_output(final_tmp, out_file)

    # Clean up temporaries
    cleanup_files(raw_nc, test1_nc, test2_nc, final_tmp)

    return True


def download_ts(date_str: str, url: str, out_file: Path,
                lon_min: float, lon_max: float,
                lat_min: float, lat_max: float,
                lon_ref: str, tmp_dir: Path) -> bool:
    """
    Download and process one day of TS (temperature + salinity) data.
    Returns True on success, False on failure.
    """
    date_flat = date_str.replace('-', '')
    raw_nc    = tmp_dir / f"ts_raw_{date_flat}.nc"
    test1_nc  = tmp_dir / f"ts_test1_{date_flat}.nc"
    test2_nc  = tmp_dir / f"ts_test2_{date_flat}.nc"
    final_tmp = tmp_dir / f"{out_file.name}.tmp"
    cleanup_files(raw_nc, test1_nc, test2_nc, final_tmp)

    success = _download_with_lon_fallback(
        date_str=date_str,
        url=url,
        variables="water_temp,salinity",
        lon_min=lon_min, lon_max=lon_max,
        lat_min=lat_min, lat_max=lat_max,
        lon_ref=lon_ref,
        out_file=raw_nc,
        tmp_dir=tmp_dir,
        prefix="ts",
    )
    if not success:
        return False

    hrs = hours_since_2000(date_str)

    # Unpack
    run(["ncpdq", "-O", "-U", str(raw_nc), str(test1_nc)])
    # Fix time axis, cast to float32
    run(["ncap2", "-O", "-s", f"time(:)={hrs}.0", str(test1_nc), str(test1_nc)])
    run(["ncap2", "-O", "-s",
         "depth=float(depth); lat=float(lat); lon=float(lon); "
         "water_temp=float(water_temp); salinity=float(salinity);",
         str(test1_nc), str(test2_nc)])
    # Rename dims, vars, and update units
    run(["ncrename", "-O",
         "-d", "lon,xlon", "-d", "lat,ylat",
         "-v", "lon,xlon", "-v", "lat,ylat",
         str(test2_nc)])
    run(["ncatted", "-O", "-a", "units,water_temp,m,c,degC", str(test2_nc)])
    # Make time a record dimension
    run(["ncks", "-O", "--mk_rec_dmn", "time", str(test2_nc), str(final_tmp)])
    finalize_output(final_tmp, out_file)

    cleanup_files(raw_nc, test1_nc, test2_nc, final_tmp)

    return True


def download_uv(date_str: str, url: str, out_file: Path,
                lon_min: float, lon_max: float,
                lat_min: float, lat_max: float,
                lon_ref: str, tmp_dir: Path) -> bool:
    """
    Download and process one day of UV (ocean velocity) data.
    Returns True on success, False on failure.
    """
    date_flat = date_str.replace('-', '')
    raw_nc    = tmp_dir / f"uv_raw_{date_flat}.nc"
    test1_nc  = tmp_dir / f"uv_test1_{date_flat}.nc"
    test2_nc  = tmp_dir / f"uv_test2_{date_flat}.nc"
    final_tmp = tmp_dir / f"{out_file.name}.tmp"
    cleanup_files(raw_nc, test1_nc, test2_nc, final_tmp)

    success = _download_with_lon_fallback(
        date_str=date_str,
        url=url,
        variables="water_u,water_v",
        lon_min=lon_min, lon_max=lon_max,
        lat_min=lat_min, lat_max=lat_max,
        lon_ref=lon_ref,
        out_file=raw_nc,
        tmp_dir=tmp_dir,
        prefix="uv",
    )
    if not success:
        return False

    hrs = hours_since_2000(date_str)

    # Unpack
    run(["ncpdq", "-O", "-U", str(raw_nc), str(test1_nc)])
    # Fix time axis, cast to float32
    run(["ncap2", "-O", "-s", f"time(:)={hrs}.0", str(test1_nc), str(test1_nc)])
    run(["ncap2", "-O", "-s",
         "depth=float(depth); lat=float(lat); lon=float(lon); "
         "water_u=float(water_u); water_v=float(water_v);",
         str(test1_nc), str(test2_nc)])
    # Rename lat/lon dims and vars
    run(["ncrename", "-O",
         "-d", "lon,xlon", "-d", "lat,ylat",
         "-v", "lon,xlon", "-v", "lat,ylat",
         str(test2_nc)])
    # Make time a record dimension
    run(["ncks", "-O", "--mk_rec_dmn", "time", str(test2_nc), str(final_tmp)])
    finalize_output(final_tmp, out_file)

    cleanup_files(raw_nc, test1_nc, test2_nc, final_tmp)

    return True


# =============================================================================
# Shared download helper (handles lon reference frame fallback)
# =============================================================================

def _download_with_lon_fallback(date_str, url, variables,
                                 lon_min, lon_max, lat_min, lat_max,
                                 lon_ref, out_file, tmp_dir, prefix) -> bool:
    """
    Download one day/variable, handling the longitude reference frame.

    The `lon_ref` config field describes the convention used by the MESH
    (hgrid.gr3), and therefore the convention we want the downloaded data to
    be in so it is consistent with the grid:

      lon_ref == "360":
          Mesh uses 0..360 longitudes (e.g. Bering Sea, where 180 crosses the
          domain). Downloaded data must be on a 0..360 grid.
            Attempt A: subset directly with the (0..360) bounds. Works for the
                       modern GLBy/GLBv 0..360 aggregations.
            Attempt B: if A fails, the source is on a legacy -180..180 grid;
                       download without lon subsetting, shift negative lons by
                       +360, then subset with the (0..360) bounds.

      lon_ref == "180":
          Mesh uses -180..180 longitudes. Downloaded data must be on a
          -180..180 grid.
            Attempt A: subset directly with the (-180..180) bounds. Works for
                       legacy sources already on a -180..180 grid.
            Attempt B: if A fails, the source is on a 0..360 grid; download
                       without lon subsetting, shift lons > 180 to negative
                       (lon-360), then subset with the (-180..180) bounds.

    NOTE: The "360" Attempt A path is the proven production path (matches the
    original Bering Sea download scripts). The "180" convention and the
    Attempt B longitude-shift/re-sort path are NOT yet validated against real
    HYCOM data and should be treated as experimental.

    Returns True on success, False if both attempts fail.
    """
    if lon_ref not in ("360", "180"):
        print(f"  ERROR: unsupported lon_reference '{lon_ref}' (use '360' or '180')")
        return False

    # Attempt A: direct subsetting with target lon bounds
    cmd_a = [
        "ncks", "-O",
        "-d", f"lon,{lon_min},{lon_max}",
        "-d", f"lat,{lat_min},{lat_max}",
        "-d", f"time,{date_str}",
        "-v", variables,
        url, str(out_file),
    ]
    result = subprocess.run(cmd_a, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  -> Attempt A succeeded (direct lon subsetting)")
        return True

    print(f"  -> Attempt A failed, trying opposite-convention fallback...")

    # Attempt B: download full lon range, shift lons to target convention, subset
    tmp_global = tmp_dir / f"{prefix}_global_{date_str.replace('-','')}.nc"
    tmp_pos    = tmp_dir / f"{prefix}_shifted_{date_str.replace('-','')}.nc"

    cmd_b = [
        "ncks", "-O",
        "-d", f"lat,{lat_min},{lat_max}",
        "-d", f"time,{date_str}",
        "-v", variables,
        url, str(tmp_global),
    ]
    result = subprocess.run(cmd_b, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  -> Attempt B also failed: {result.stderr.strip()}")
        tmp_global.unlink(missing_ok=True)
        return False

    if lon_ref == "360":
        # Source is -180..180; shift negatives up to 0..360
        shift_expr = "where(lon<0) lon=lon+360"
    else:  # lon_ref == "180"
        # Source is 0..360; shift values >180 down to -180..180
        shift_expr = "where(lon>180) lon=lon-360"

    run(["ncap2", "-O", "-s", shift_expr, str(tmp_global), str(tmp_pos)])
    # After shifting, the lon coordinate is no longer monotonic (it wraps).
    # ncks range subsetting (-d lon,min,max) requires a monotonic coordinate,
    # so re-sort along the lon dimension before subsetting.
    run(["ncks", "-O", "--msa", "-d", f"lon,{lon_min},{lon_max}",
         str(tmp_pos), str(out_file)])

    tmp_global.unlink(missing_ok=True)
    tmp_pos.unlink(missing_ok=True)

    print(f"  -> Attempt B succeeded (lon shifted to '{lon_ref}' convention)")
    return True


# =============================================================================
# Main download loop
# =============================================================================

def run_download(cfg: dict):
    """Main entry point: iterate over all days and download SSH, TS, UV."""

    check_dtn()
    check_active_env(cfg)
    check_required_tools()

    pid         = cfg["project_id"]
    project_dir = Path(cfg["project_dir"])
    start       = date.fromisoformat(cfg["start_date"])
    end         = date.fromisoformat(cfg["end_date"])
    lon_min     = float(cfg["lon_min"])
    lon_max     = float(cfg["lon_max"])
    lat_min     = float(cfg["lat_min"])
    lat_max     = float(cfg["lat_max"])
    lon_ref     = cfg["lon_reference"]

    raw_dir = project_dir / f"M{pid}" / "raw" / "hycom"
    ssh_dir = raw_dir / "ssh"
    ts_dir  = raw_dir / "ts"
    uv_dir  = raw_dir / "uv"
    tmp_dir = raw_dir / "tmp"

    for d in [ssh_dir, ts_dir, uv_dir, tmp_dir]:
        d.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  HYCOM download: {start} -> {end}")
    print(f"  Domain: lon [{lon_min}, {lon_max}]  lat [{lat_min}, {lat_max}]")
    print(f"  Lon reference: {lon_ref}")
    print(f"  Raw output:    {raw_dir}")
    print(f"{'='*60}\n")

    current = start
    failed_days = []

    while current <= end:
        date_str  = current.isoformat()          # e.g. "2024-09-01"
        date_flat = current.strftime("%Y%m%d")   # e.g. "20240901"

        print(f"\n--- {date_str} ---")

        # --- SSH ---
        ssh_out = ssh_dir / f"ssh_{date_flat}.nc"
        if is_complete_file(ssh_out):
            print(f"  SSH: already exists, skipping.")
        else:
            if ssh_out.exists():
                print(f"  SSH: existing file is empty/incomplete, re-downloading.")
                ssh_out.unlink()
            url = get_epoch_url("ssh", date_flat)
            print(f"  SSH: downloading from {url}")
            try:
                ok = download_ssh(date_str, url, ssh_out,
                                  lon_min, lon_max, lat_min, lat_max,
                                  lon_ref, tmp_dir)
            except Exception as exc:
                print(f"  ERROR: SSH processing failed for {date_str}: {exc}")
                ok = False
            if not ok:
                print(f"  ERROR: SSH download failed for {date_str}")
                failed_days.append((date_str, "ssh"))

        # --- TS ---
        ts_out = ts_dir / f"ts_{date_flat}.nc"
        if is_complete_file(ts_out):
            print(f"  TS:  already exists, skipping.")
        else:
            if ts_out.exists():
                print(f"  TS:  existing file is empty/incomplete, re-downloading.")
                ts_out.unlink()
            url = get_epoch_url("ts", date_flat)
            print(f"  TS:  downloading from {url}")
            try:
                ok = download_ts(date_str, url, ts_out,
                                 lon_min, lon_max, lat_min, lat_max,
                                 lon_ref, tmp_dir)
            except Exception as exc:
                print(f"  ERROR: TS processing failed for {date_str}: {exc}")
                ok = False
            if not ok:
                print(f"  ERROR: TS download failed for {date_str}")
                failed_days.append((date_str, "ts"))

        # --- UV ---
        uv_out = uv_dir / f"uv_{date_flat}.nc"
        if is_complete_file(uv_out):
            print(f"  UV:  already exists, skipping.")
        else:
            if uv_out.exists():
                print(f"  UV:  existing file is empty/incomplete, re-downloading.")
                uv_out.unlink()
            url = get_epoch_url("uv", date_flat)
            print(f"  UV:  downloading from {url}")
            try:
                ok = download_uv(date_str, url, uv_out,
                                 lon_min, lon_max, lat_min, lat_max,
                                 lon_ref, tmp_dir)
            except Exception as exc:
                print(f"  ERROR: UV processing failed for {date_str}: {exc}")
                ok = False
            if not ok:
                print(f"  ERROR: UV download failed for {date_str}")
                failed_days.append((date_str, "uv"))

        current += timedelta(days=1)

    # Summary
    print(f"\n{'='*60}")
    if not failed_days:
        print("  HYCOM download complete. No failures.")
    else:
        print(f"  HYCOM download complete with {len(failed_days)} failure(s):")
        for d, var in failed_days:
            print(f"    {d}  {var}")
        print("  Re-run the workflow to retry failed days (existing files are skipped).")
    print(f"{'='*60}\n")


# =============================================================================
# CLI entry point (when called directly for debugging)
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download HYCOM data for SCHISM workflow")
    parser.add_argument("--config", required=True,
                        help="Path to the config/ directory (containing project.yaml etc.)")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    run_download(cfg)
