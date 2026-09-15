"""
models/schism_wwm/preprocess/gen_wwminput.py
============================================
Phase 3 (interactive) — Generate wwminput.nml for each monthly run
directory from:

  1. fix/wwminput.nml   — user-provided template (physics parameters,
                          numerical methods, boundary settings, etc.)
  2. I{ID}_YYYYMM/param.nml — derives dt, nstep_wwm, msc2, mdc2, h0,
                              start date/time, rnday, ihot
  3. fix/station.in     — surface stations (depth == 0.0) for WWM
                          STATION block output
  4. schism_wwm.yaml    — cutoff frequency, output style, hotfile interval

Fields substituted in the template (all others are preserved verbatim,
including inline Fortran comments):

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

The function writes I{ID}/I{ID}_YYYYMM/wwminput.nml and touches
a sentinel I{ID}/I{ID}_YYYYMM/gen_wwminput.done on success.

Resume-safe: months with an existing sentinel are skipped.
"""

import re
import sys
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path

from workflow.core.config import model_dir, list_months, ProgressTracker


# =============================================================================
# param.nml reader
# =============================================================================

def _read_param_value(text: str, param: str):
    """
    Return the string value of `param` from namelist text, or None.

    Handles:
      - optional whitespace around '='
      - inline Fortran comments after '!' (with or without space)
      - single quotes around string values
      - the param name is matched case-insensitively
    """
    pattern = re.compile(
        r'^\s*' + re.escape(param) + r'\s*=\s*([^\s!,\n]+)',
        re.IGNORECASE | re.MULTILINE
    )
    m = pattern.search(text)
    if m is None:
        return None
    # Strip surrounding single quotes if present
    val = m.group(1).strip().strip("'")
    return val


def read_param_nml(param_path: Path) -> dict:
    """
    Read the subset of param.nml fields needed for wwminput.nml generation.

    Returns a dict with keys:
        dt            float   time step [s]
        nstep_wwm     int     WWM coupling interval [steps]
        msc2          int     # frequency bins
        mdc2          int     # directional bins
        h0            float   min water depth [m]
        start_year    int
        start_month   int
        start_day     int
        start_hour    float
        rnday         float   run length [days]
        ihot          int     hotstart flag (0=cold, 1/2=hot)

    Exits with an error if any required field is missing.
    """
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

    result = {}
    missing = []
    for key, cast in required.items():
        val = _read_param_value(text, key)
        if val is None:
            missing.append(key)
            continue
        try:
            result[key] = cast(val)
        except ValueError:
            print(f"  ERROR: could not parse '{key}' = '{val}' as {cast.__name__} "
                  f"in {param_path}")
            sys.exit(1)

    if missing:
        print(f"  ERROR: required parameter(s) not found in {param_path}:")
        for m in missing:
            print(f"    {m}")
        sys.exit(1)

    return result


# =============================================================================
# Time helpers
# =============================================================================

def _wwm_time(dt_obj: datetime) -> str:
    """Format a datetime as WWM time string 'YYYYMMDD.HHMMSS'."""
    return dt_obj.strftime('%Y%m%d.%H%M%S')


def _compute_times(p: dict):
    """
    Compute BEGTC and ENDTC datetime objects from param.nml values.

    Returns (begtc_dt, endtc_dt).
    start_hour may be fractional (e.g. 12.5 = 12h 30m).
    """
    hour_int = int(p['start_hour'])
    minute   = int(round((p['start_hour'] - hour_int) * 60))
    begtc = datetime(
        p['start_year'], p['start_month'], p['start_day'],
        hour_int, minute, 0
    )
    endtc = begtc + timedelta(days=float(p['rnday']))
    return begtc, endtc


# =============================================================================
# station.in parser (surface stations only, all lines)
# =============================================================================

def read_surface_stations(station_in_path: Path) -> list:
    """
    Read all surface (depth == 0.0) station lines from station.in.

    Returns a list of dicts:
        name  : str   station name/ID for WWM NOUTS
        lon   : float longitude
        lat   : float latitude

    A line is included if its depth field == 0.0.

    Name extraction rules (in priority order):
      1. Structured comment  ![...],<id>,<source>,<name>  → use <id>
      2. Any comment after ! → strip '!' and whitespace, use as name
      3. No comment          → use 'sta_{line_index}'
    """
    text = station_in_path.read_text(errors='ignore').splitlines()

    if len(text) < 3:
        return []

    # Lines 0 and 1 are the flag line and nsta line; stations start at line 2.
    station_lines = text[2:]

    stations = []
    line_index = 0
    for raw in station_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        line_index += 1

        # Split into data part and optional comment
        if '!' in stripped:
            data_part, comment = stripped.split('!', 1)
            comment = comment.strip()
        else:
            data_part = stripped
            comment = ''

        # Parse numeric fields: idx lon lat depth
        nums = data_part.split()
        if len(nums) < 4:
            continue
        try:
            lon   = float(nums[1])
            lat   = float(nums[2])
            depth = float(nums[3])
        except ValueError:
            continue

        # Only keep surface stations
        if abs(depth) > 1e-6:
            continue

        # Determine station name
        name = _extract_station_name(comment, line_index)

        stations.append({'name': name, 'lon': lon, 'lat': lat})

    return stations


