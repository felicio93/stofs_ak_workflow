"""
models/schism_wwm/preprocess/gen_wwminput.py
============================================
Phase 3 (interactive) — Generate wwminput.nml for each group run
directory from:

  1. fix/wwminput.nml   — user-provided template (physics parameters,
                          numerical methods, boundary settings, etc.)
  2. I{ID}_{group_id}/param.nml — derives dt, nstep_wwm, msc2, mdc2,
                                   h0, start date/time, rnday, ihot
  3. fix/station.in     — surface stations (depth == 0.0) for WWM
                          STATION block output
  4. schism_wwm.yaml    — cutoff frequency, output style, hotfile interval

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

BEGTC / ENDTC
-------------
BEGTC = group start date 00:00:00
ENDTC = group end date 00:00:00 of the NEXT day (one day past the last
        simulation day). This gives WWM a bracket past the run end so
        the final timestep always has a valid time window.

Fields substituted in the template (all others preserved verbatim):
  &PROC   PROCNAME, DELTC, BEGTC, ENDTC, DMIN
  &GRID   MDC, MSC
  &INIT   LHOTR
  &BOUC   BEGTC, ENDTC
  &WIND   BEGTC, ENDTC
  &CURR   BEGTC, ENDTC
  &WALV   BEGTC, ENDTC
  &HISTORY BEGTC, ENDTC
  &STATION BEGTC, ENDTC, IOUTS, NOUTS, XOUTS, YOUTS, CUTOFF,
           OUTSTYLE, LSP1D, LSP2D
  &HOTFILE BEGTC, ENDTC, DELTC

Sentinel: I{ID}_{group_id}/gen_wwminput.done
"""

import re
import sys
from datetime import timedelta
from pathlib import Path

from workflow.core.config import (
    model_dir,
    list_groups,
    ProgressTracker,
    group_date_range,
    get_group_ndays,
)


# =============================================================================
# param.nml reader
# =============================================================================

def _read_param_value(text: str, param: str):
    """Return the string value of param from namelist text, or None."""
    pattern = re.compile(
        r'^\s*' + re.escape(param) + r'\s*=\s*([^\s!,\n]+)',
        re.IGNORECASE | re.MULTILINE
    )
    m = pattern.search(text)
    if m is None:
        return None
    return m.group(1).strip().strip("'")


def read_param_nml(param_path: Path) -> dict:
    """Read the subset of param.nml fields needed for wwminput.nml."""
    text = param_path.read_text(errors='ignore')

    required = {
        'dt':          float,
        'nstep_wwm':   int,
        'msc2':        int,
        'mdc2':        int,
        'h0':          float,
        'start_year':  int,
        'start_month': int,
        'start_day':   int,
        'start_hour':  float,
        'rnday':       float,
        'ihot':        int,
    }

    result  = {}
    missing = []
    for key, cast in required.items():
        val = _read_param_value(text, key)
        if val is None:
            missing.append(key)
            continue
        try:
            result[key] = cast(val)
        except ValueError:
            print(f"  ERROR: could not parse '{key}' = '{val}' "
                  f"as {cast.__name__} in {param_path}")
            sys.exit(1)

    if missing:
        print(f"  ERROR: required parameter(s) not found in "
              f"{param_path}:")
        for m in missing:
            print(f"    {m}")
        sys.exit(1)

    return result


# =============================================================================
# Time helpers
# =============================================================================

def _wwm_time(dt_obj) -> str:
    """Format a datetime as WWM time string 'YYYYMMDD.HHMMSS'."""
    return dt_obj.strftime('%Y%m%d.%H%M%S')


def _compute_times(cfg: dict, group_id: str):
    """Compute BEGTC and ENDTC for a group.

    BEGTC = group start date 00:00:00
    ENDTC = day AFTER group end date 00:00:00
            (one-day bracket past the last simulation day so WWM
             always has a valid time window at the final timestep)

    Returns (begtc_str, endtc_str) as WWM-formatted strings.
    """
    from datetime import datetime
    gstart, gend = group_date_range(cfg, group_id)

    begtc_dt = datetime(gstart.year, gstart.month, gstart.day,
                        0, 0, 0)
    # One calendar day past the last simulation day
    endtc_dt = datetime(gend.year, gend.month, gend.day,
                        0, 0, 0) + timedelta(days=1)

    return _wwm_time(begtc_dt), _wwm_time(endtc_dt)


