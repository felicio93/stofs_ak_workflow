"""
gen_param.py
============
Phase 3 (interactive) — Copy fix/param.nml into each I{ID}_YYYYMM/ directory
with the date and timing parameters updated for that specific month.

Parameters updated per month
-----------------------------
    start_year   <- year of the month
    start_month  <- month number
    start_day    <- 1 (always)
    start_hour   <- 0 (always)
    rnday        <- calendar days in the month
    nhot_write   <- int(rnday * 86400 / dt)  writes hotstart at last timestep

Parameters read from the template (not modified)
-------------------------------------------------
    dt           <- read to compute nhot_write
    ihot         <- must be 1 for monthly hot-start chaining; a warning is
                    printed if a different value is found (not changed)
    all others   <- preserved exactly as in the template

Usage
-----
Place your calibrated param.nml in fix/param.nml.  The script copies it to
I{ID}/I{ID}_YYYYMM/param.nml for every month in the project date range,
substituting only the six parameters above.

Resume-safe: months whose param.nml already exists are skipped.
"""

import re
import sys
from calendar import monthrange
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow.config import model_dir, list_months, ProgressTracker


# =============================================================================
# Namelist helpers
# =============================================================================

def _read_nml_value(text: str, param: str):
    """
    Return the value string for `param` from namelist text, or None if not found.
    Matches:  param = <value>  (with optional trailing ! comment).
    Case-insensitive on the parameter name.
    """
    pattern = re.compile(
        r'^\s*' + re.escape(param) + r'\s*=\s*([^\s!]+)',
        re.IGNORECASE | re.MULTILINE
    )
    m = pattern.search(text)
    return m.group(1) if m else None


def _set_nml_value(text: str, param: str, value) -> str:
    """
    Replace the value of `param` in namelist text, preserving the rest of
    the line (including any trailing ! comment).
    If the parameter is not found the text is returned unchanged.
    """
    pattern = re.compile(
        r'(^\s*' + re.escape(param) + r'\s*=\s*)([^\s!]+)',
        re.IGNORECASE | re.MULTILINE
    )
    new_text, n = pattern.subn(lambda m: m.group(1) + str(value), text)
    if n == 0:
        print(f"  WARNING: parameter '{param}' not found in param.nml — "
              f"could not set to {value}.")
    return new_text


# =============================================================================
# Per-month processor
# =============================================================================