def _extract_station_name(comment: str, line_index: int) -> str:
    """
    Extract the best station name from a station.in comment string
    (the part after '!', already stripped).

    Priority:
      1. Structured format  [VARS],<id>,<source>,<name>  → <id>
      2. Any non-empty comment text → use as-is (e.g. 'modulation1')
      3. Fallback → 'sta_{line_index}'
    """
    if not comment:
        return f'sta_{line_index}'

    # Try structured format: [VARS],id,source,name
    m = re.match(r'^\[.*?\]\s*,\s*([^,]+)', comment)
    if m:
        station_id = m.group(1).strip()
        if station_id:
            return station_id

    # Use the raw comment text, cleaned up
    # Remove Fortran-style inline continuations and extra whitespace
    name = comment.strip()
    # Replace spaces with underscores to be safe in Fortran namelists
    name = re.sub(r'\s+', '_', name)
    if name:
        return name

    return f'sta_{line_index}'


# =============================================================================
# Fortran namelist field substitution
# =============================================================================

def _substitute_field(text: str, section: str, field: str,
                       new_value: str) -> str:
    """
    Substitute the value of `field` inside `section` in a Fortran namelist.

    Matches lines of the form (case-insensitive):
        FIELD = <value>               (no inline comment)
        FIELD = <value>!comment       (inline comment, no space)
        FIELD = <value> !comment      (inline comment with space)

    The replacement preserves everything after the value (inline comments,
    continuation characters, etc.).

    If the field is not found in the text, a warning is printed and the
    text is returned unchanged.

    `section` is used only for the warning message; matching is done on
    the field name globally within the text (namelist sections are not
    parsed structurally). This is safe because WWM field names are unique
    across sections for the fields we substitute — and for fields that
    appear in multiple sections (BEGTC, ENDTC, DELTC) we use
    _substitute_field_in_section() instead.
    """
    pattern = re.compile(
        r'(?i)(^\s*' + re.escape(field) + r'\s*=\s*)([^\n]*)',
        re.MULTILINE
    )

    def replacer(m):
        prefix   = m.group(1)   # "    FIELD = "
        rest     = m.group(2)   # "<old_value>  !comment" or "<old_value>!comment"

        # Separate the old value from any trailing comment / whitespace.
        # Value ends at the first '!' or end of line.
        # We also need to handle quoted strings: e.g. '20191019.120000'!
        val_match = re.match(
            r"(\s*'[^']*'|[^\s!,]*)(.*)$", rest, re.DOTALL
        )
        if val_match:
            suffix = val_match.group(2)   # everything after old value
        else:
            suffix = ''

        return prefix + new_value + suffix

    new_text, count = pattern.subn(replacer, text)
    if count == 0:
        print(f"  WARNING: field '{field}' not found in {section} section; "
              f"skipping substitution.")
    return new_text


def _substitute_field_nth(text: str, field: str, new_value: str,
                           occurrence: int) -> str:
    """
    Substitute only the Nth occurrence (1-based) of `field = ...` in text.

    Used for fields that appear in multiple namelist sections
    (BEGTC, ENDTC, DELTC), where we need to update each section
    independently. Returns (new_text, replaced: bool).
    """
    pattern = re.compile(
        r'(?i)(^\s*' + re.escape(field) + r'\s*=\s*)([^\n]*)',
        re.MULTILINE
    )
    matches = list(pattern.finditer(text))
    if occurrence > len(matches):
        return text, False

    m = matches[occurrence - 1]
    prefix = m.group(1)
    rest   = m.group(2)

    val_match = re.match(
        r"(\s*'[^']*'|[^\s!,]*)(.*)$", rest, re.DOTALL
    )
    suffix = val_match.group(2) if val_match else ''

    new_text = text[:m.start()] + prefix + new_value + suffix + text[m.end():]
    return new_text, True


