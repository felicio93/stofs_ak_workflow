"""
core/station_parser.py
======================
Parser for the SCHISM ``station.in`` file, shared by the CO-OPS observation
downloader (postprocess.downloaders.coops) and the station skill-assessment
step (postprocess.station_skill).

station.in format
-----------------
Line 1  : on/off flags for the station output variables, in SCHISM's fixed
          order: elev, air pressure, windx, windy, T, S, u, v, w, (tracers...).
          e.g.  ``1 1 1 1 1 1 1 1 1 !on(1)|off(0) flags for elev,...``
Line 2  : number of stations (nsta)
Line 3+ : one line per station:

    <idx> <lon> <lat> <depth> ![VARS],<station_id>,<SOURCE>,<name>

where the comment after ``!`` (this workflow's convention) is:
    * ``[VARS]``      comma-separated variable tokens in brackets, e.g.
                      ``[WL,T]``, ``[WL]``, ``[T]``, ``[CU]``
    * ``<station_id>``the observation network's station id (e.g. 9459450)
    * ``<SOURCE>``    the observation network: CO-OPS / COOPS / NDBC (case- and
                      hyphen-insensitive)
    * ``<name>``      free-text station name

A station line is considered VALID for observation matching only when the
comment is EXACTLY ``[VARS],<id>,<SOURCE>,<name>`` — i.e. a bracketed token
list followed by three comma-separated fields. Lines that do not match are
silently ignored, e.g.:

    18 187.112 51.988 0 ! modulation1              -> no bracket, skipped
    24 196.639 54.798 -4.572 ![CU],CO-OPS,UNI1901  -> only 2 fields (no id), skipped

The 1-based station index (the first column, which is also the line's position
among the station lines) is retained as ``line_index``. This is the column
that maps a station to its value in the SCHISM ``outputs/staout_*`` files.
"""

import re
from pathlib import Path


# Canonical SCHISM staout variable order (staout_1 .. staout_9). Index in this
# list + 1 == the staout file number.
STAOUT_ORDER = [
    "elev",          # staout_1
    "air_pressure",  # staout_2
    "windx",         # staout_3
    "windy",         # staout_4
    "T",             # staout_5
    "S",             # staout_6
    "u",             # staout_7
    "v",             # staout_8
    "w",             # staout_9
]


def normalize_source(raw: str) -> str:
    """Normalize a source token to a canonical key.

    'CO-OPS', 'COOPS', 'co-ops', 'coops' -> 'CO-OPS'
    'NDBC', 'ndbc'                       -> 'NDBC'
    Anything else is upper-cased and returned as-is.
    """
    s = raw.strip().upper().replace("-", "").replace("_", "")
    if s == "COOPS":
        return "CO-OPS"
    if s == "NDBC":
        return "NDBC"
    return raw.strip().upper()


def _parse_var_tokens(bracket: str) -> list:
    """Return the list of raw variable tokens inside a '[...]' string.

    '[WL,T]' -> ['WL', 'T'] ; '[WL]' -> ['WL'] ; '[]' -> []
    """
    inner = bracket.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        return []
    inner = inner[1:-1].strip()
    if not inner:
        return []
    return [t.strip() for t in inner.split(",") if t.strip()]


def parse_station_in(path: Path) -> list:
    """Parse a SCHISM station.in file.

    Returns a list of dicts, one per VALID station line, each with:
        line_index : int   1-based position among the station lines (staout col)
        lon        : float
        lat        : float
        depth      : float
        vars       : list[str]  raw variable tokens, e.g. ['WL', 'T']
        station_id : str
        source     : str   normalized (CO-OPS / NDBC / ...)
        name       : str

    Invalid / non-conforming station lines (no bracket, or missing the
    id/source/name triplet) are skipped.
    """
    path = Path(path)
    text = path.read_text(errors="ignore").splitlines()

    # Skip the flag line (line 1) and the nsta line (line 2). Be tolerant of
    # blank lines before them.
    lines = [ln for ln in text]
    # Find the nsta line: the first line whose first token is an int AFTER the
    # flag line. Simpler: line 1 = flags, line 2 = nsta, station lines follow.
    if len(lines) < 3:
        return []

    station_lines = lines[2:]

    stations = []
    line_index = 0
    for raw in station_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        # Every station line contributes to the staout column count, whether or
        # not its comment is a valid observation spec. The staout files have one
        # column per station line in file order, so line_index must advance for
        # EVERY station row.
        line_index += 1

        # Split off the comment.
        if "!" not in stripped:
            continue
        data_part, comment = stripped.split("!", 1)
        comment = comment.strip()

        # The numeric part: idx lon lat depth
        nums = data_part.split()
        if len(nums) < 4:
            continue
        try:
            lon = float(nums[1])
            lat = float(nums[2])
            depth = float(nums[3])
        except ValueError:
            continue

        # The comment must be '[VARS],<id>,<SOURCE>,<name>'.
        # Require the bracket at the start.
        if not comment.startswith("["):
            continue
        # Split into [VARS] and the remaining comma fields.
        m = re.match(r"^\[(.*?)\](.*)$", comment)
        if not m:
            continue
        bracket = "[" + m.group(1) + "]"
        rest = m.group(2)
        # rest should start with a comma then id,source,name
        rest = rest.lstrip()
        if not rest.startswith(","):
            continue
        fields = [f.strip() for f in rest[1:].split(",")]
        # Need exactly id, source, name (3 fields). Malformed lines like
        # '[CU],CO-OPS,UNI1901' have only 2 fields -> skipped.
        if len(fields) != 3:
            continue
        station_id, source_raw, name = fields
        if not station_id or not source_raw:
            continue

        var_tokens = _parse_var_tokens(bracket)
        if not var_tokens:
            continue

        stations.append({
            "line_index": line_index,
            "lon": lon,
            "lat": lat,
            "depth": depth,
            "vars": var_tokens,
            "station_id": station_id,
            "source": normalize_source(source_raw),
            "name": name,
        })

    return stations
