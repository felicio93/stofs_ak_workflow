"""
core/config.py
==============
Shared configuration loading and group-enumeration helpers used by every
workflow step and model driver.

Grouping modes
--------------
  monthly  : calendar-month groups, IDs are 'YYYYMM' strings.
             This is the original behavior and is fully backward-compatible.
  ndays    : fixed-length groups of N days, IDs are 'YYYYMMDD' strings
             (the start date of each group).
             Aliases that normalise to ndays at load time:
               daily     -> group_ndays: 1
               weekly    -> group_ndays: 7
               biweekly  -> group_ndays: 14

The canonical internal representation is always one of:
  cfg['grouping'] == 'monthly'
  cfg['grouping'] == 'ndays'  and  cfg['group_ndays'] == <int>

All downstream code should use the helpers below rather than inspecting
cfg['grouping'] directly.
"""

import sys
import time
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from dateutil.relativedelta import relativedelta


# ---------------------------------------------------------------------------
# Config files
# ---------------------------------------------------------------------------

BASE_CONFIG_FILES = ("project.yaml", "domain.yaml", "steps.yaml", "envs.yaml")
KNOWN_MODEL_TYPES = ("schism", "schism_wwm", "schism_mice", "ufs_schism")

# Canonical grouping values accepted in project.yaml.
# Aliases are normalised to one of the two canonical forms at load time.
KNOWN_GROUPINGS = ("monthly", "ndays", "daily", "weekly", "biweekly")

# Maps convenience aliases -> (canonical_grouping, group_ndays or None)
_GROUPING_ALIASES = {
    "daily":    ("ndays", 1),
    "weekly":   ("ndays", 7),
    "biweekly": ("ndays", 14),
    "monthly":  ("monthly", None),
    "ndays":    ("ndays", None),   # pass-through; group_ndays must be set
}


# ---------------------------------------------------------------------------
# Grouping normalisation (called by load_config)
# ---------------------------------------------------------------------------

def normalize_grouping(cfg: dict) -> dict:
    """Normalise grouping aliases to the two canonical internal forms.

    After this call:
      cfg['grouping'] is either 'monthly' or 'ndays'
      cfg['group_ndays'] is set to an int whenever grouping == 'ndays'

    Modifies cfg in-place and returns it.
    """
    raw = str(cfg.get("grouping", "monthly")).lower().strip()

    if raw not in _GROUPING_ALIASES:
        print(f"ERROR: unknown grouping '{raw}'. "
              f"Valid options: {', '.join(sorted(KNOWN_GROUPINGS))}")
        sys.exit(1)

    canonical, ndays = _GROUPING_ALIASES[raw]
    cfg["grouping"] = canonical

    if canonical == "ndays":
        if ndays is not None:
            # alias supplied the value (daily/weekly/biweekly)
            cfg["group_ndays"] = ndays
        else:
            # user wrote grouping: ndays — they must supply group_ndays
            if "group_ndays" not in cfg:
                print("ERROR: grouping: ndays requires group_ndays to be set "
                      "in project.yaml  (e.g.  group_ndays: 10)")
                sys.exit(1)
            try:
                cfg["group_ndays"] = int(cfg["group_ndays"])
            except (TypeError, ValueError):
                print(f"ERROR: group_ndays must be a positive integer, "
                      f"got: {cfg['group_ndays']!r}")
                sys.exit(1)
            if cfg["group_ndays"] < 1:
                print(f"ERROR: group_ndays must be >= 1, "
                      f"got: {cfg['group_ndays']}")
                sys.exit(1)

    return cfg


# ---------------------------------------------------------------------------
# Group enumeration
# ---------------------------------------------------------------------------

def list_groups(cfg: dict) -> list:
    """Return an ordered list of group identifiers for the project date range.

    Monthly  -> ['YYYYMM', ...]           e.g. ['202409', '202410', ...]
    ndays    -> ['YYYYMMDD', ...]         e.g. ['20240901', '20240908', ...]

    The last group may be shorter than group_ndays if the end_date does not
    fall on a group boundary — the model simply runs for fewer days.
    """
    start = date.fromisoformat(cfg["start_date"])
    end   = date.fromisoformat(cfg["end_date"])

    if cfg.get("grouping") == "monthly":
        groups  = []
        current = date(start.year, start.month, 1)
        last    = date(end.year,   end.month,   1)
        while current <= last:
            groups.append(current.strftime("%Y%m"))
            current += relativedelta(months=1)
        return groups

    # ndays grouping
    n       = int(cfg["group_ndays"])
    groups  = []
    current = start
    while current <= end:
        groups.append(current.strftime("%Y%m%d"))
        current += timedelta(days=n)
    return groups


def list_months(cfg: dict) -> list:
    """Backward-compatible alias for list_groups()."""
    return list_groups(cfg)


# ---------------------------------------------------------------------------
# Group date range
# ---------------------------------------------------------------------------