# =============================================================================
# station.in parser (surface stations only)
# =============================================================================

def read_surface_stations(station_in_path: Path) -> list:
    """Read all surface (depth == 0.0) station lines from station.in."""
    text = station_in_path.read_text(errors='ignore').splitlines()
    if len(text) < 3:
        return []

    station_lines = text[2:]
    stations      = []
    line_index    = 0

    for raw in station_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        line_index += 1

        if '!' in stripped:
            data_part, comment = stripped.split('!', 1)
            comment = comment.strip()
        else:
            data_part = stripped
            comment   = ''

        nums = data_part.split()
        if len(nums) < 4:
            continue
        try:
            lon   = float(nums[1])
            lat   = float(nums[2])
            depth = float(nums[3])
        except ValueError:
            continue

        if abs(depth) > 1e-6:
            continue

        name = _extract_station_name(comment, line_index)
        stations.append({'name': name, 'lon': lon, 'lat': lat})

    return stations


def _extract_station_name(comment: str, line_index: int) -> str:
    if not comment:
        return f'sta_{line_index}'
    m = re.match(r'^\[.*?\]\s*,\s*([^,]+)', comment)
    if m:
        station_id = m.group(1).strip()
        if station_id:
            return station_id
    name = re.sub(r'\s+', '_', comment.strip())
    return name if name else f'sta_{line_index}'


# =============================================================================
# Fortran namelist field substitution
# =============================================================================

def _substitute_field(text: str, section: str, field: str,
                      new_value: str) -> str:
    """Substitute the value of field globally in the namelist text."""
    pattern = re.compile(
        r'(?i)(^\s*' + re.escape(field) + r'\s*=\s*)([^\n]*)',
        re.MULTILINE
    )

    def replacer(m):
        prefix    = m.group(1)
        rest      = m.group(2)
        val_match = re.match(
            r"(\s*'[^']*'|[^\s!,]*)(.*)$", rest, re.DOTALL)
        suffix = val_match.group(2) if val_match else ''
        return prefix + new_value + suffix

    new_text, count = pattern.subn(replacer, text)
    if count == 0:
        print(f"  WARNING: field '{field}' not found in "
              f"{section} section; skipping substitution.")
    return new_text


def _substitute_field_nth(text: str, field: str, new_value: str,
                           occurrence: int) -> tuple:
    """Substitute only the Nth occurrence of field = ..."""
    pattern = re.compile(
        r'(?i)(^\s*' + re.escape(field) + r'\s*=\s*)([^\n]*)',
        re.MULTILINE
    )
    matches = list(pattern.finditer(text))
    if occurrence > len(matches):
        return text, False

    m      = matches[occurrence - 1]
    prefix = m.group(1)
    rest   = m.group(2)
    val_match = re.match(
        r"(\s*'[^']*'|[^\s!,]*)(.*)$", rest, re.DOTALL)
    suffix   = val_match.group(2) if val_match else ''
    new_text = (text[:m.start()]
                + prefix + new_value + suffix
                + text[m.end():])
    return new_text, True


def _count_occurrences(text: str, field: str) -> int:
    pattern = re.compile(
        r'(?i)^\s*' + re.escape(field) + r'\s*=',
        re.MULTILINE
    )
    return len(pattern.findall(text))


# Section order for BEGTC/ENDTC occurrence-based substitution
_WWM_SECTION_ORDER = [
    'PROC', 'BOUC', 'WIND', 'CURR', 'WALV',
    'HISTORY', 'STATION', 'HOTFILE',
]
_SECTION_BEGTC_OCC = {
    s: i + 1 for i, s in enumerate(_WWM_SECTION_ORDER)
}
_SECTION_ENDTC_OCC = {
    s: i + 1 for i, s in enumerate(_WWM_SECTION_ORDER)
}


