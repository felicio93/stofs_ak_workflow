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
BEGTC = SCHISM start time of the group, from param.nml
        (start_year/month/day/hour)
ENDTC = BEGTC + rnday days (the end of the SCHISM run, from param.nml)

For the usual case (a group starting at 00:00 on its first day and
running for the whole group) this equals group start 00:00 and the
00:00 of the day after the last simulation day. Deriving the window from
param.nml keeps WWM and SCHISM on the same time window even when the
first group uses a non-zero start_day/start_hour from the template.

Fields substituted in the template. The &STATION block is REBUILT from
the station list and fixed output settings (it is not preserved verbatim);
other namelists keep their template values for fields not listed here:
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


def _compute_times(p: dict):
    """Compute BEGTC and ENDTC from the group's param.nml values.

    BEGTC = start_year/month/day/hour  (SCHISM start of the group)
    ENDTC = BEGTC + rnday days         (SCHISM end of the group)

    `p` is the dict returned by read_param_nml().
    Returns (begtc_str, endtc_str) as WWM-formatted strings.
    """
    from datetime import datetime

    begtc_dt = (datetime(p['start_year'], p['start_month'],
                         p['start_day'], 0, 0, 0)
                + timedelta(hours=float(p['start_hour'])))
    endtc_dt = begtc_dt + timedelta(days=float(p['rnday']))

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


def _count_occurrences(text: str, field: str) -> int:
    pattern = re.compile(
        r'(?i)^\s*' + re.escape(field) + r'\s*=',
        re.MULTILINE
    )
    return len(pattern.findall(text))


def _section_span(text: str, section: str):
    """(start, end) of the &SECTION ... / namelist block, or None."""
    m = re.search(r'(?ims)^\s*&' + re.escape(section) + r'\b.*?^\s*/\s*$',
                  text)
    return (m.start(), m.end()) if m else None


def _substitute_timed_field(text: str, section: str, field: str,
                            new_value: str, required: bool = True) -> str:
    """Set `field` inside the &SECTION block only.

    The same field names (BEGTC, ENDTC, DELTC) appear in several sections,
    so the block is located by name. With required=False a section that is
    absent or does not set the field is left alone: in the coupled setup
    &WIND, &CURR and &WALV are empty because SCHISM passes those fields.
    """
    span = _section_span(text, section)
    if span is None or _count_occurrences(text[span[0]:span[1]], field) == 0:
        if required:
            raise ValueError(f"{field} not found in &{section} of "
                             f"fix/wwminput.nml")
        return text
    a, b = span
    return text[:a] + _substitute_field(text[a:b], section, field,
                                        new_value) + text[b:]


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
    text = _substitute_timed_field(
        text, 'PROC', 'DELTC', f"{deltc_wwm:.1f}")
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

    # Step 5: &BOUC, &WIND, &CURR, &WALV (each only if it sets a time
    # window; empty in the coupled setup), &HISTORY
    for section in ('BOUC', 'WIND', 'CURR', 'WALV'):
        text = _substitute_timed_field(
            text, section, 'BEGTC', f"'{begtc_str}'", required=False)
        text = _substitute_timed_field(
            text, section, 'ENDTC', f"'{endtc_str}'", required=False)

    text = _substitute_timed_field(
        text, 'HISTORY', 'BEGTC', f"'{begtc_str}'")
    text = _substitute_timed_field(
        text, 'HISTORY', 'ENDTC', f"'{endtc_str}'")

    # Step 6: &HOTFILE times + DELTC
    deltc_hot = float(cfg.get('wwm_deltc_hotfile', 86400.0))
    text = _substitute_timed_field(
        text, 'HOTFILE', 'BEGTC', f"'{begtc_str}'")
    text = _substitute_timed_field(
        text, 'HOTFILE', 'ENDTC', f"'{endtc_str}'")
    text = _substitute_timed_field(
        text, 'HOTFILE', 'DELTC', f"{deltc_hot:.1f}")

    return text


# =============================================================================
# Hotfile record of the previous group
# =============================================================================

def hotfile_latest_record(prev_ndays: int, deltc_hot: float,
                          lcyclehot: bool) -> int:
    """Record of the previous group's hotfile that holds its end state.

    WWM writes the hotfile at its first step (HOTF%TMJD starts at BEGTC and
    MAIN%TMJD = BEGTC + K*DELT with K >= 1) and then every DELTC up to and
    including ENDTC (wwm_output.F90), so a group of L days writes
    1 + L*86400/DELTC records.  With LCYCLEHOT=T the writes alternate
    between records 1 and 2 (POS = mod(IDXHOTOUT,2)+1, wwm_hotfile.F90):
    a 7-day group with daily hotfiles ends in record 2, a 2-day one in 1.
    """
    span = prev_ndays * 86400.0
    if deltc_hot <= 0 or span % deltc_hot != 0:
        raise ValueError(
            f"wwm_deltc_hotfile={deltc_hot:g}s does not divide the previous "
            f"group ({prev_ndays} days): its last hotfile would not be at "
            f"the end of the group")
    nwrite = 1 + int(span // deltc_hot)
    return (nwrite - 1) % 2 + 1 if lcyclehot else nwrite


def _read_lcyclehot(text: str) -> bool:
    m = re.search(r'(?im)^\s*LCYCLEHOT\s*=\s*\.?([TF])', text)
    if not m:
        raise ValueError("LCYCLEHOT is not set in wwminput.nml &HOTFILE; "
                         "set it explicitly (T recommended)")
    return m.group(1).upper() == 'T'


# =============================================================================
# Per-group entry point
# =============================================================================

def gen_wwminput_group(cfg: dict, group_id: str,
                       is_first_group: bool,
                       prev_ndays: int = None) -> bool:
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

    begtc_str, endtc_str = _compute_times(p)
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
    try:
        text = _apply_substitutions(
            text, p, begtc_str, endtc_str,
            stations, cfg, group_id, is_first_group)
        if not is_first_group and prev_ndays:
            pos = hotfile_latest_record(
                prev_ndays, float(cfg.get('wwm_deltc_hotfile', 86400.0)),
                _read_lcyclehot(text))
            text = _substitute_field(text, 'HOTFILE', 'IHOTPOS_IN', str(pos))
            print(f"  IHOTPOS_IN = {pos}  (end state of the previous "
                  f"{prev_ndays}-day group)")
    except ValueError as exc:
        print(f"  ERROR {group_id}: {exc}")
        return False

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
    prog     = ProgressTracker(total=len(groups))
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_wwminput: {groups[0]} -> {groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"  Template: fix/wwminput.nml")
    print(f"  BEGTC/ENDTC from each group's param.nml "
          f"(start time, start + rnday)")
    print(f"{'='*60}\n")

    for i, group_id in enumerate(groups):
        prev = get_group_ndays(cfg, groups[i - 1]) if i > 0 else None
        ok = gen_wwminput_group(cfg, group_id,
                                is_first_group=(i == 0),
                                prev_ndays=prev)
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