def group_date_range(cfg: dict, group_id: str) -> tuple:
    """Return (start_date, end_date) as date objects for a group.

    end_date is the LAST simulation day of the group (inclusive).
    It is capped at cfg['end_date'] so the final group is never longer
    than the project end date.

    Monthly  group_id: 'YYYYMM'
    ndays    group_id: 'YYYYMMDD'
    """
    project_end = date.fromisoformat(cfg["end_date"])

    if cfg.get("grouping") == "monthly":
        year   = int(group_id[:4])
        month  = int(group_id[4:])
        start  = date(year, month, 1)
        ndays  = monthrange(year, month)[1]
        end    = date(year, month, ndays)
    else:
        n     = int(cfg["group_ndays"])
        start = date.fromisoformat(group_id)
        end   = start + timedelta(days=n - 1)

    # Never let end exceed the project end date.
    end = min(end, project_end)
    return start, end


# ---------------------------------------------------------------------------
# Group length helpers
# ---------------------------------------------------------------------------

def get_group_ndays(cfg: dict, group_id: str) -> int:
    """Return the number of simulation days in a group.

    For monthly groups this is the calendar length of the month (28-31).
    For ndays groups this is cfg['group_ndays'], capped at the project end.
    """
    start, end = group_date_range(cfg, group_id)
    return (end - start).days + 1


def stack_ceiling(cfg: dict, group_id: str) -> int:
    """Return the number of daily records each forcing stack must contain.

    This equals the group length plus a read-ahead buffer of 3 days.
    The buffer ensures SCHISM's internal read-ahead never runs out of
    records regardless of group length.

    Examples
    --------
    daily  (1 day)  -> 4
    weekly (7 days) -> 10
    monthly Sep     -> 33  (30 + 3)
    monthly Oct     -> 34  (31 + 3)
    """
    READ_AHEAD_BUFFER = 3
    return get_group_ndays(cfg, group_id) + READ_AHEAD_BUFFER


# ---------------------------------------------------------------------------
# Convenience path helpers
# ---------------------------------------------------------------------------

def model_dir(cfg: dict) -> Path:
    """Return the M{ID} directory path."""
    return Path(cfg["project_dir"]) / f"M{cfg['project_id']}"


# ---------------------------------------------------------------------------
# Progress / logging helpers
# ---------------------------------------------------------------------------

def _fmt_hms(seconds: float) -> str:
    """Format a duration in seconds as H:MM:SS."""
    seconds = int(max(0, seconds))
    h, rem  = divmod(seconds, 3600)
    m, s    = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


class ProgressTracker:
    """Dependency-free, log-friendly progress reporter.

    Prints one new line per completed item (good for ``tail -f`` on a
    redirected log). Each line shows count, percent, elapsed time, ETA,
    and the current item label.

    Usage::

        prog = ProgressTracker(total=len(items), label="HYCOM download")
        for it in items:
            ... do work ...
            prog.update(str(it))
    """

    def __init__(self, total: int, label: str = "progress"):
        self.total = max(1, int(total))
        self.label = label
        self.done  = 0
        self.start = time.time()

    def update(self, item: str = ""):
        self.done  += 1
        elapsed     = time.time() - self.start
        per_item    = elapsed / self.done
        remaining   = per_item * (self.total - self.done)
        pct         = 100.0 * self.done / self.total
        tag         = f"  ({item})" if item else ""
        print(f"  [{self.done:>4}/{self.total}] {pct:5.1f}%  "
              f"elapsed {_fmt_hms(elapsed)}  ETA {_fmt_hms(remaining)}"
              f"{tag}", flush=True)


class DebugLog:
    """Append-only debug log for the verbose command trace.

    Keeps the screen clean: detailed lines (every shell command run, plus
    stderr on failures) go to a timestamped file under M{ID}/logs/, while
    only meaningful summaries are printed to stdout.

    Usage::

        dbg = DebugLog(model_dir / "logs", "download_hycom")
        dbg.write("CMD: " + " ".join(cmd))
        print(f"  debug trace -> {dbg.path}")
    """

    def __init__(self, logs_dir: Path, name: str):
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = logs_dir / f"{name}_debug_{stamp}.log"
        self._fh  = open(self.path, "a", buffering=1)   # line-buffered

    def write(self, line: str):
        self._fh.write(line.rstrip("\n") + "\n")

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(config_dir: Path) -> dict:
    """Load and merge YAML config files from the config directory.

    Base files (project / domain / steps / envs) are required.
    The model-specific file matching cfg['model_type'] (e.g. schism.yaml)
    is merged if present.  postprocess.yaml is optional.

    Grouping aliases are normalised before returning so all downstream
    code sees only 'monthly' or 'ndays'.
    """
    cfg = {}
    for fname in BASE_CONFIG_FILES:
        fpath = config_dir / fname
        if not fpath.exists():
            print(f"ERROR: Config file not found: {fpath}")
            sys.exit(1)
        with open(fpath) as f:
            data = yaml.safe_load(f)
            if data:
                cfg.update(data)

    # Active model YAML (schism.yaml / schism_wwm.yaml / ufs_schism.yaml …)
    model_type  = str(cfg.get("model_type", "schism")).lower()
    model_fpath = config_dir / f"{model_type}.yaml"
    if model_fpath.exists():
        with open(model_fpath) as f:
            data = yaml.safe_load(f)
            if data:
                cfg.update(data)

    # Optional Phase-5 settings
    pp_fpath = config_dir / "postprocess.yaml"
    if pp_fpath.exists():
        with open(pp_fpath) as f:
            data = yaml.safe_load(f)
            if data:
                cfg.update(data)

    # Normalise grouping aliases to canonical internal form.
    normalize_grouping(cfg)

    return cfg