def _substitute_timed_field(text: str, section: str,
                             field: str, new_value: str) -> str:
    occ_map = (_SECTION_BEGTC_OCC if field.upper() == 'BEGTC'
               else _SECTION_ENDTC_OCC)
    occ     = occ_map.get(section.upper())
    if occ is None:
        return _substitute_field(text, section, field, new_value)
    total = _count_occurrences(text, field)
    if occ > total:
        return text
    new_text, ok = _substitute_field_nth(
        text, field, new_value, occ)
    if not ok:
        print(f"  WARNING: could not substitute {field} in "
              f"&{section}")
    return new_text


# =============================================================================
# STATION block rebuilder
# =============================================================================

def _build_station_block(stations: list, begtc: str,
                         endtc: str, outstyle: str,
                         lsp1d: bool, lsp2d: bool,
                         cutoff_freq: float) -> str:
    n = len(stations)

    def _fmt_array(values, quote=False, width=4):
        lines = []
        for i in range(0, len(values), width):
            chunk = values[i:i + width]
            if quote:
                formatted = ', '.join(f"'{v}'" for v in chunk)
            else:
                formatted = ', '.join(str(v) for v in chunk)
            if i + width < len(values):
                formatted += ','
            lines.append('        ' + formatted)
        return '\n'.join(lines)

    names      = [s['name'] for s in stations]
    lons       = [f"{s['lon']:.4f}" for s in stations]
    lats       = [f"{s['lat']:.4f}" for s in stations]
    cutoff_str = f"{n}*{cutoff_freq}"
    lsp1d_str  = '.true.'  if lsp1d else '.false.'
    lsp2d_str  = '.true.'  if lsp2d else '.false.'

    block = f"""\
&STATION
    BEGTC = '{begtc}'
    DELTC = 1800.0
    UNITC = 'SEC'
    ENDTC = '{endtc}'
    DEFINETC = 86400
    OUTSTYLE = '{outstyle}'
    MULTIPLEOUT = 0
    USE_SINGLE_OUT = .true.
    PARAMWRITE = .true.
    FILEOUT = 'wwm_sta.dat'
    LOUTITER = .false.
    IOUTS = {n}
    NOUTS =
{_fmt_array(names, quote=True)},
    XOUTS =
{_fmt_array(lons)},
    YOUTS =
{_fmt_array(lats)},
    CUTOFF = {cutoff_str}
    LSP1D = {lsp1d_str}
    LSP2D = {lsp2d_str}
    LSIGMAX = .true.
    HS = .true.
    TM01 = .true.
    TM02 = .false.
    KLM = .false.
    WLM = .false.
    ETOTC = .false.
    ETOTS = .false.
    DM = .true.
    DSPR = .false.
    TPPD = .false.
    TPP = .false.
    CPP = .false.
    WNPP = .false.
    CGPP = .false.
    KPP = .false.
    LPP = .false.
    PEAKD = .false.
    PEAKDSPR = .false.
    DPEAK = .false.
    UBOT = .false.
    ORBITAL = .false.
    BOTEXPER = .false.
    TMBOT = .false.
    URSELL = .false.
    UFRIC = .false.
    Z0 = .false.
    ALPHA_CH = .false.
    WINDX = .false.
    WINDY = .false.
    CD = .false.
    CURRTX = .false.
    CURRTY = .false.
    WATLEV = .false.
    WATLEVOLD = .false.
    DEPDT = .false.
    DEP = .false.
    TAUW = .false.
    TAUHF = .false.
    TAUTOT = .false.
    STOKESSURFX = .false.
    STOKESSURFY = .false.
    STOKESBAROX = .false.
    STOKESBAROY = .false.
    RSXX = .false.
    RSXY = .false.
    RSYY = .false.
    CFL1 = .false.
    CFL2 = .false.
    CFL3 = .false.
/
"""
    return block