def _count_occurrences(text: str, field: str) -> int:
    """Count how many times `field = ...` appears in text."""
    pattern = re.compile(
        r'(?i)^\s*' + re.escape(field) + r'\s*=',
        re.MULTILINE
    )
    return len(pattern.findall(text))


# =============================================================================
# Section-order map for BEGTC / ENDTC / DELTC substitution
# =============================================================================

# WWM namelist sections in document order, and which ones have BEGTC/ENDTC.
# We use occurrence index (1-based) to substitute the right section.
# Sections are listed in the order they normally appear in wwminput.nml.
# Fields that appear in multiple sections need occurrence-based substitution.

# Standard section order in wwminput.nml (based on the sample files provided).
# Each entry is (section_name, has_BEGTC, has_ENDTC, has_DELTC_hotfile)
_WWM_SECTION_ORDER = [
    'PROC',     # 1st BEGTC/ENDTC
    'BOUC',     # 2nd BEGTC/ENDTC
    'WIND',     # 3rd BEGTC/ENDTC
    'CURR',     # 4th BEGTC/ENDTC
    'WALV',     # 5th BEGTC/ENDTC
    'HISTORY',  # 6th BEGTC/ENDTC
    'STATION',  # 7th BEGTC/ENDTC
    'HOTFILE',  # 8th BEGTC/ENDTC
]

# Map section name → occurrence index for BEGTC and ENDTC
_SECTION_BEGTC_OCC = {s: i + 1 for i, s in enumerate(_WWM_SECTION_ORDER)}
_SECTION_ENDTC_OCC = {s: i + 1 for i, s in enumerate(_WWM_SECTION_ORDER)}


def _substitute_timed_field(text: str, section: str, field: str,
                              new_value: str) -> str:
    """
    Substitute a time field (BEGTC, ENDTC) in a specific section by
    occurrence index. Returns updated text.
    """
    occ_map = (_SECTION_BEGTC_OCC if field.upper() == 'BEGTC'
               else _SECTION_ENDTC_OCC)
    occ = occ_map.get(section.upper())
    if occ is None:
        # Fallback: substitute all occurrences of this field
        return _substitute_field(text, section, field, new_value)

    total = _count_occurrences(text, field)
    if occ > total:
        # Section may not have this field in this template — skip silently
        return text

    new_text, ok = _substitute_field_nth(text, field, new_value, occ)
    if not ok:
        print(f"  WARNING: could not substitute {field} in &{section}")
    return new_text


# =============================================================================
# STATION block rebuilder
# =============================================================================