def _process_month(ym: str, cfg: dict, template_text: str,
                   dt: float, mdir: Path, is_first: bool) -> bool:
    """
    Write I{ID}/I{ID}_YYYYMM/param.nml for one month.

    First month: start_day, start_hour, and ihot are taken as-is from the
    template (user's cold-start values, e.g. start_day=7, start_hour=12,
    ihot=0).  Only start_year, start_month, rnday, and nhot_write are updated.

    All subsequent months: start_day=1, start_hour=0, ihot=1 are enforced
    in addition to start_year, start_month, rnday, and nhot_write.

    Returns True on success, False on failure.
    """
    year  = int(ym[:4])
    month = int(ym[4:])
    ndays = monthrange(year, month)[1]

    pid     = cfg["project_id"]
    out_dir = mdir / f"I{pid}" / f"I{pid}_{ym}"
    out_nml = out_dir / "param.nml"

    if out_nml.exists() and out_nml.stat().st_size > 0:
        print(f"  {ym}: param.nml already exists, skipping.")
        return True

    if not out_dir.exists():
        print(f"  ERROR {ym}: output directory {out_dir} does not exist. "
              f"Run --init first.")
        return False

    # Compute nhot_write = total timesteps (integer division)
    total_steps = int(ndays * 86400 // dt)
    if (ndays * 86400) % dt != 0:
        print(f"  {ym}: NOTE nhot_write = {total_steps} "
              f"(rnday*86400/dt = {ndays * 86400 / dt:.4f} — "
              f"not exact integer, using floor)")

    text = template_text
    text = _set_nml_value(text, "start_year",  year)
    text = _set_nml_value(text, "start_month", month)
    text = _set_nml_value(text, "rnday",       ndays)
    text = _set_nml_value(text, "nhot_write",  total_steps)

    if is_first:
        # Preserve start_day, start_hour, ihot from template (cold-start values)
        day_str  = _read_nml_value(text, "start_day")  or "?"
        hour_str = _read_nml_value(text, "start_hour") or "?"
        ihot_str = _read_nml_value(text, "ihot")       or "?"
        print(f"  {ym}: first month — keeping template values: "
              f"start_day={day_str}, start_hour={hour_str}, ihot={ihot_str}")
    else:
        # Enforce hot-start values for all subsequent months
        text = _set_nml_value(text, "start_day",  1)
        text = _set_nml_value(text, "start_hour", 0)
        text = _set_nml_value(text, "ihot",       1)

    out_nml.write_text(text)
    print(f"  {ym}: param.nml written  "
          f"(rnday={ndays}, nhot_write={total_steps})")
    return True


# =============================================================================
# Main entry point
# =============================================================================

def run_gen_param(cfg: dict):
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    fix  = mdir / "fix"

    template = fix / "param.nml"
    if not template.exists():
        print(f"ERROR: fix/param.nml not found.")
        print(f"  Copy your calibrated param.nml to {template} "
              f"before running gen_param.")
        sys.exit(1)

    template_text = template.read_text()

    # ------------------------------------------------------------------
    # Read dt from template
    # ------------------------------------------------------------------
    dt_str = _read_nml_value(template_text, "dt")
    if dt_str is None:
        print("ERROR: 'dt' not found in fix/param.nml. Cannot compute nhot_write.")
        sys.exit(1)
    try:
        dt = float(dt_str)
    except ValueError:
        print(f"ERROR: could not parse dt value '{dt_str}' as a number.")
        sys.exit(1)
    print(f"  Template dt = {dt} s")

    # ------------------------------------------------------------------
    # Check ihot
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Check ihot in template — for first month only, not a concern
    # but warn if it's something unexpected for subsequent months
    # ------------------------------------------------------------------
    ihot_str = _read_nml_value(template_text, "ihot")
    if ihot_str is not None and ihot_str.strip() not in ("0", "1", "2"):
        print(f"  WARNING: ihot = {ihot_str} in fix/param.nml — "
              f"unrecognised value.")

    # ------------------------------------------------------------------
    # Check nhot
    # ------------------------------------------------------------------
    nhot_str = _read_nml_value(template_text, "nhot")
    if nhot_str is not None and nhot_str.strip() != "1":
        print(f"  WARNING: nhot = {nhot_str} in fix/param.nml. "
              f"Set nhot = 1 to enable hotstart output.")

    # ------------------------------------------------------------------
    # Process months
    # ------------------------------------------------------------------
    months = list_months(cfg)
    prog   = ProgressTracker(total=len(months), label="gen_param")
    failed = []

    print(f"\n{'='*60}")
    print(f"  gen_param: {months[0]} -> {months[-1]}  ({len(months)} months)")
    print(f"  Template: fix/param.nml  |  dt = {dt} s")
    print(f"  First month ({months[0]}): start_day/start_hour/ihot kept from template")
    print(f"  All others: start_day=1, start_hour=0, ihot=1 enforced")
    print(f"{'='*60}\n")

    for i, ym in enumerate(months):
        ok = _process_month(ym, cfg, template_text, dt, mdir,
                            is_first=(i == 0))
        if not ok:
            failed.append(ym)
        prog.update(ym)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_param complete. No failures.")
    else:
        print(f"  gen_param complete with {len(failed)} failure(s):")
        for m in failed:
            print(f"    {m}")
        print("  Re-run to retry (existing files are skipped).")
    print(f"{'='*60}\n")