# =============================================================================
# Main substitution driver
# =============================================================================

def _apply_substitutions(text: str, p: dict,
                         begtc_str: str, endtc_str: str,
                         stations: list, cfg: dict,
                         group_id: str,
                         is_first_group: bool) -> str:
    pid = cfg["project_id"]

    # Step 1: Replace &STATION block first (removes its DELTC
    # from the occurrence count before we touch PROC/HOTFILE DELTC)
    outstyle    = str(cfg.get('wwm_outstyle_station', 'NC'))
    lsp1d       = bool(cfg.get('wwm_lsp1d', True))
    lsp2d       = bool(cfg.get('wwm_lsp2d', True))
    cutoff_freq = float(cfg.get('wwm_cutoff_freq', 0.35))

    station_block = _build_station_block(
        stations, begtc_str, endtc_str,
        outstyle, lsp1d, lsp2d, cutoff_freq)

    station_pattern = re.compile(
        r'&STATION\b.*?^/',
        re.IGNORECASE | re.DOTALL | re.MULTILINE)
    new_text, n_replaced = station_pattern.subn(
        station_block.rstrip('\n'), text)
    if n_replaced == 0:
        print("  WARNING: &STATION block not found in template; "
              "appending station block at end.")
        text = text.rstrip('\n') + '\n\n' + station_block
    else:
        text = new_text

    # Step 2: &PROC — PROCNAME, DELTC (occ 1), DMIN, BEGTC, ENDTC
    procname  = cfg.get('wwm_procname') or f"R{pid}_{group_id}"
    deltc_wwm = p['dt'] * p['nstep_wwm']

    text = _substitute_field(text, 'PROC', 'PROCNAME',
                             f"'{procname}'")
    text, _ = _substitute_field_nth(
        text, 'DELTC', f"{deltc_wwm:.1f}", 1)
    text = _substitute_field(text, 'PROC', 'DMIN',
                             f"{p['h0']:.4f}")
    text = _substitute_timed_field(
        text, 'PROC', 'BEGTC', f"'{begtc_str}'")
    text = _substitute_timed_field(
        text, 'PROC', 'ENDTC', f"'{endtc_str}'")

    # Step 3: &GRID
    text = _substitute_field(text, 'GRID', 'MDC', str(p['mdc2']))
    text = _substitute_field(text, 'GRID', 'MSC', str(p['msc2']))

    # Step 4: &INIT — LHOTR (hotstart flag)
    # False for the very first group (cold start),
    # True for all subsequent groups
    lhotr = ('.true.'
             if (not is_first_group and p['ihot'] > 0)
             else '.false.')
    text = _substitute_field(text, 'INIT', 'LHOTR', lhotr)

    # Step 5: &BOUC, &WIND, &CURR, &WALV, &HISTORY
    for section in ('BOUC', 'WIND', 'CURR', 'WALV'):
        text = _substitute_timed_field(
            text, section, 'BEGTC', f"'{begtc_str}'")
        text = _substitute_timed_field(
            text, section, 'ENDTC', f"'{endtc_str}'")

    text = _substitute_timed_field(
        text, 'HISTORY', 'BEGTC', f"'{begtc_str}'")
    text = _substitute_timed_field(
        text, 'HISTORY', 'ENDTC', f"'{endtc_str}'")

    # Step 6: &HOTFILE times + DELTC (last occurrence)
    deltc_hot = float(cfg.get('wwm_deltc_hotfile', 86400.0))
    text = _substitute_timed_field(
        text, 'HOTFILE', 'BEGTC', f"'{begtc_str}'")
    text = _substitute_timed_field(
        text, 'HOTFILE', 'ENDTC', f"'{endtc_str}'")

    total_deltc = _count_occurrences(text, 'DELTC')
    if total_deltc >= 1:
        new_text, ok = _substitute_field_nth(
            text, 'DELTC', f"{deltc_hot:.1f}", total_deltc)
        if ok:
            text = new_text
        else:
            print("  WARNING: could not substitute HOTFILE DELTC")

    return text