def _build_station_block(stations: list, begtc: str, endtc: str,
                          outstyle: str, lsp1d: bool, lsp2d: bool,
                          cutoff_freq: float) -> str:
    """
    Build the complete &STATION ... / block as a string.

    Uses the same structure as the sample wwminput.nml so the output is
    familiar to the user. Only the time-dependent and station-dependent
    fields are set here; physics flags (HS, TM01, etc.) are preserved
    from the template via the overall substitution logic.
    """
    n = len(stations)

    # Format station arrays with 4 per line for readability
    def _fmt_array(values, quote=False, width=4):
        lines = []
        for i in range(0, len(values), width):
            chunk = values[i:i + width]
            if quote:
                formatted = ', '.join(f"'{v}'" for v in chunk)
            else:
                formatted = ', '.join(str(v) for v in chunk)
            # Add trailing comma except for last chunk
            if i + width < len(values):
                formatted += ','
            lines.append('        ' + formatted)
        return '\n'.join(lines)

    names = [s['name'] for s in stations]
    lons  = [f"{s['lon']:.4f}" for s in stations]
    lats  = [f"{s['lat']:.4f}" for s in stations]
    cutoff_str = f"{n}*{cutoff_freq}"

    lsp1d_str = '.true.' if lsp1d else '.false.'
    lsp2d_str = '.true.' if lsp2d else '.false.'

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
# Main section substitution driver
# =============================================================================
def _apply_substitutions(text: str, p: dict, begtc_str: str, endtc_str: str,
                          stations: list, cfg: dict, ym: str,
                          is_first_month: bool) -> str:
    """
    Apply all dynamic substitutions to the wwminput.nml template text.
    Returns the modified text.

    Order matters for DELTC substitution:
      1. Replace &STATION block first (removes STATION's DELTC from count)
      2. Then substitute PROC DELTC (occurrence 1)
      3. Then substitute HOTFILE DELTC (now the last occurrence)
    """
    pid = cfg['project_id']

    # ----------------------------------------------------------------
    # Step 1: Replace &STATION block FIRST so its DELTC (occurrence 4)
    # is removed before we do occurrence-based DELTC substitutions.
    # ----------------------------------------------------------------
    outstyle    = str(cfg.get('wwm_outstyle_station', 'NC'))
    lsp1d       = bool(cfg.get('wwm_lsp1d', True))
    lsp2d       = bool(cfg.get('wwm_lsp2d', True))
    cutoff_freq = float(cfg.get('wwm_cutoff_freq', 0.35))

    station_block = _build_station_block(
        stations, begtc_str, endtc_str,
        outstyle, lsp1d, lsp2d, cutoff_freq
    )
    station_pattern = re.compile(
        r'&STATION\b.*?^/',
        re.IGNORECASE | re.DOTALL | re.MULTILINE
    )
    new_text, n_replaced = station_pattern.subn(
        station_block.rstrip('\n'), text)
    if n_replaced == 0:
        print("  WARNING: &STATION block not found in template; "
              "appending station block at end.")
        text = text.rstrip('\n') + '\n\n' + station_block
    else:
        text = new_text

    # After &STATION replacement, DELTC occurrences are:
    #   1 = &PROC    (WWM time step)
    #   2 = &BOUC    (boundary file time step — leave as template)
    #   3 = &HISTORY (history output time step — leave as template)
    #   4 = &HOTFILE (hotfile output interval)
    # &STATION DELTC is now in the rebuilt block (hardcoded 1800.0) and
    # won't interfere because _build_station_block uses a fixed value.
    # However the rebuilt block IS part of `text` now, so we need to
    # count carefully. The station block uses '    DELTC = 1800.0'
    # (4-space indent) while template fields use ' DELTC' (1-space).
    # We'll use _substitute_field_nth which matches any indent.

    # ----------------------------------------------------------------
    # Step 2: &PROC — substitute PROCNAME, PROC DELTC, DMIN, times
    # ----------------------------------------------------------------
    procname  = cfg.get('wwm_procname') or f"R{pid}_{ym}"
    deltc_wwm = p['dt'] * p['nstep_wwm']

    text = _substitute_field(text, 'PROC', 'PROCNAME', f"'{procname}'")

    # PROC DELTC = occurrence 1
    text, _ = _substitute_field_nth(text, 'DELTC', f"{deltc_wwm:.1f}", 1)

    text = _substitute_field(text, 'PROC', 'DMIN',  f"{p['h0']:.4f}")
    text = _substitute_timed_field(text, 'PROC', 'BEGTC', f"'{begtc_str}'")
    text = _substitute_timed_field(text, 'PROC', 'ENDTC', f"'{endtc_str}'")

    # ----------------------------------------------------------------
    # Step 3: &GRID
    # ----------------------------------------------------------------
    text = _substitute_field(text, 'GRID', 'MDC', str(p['mdc2']))
    text = _substitute_field(text, 'GRID', 'MSC', str(p['msc2']))

    # ----------------------------------------------------------------
    # Step 4: &INIT
    # ----------------------------------------------------------------
    lhotr = '.true.' if (not is_first_month and p['ihot'] > 0) else '.false.'
    text = _substitute_field(text, 'INIT', 'LHOTR', lhotr)

    # ----------------------------------------------------------------
    # Step 5: Time fields in &BOUC, &WIND, &CURR, &WALV, &HISTORY
    # ----------------------------------------------------------------
    for section in ('BOUC', 'WIND', 'CURR', 'WALV'):
        text = _substitute_timed_field(text, section, 'BEGTC', f"'{begtc_str}'")
        text = _substitute_timed_field(text, section, 'ENDTC', f"'{endtc_str}'")

    text = _substitute_timed_field(text, 'HISTORY', 'BEGTC', f"'{begtc_str}'")
    text = _substitute_timed_field(text, 'HISTORY', 'ENDTC', f"'{endtc_str}'")

    # ----------------------------------------------------------------
    # Step 6: &HOTFILE times + DELTC
    # HOTFILE DELTC is the last occurrence after &STATION replacement.
    # ----------------------------------------------------------------
    deltc_hot = float(cfg.get('wwm_deltc_hotfile', 86400.0))
    text = _substitute_timed_field(text, 'HOTFILE', 'BEGTC', f"'{begtc_str}'")
    text = _substitute_timed_field(text, 'HOTFILE', 'ENDTC', f"'{endtc_str}'")

    # Count occurrences after &STATION replacement + PROC DELTC substitution.
    # Remaining: BOUC(1), HISTORY(1), STATION-rebuilt(1), HOTFILE(1) = 4 total
    # but PROC was already done so it's now at occurrence 1 with new value.
    # The last occurrence is HOTFILE.
    total_deltc = _count_occurrences(text, 'DELTC')
    if total_deltc >= 1:
        new_text, ok = _substitute_field_nth(
            text, 'DELTC', f"{deltc_hot:.1f}", total_deltc)
        if ok:
            text = new_text
        else:
            print(f"  WARNING: could not substitute HOTFILE DELTC")

    return text

# =============================================================================
# Per-month entry point
# =============================================================================

def gen_wwminput_month(cfg: dict, ym: str, is_first_month: bool) -> bool:
    """
    Generate wwminput.nml for one calendar month.

    Parameters
    ----------
    cfg           : merged workflow config dict
    ym            : month string 'YYYYMM'
    is_first_month: True if this is the first month in the simulation
                    (controls LHOTR cold/hot start)

    Returns True on success, False on failure.
    """
    pid  = cfg['project_id']
    mdir = model_dir(cfg)
    fix  = mdir / 'fix'
    idir = mdir / f'I{pid}' / f'I{pid}_{ym}'

    out_path = idir / 'wwminput.nml'
    sentinel = idir / 'gen_wwminput.done'

    if sentinel.exists() and out_path.exists():
        print(f"  {ym}: wwminput.nml already exists, skipping.")
        return True

    # --- Validate required inputs ---
    template_path  = fix / 'wwminput.nml'
    param_path     = idir / 'param.nml'
    station_in_path = fix / 'station.in'

    missing = []
    for p, label in [(template_path,   'fix/wwminput.nml'),
                     (param_path,       f'I{pid}_{ym}/param.nml'),
                     (station_in_path,  'fix/station.in')]:
        if not p.exists():
            missing.append(label)
    if missing:
        print(f"  ERROR {ym}: required file(s) not found:")
        for m in missing:
            print(f"    {m}")
        return False

    # --- Read inputs ---
    print(f"\n--- gen_wwminput {ym} ---")

    try:
        p = read_param_nml(param_path)
    except SystemExit:
        return False

    begtc_dt, endtc_dt = _compute_times(p)
    begtc_str = _wwm_time(begtc_dt)
    endtc_str = _wwm_time(endtc_dt)

    stations = read_surface_stations(station_in_path)
    if not stations:
        print(f"  WARNING {ym}: no surface stations found in station.in; "
              f"STATION block will be empty.")

    print(f"  BEGTC = {begtc_str}  ENDTC = {endtc_str}")
    print(f"  dt={p['dt']}s  nstep_wwm={p['nstep_wwm']}  "
          f"→ WWM DELTC={p['dt'] * p['nstep_wwm']:.1f}s")
    print(f"  msc2={p['msc2']}  mdc2={p['mdc2']}  h0={p['h0']}")
    lhotr_str = '.true.' if (not is_first_month and p['ihot'] > 0) else '.false.'
    print(f"  ihot={p['ihot']}  LHOTR='{lhotr_str}'")
    print(f"  Surface stations: {len(stations)}")

    # --- Template substitution ---
    text = template_path.read_text(errors='ignore')
    text = _apply_substitutions(
        text, p, begtc_str, endtc_str,
        stations, cfg, ym, is_first_month
    )

    # --- Write output ---
    out_path.write_text(text)
    sentinel.touch()
    print(f"  Written: {out_path}")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point (all months)
# =============================================================================

def run_gen_wwminput(cfg: dict):
    """
    Generate wwminput.nml for every month in the project date range.

    Called by SchismWwmDriver.preprocess().
    """
    months = list_months(cfg)
    prog   = ProgressTracker(total=len(months), label='gen_wwminput')
    failed = []

    print(f"\n{'=' * 60}")
    print(f"  gen_wwminput: {months[0]} -> {months[-1]}  ({len(months)} months)")
    print(f"  Template: fix/wwminput.nml")
    print(f"{'=' * 60}\n")

    for i, ym in enumerate(months):
        ok = gen_wwminput_month(cfg, ym, is_first_month=(i == 0))
        if not ok:
            failed.append(ym)
        prog.update(ym)

    print(f"\n{'=' * 60}")
    if not failed:
        print("  gen_wwminput complete. No failures.")
    else:
        print(f"  gen_wwminput complete with {len(failed)} failure(s):")
        for m in failed:
            print(f"    {m}")
        print("  Re-run to retry (existing sentinels are skipped).")
    print(f"{'=' * 60}\n")