# =============================================================================
# Per-group entry point
# =============================================================================

def gen_wwminput_group(cfg: dict, group_id: str,
                       is_first_group: bool) -> bool:
    """Generate wwminput.nml for one group.

    Parameters
    ----------
    cfg            : merged workflow config dict
    group_id       : group identifier (YYYYMM or YYYYMMDD)
    is_first_group : True if this is the first group in the simulation

    Returns True on success, False on failure.
    """
    pid  = cfg['project_id']
    mdir = model_dir(cfg)
    fix  = mdir / 'fix'
    idir = mdir / f'I{pid}' / f'I{pid}_{group_id}'

    out_path = idir / 'wwminput.nml'
    sentinel = idir / 'gen_wwminput.done'

    if sentinel.exists() and out_path.exists():
        print(f"  {group_id}: wwminput.nml already exists, "
              f"skipping.")
        return True

    # --- Validate required inputs ---
    template_path   = fix / 'wwminput.nml'
    param_path      = idir / 'param.nml'
    station_in_path = fix / 'station.in'

    missing = []
    for p, label in [
        (template_path,   'fix/wwminput.nml'),
        (param_path,      f'I{pid}_{group_id}/param.nml'),
        (station_in_path, 'fix/station.in'),
    ]:
        if not p.exists():
            missing.append(label)
    if missing:
        print(f"  ERROR {group_id}: required file(s) not found:")
        for m in missing:
            print(f"    {m}")
        return False

    # --- Read inputs ---
    print(f"\n--- gen_wwminput {group_id} ---")

    try:
        p = read_param_nml(param_path)
    except SystemExit:
        return False

    begtc_str, endtc_str = _compute_times(cfg, group_id)
    gstart, gend         = group_date_range(cfg, group_id)
    ndays                = get_group_ndays(cfg, group_id)

    stations = read_surface_stations(station_in_path)
    if not stations:
        print(f"  WARNING {group_id}: no surface stations found "
              f"in station.in; STATION block will be empty.")

    print(f"  BEGTC = {begtc_str}  ENDTC = {endtc_str}")
    print(f"  dt={p['dt']}s  nstep_wwm={p['nstep_wwm']}  "
          f"-> WWM DELTC={p['dt'] * p['nstep_wwm']:.1f}s")
    print(f"  msc2={p['msc2']}  mdc2={p['mdc2']}  "
          f"h0={p['h0']}")
    lhotr_str = ('.true.'
                 if (not is_first_group and p['ihot'] > 0)
                 else '.false.')
    print(f"  ihot={p['ihot']}  LHOTR='{lhotr_str}'")
    print(f"  Surface stations: {len(stations)}")
    print(f"  Group: {gstart} -> {gend}  ({ndays} days)")

    # --- Template substitution ---
    text = template_path.read_text(errors='ignore')
    text = _apply_substitutions(
        text, p, begtc_str, endtc_str,
        stations, cfg, group_id, is_first_group)

    # --- Write output ---
    out_path.write_text(text)
    sentinel.touch()
    print(f"  Written: {out_path}")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point (all groups)
# =============================================================================

def run_gen_wwminput(cfg: dict):
    """Generate wwminput.nml for every group in the project date range."""
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    prog     = ProgressTracker(total=len(groups),
                               label='gen_wwminput')
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_wwminput: {groups[0]} -> {groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"  Template: fix/wwminput.nml")
    print(f"  ENDTC = group end + 1 day (WWM read-ahead bracket)")
    print(f"{'='*60}\n")

    for i, group_id in enumerate(groups):
        ok = gen_wwminput_group(cfg, group_id,
                                is_first_group=(i == 0))
        if not ok:
            failed.append(group_id)
        prog.update(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_wwminput complete. No failures.")
    else:
        print(f"  gen_wwminput complete with {len(failed)} "
              f"failure(s):")
        for g in failed:
            print(f"    {g}")
        print("  Re-run to retry (existing sentinels are skipped).")
    print(f"{'='*60}\n")
