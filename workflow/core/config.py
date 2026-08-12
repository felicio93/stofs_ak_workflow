"""
core/config.py
==============
Shared configuration loading and month-enumeration helpers used by every
workflow step and model driver.
"""

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from dateutil.relativedelta import relativedelta


# Config files loaded from the project's config/ directory. The base files are
# always required. The model-specific file is chosen from `model_type`
# (project.yaml) and loaded if present; it is optional until the model's
# phase-3 steps are used. Naming convention: <model_type>.yaml.
BASE_CONFIG_FILES = ("project.yaml", "domain.yaml", "steps.yaml", "envs.yaml")
KNOWN_MODEL_TYPES = ("schism", "schism_wwm", "schism_mice", "ufs_coastal")


def load_config(config_dir: Path) -> dict:
    """Load and merge YAML config files from the config directory.

    Base files (project/domain/steps/envs) are required. The model-specific
    file matching cfg['model_type'] (e.g. schism.yaml) is merged if present.

    Only the YAML for the active model_type is loaded — other model YAMLs in
    the same directory are ignored, so leftover configs cannot silently
    override the active model's keys.
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

    # Determine the active model and load only its YAML (default: schism).
    model_type = str(cfg.get("model_type", "schism")).lower()
    model_fname = f"{model_type}.yaml"
    model_fpath = config_dir / model_fname
    if model_fpath.exists():
        with open(model_fpath) as f:
            data = yaml.safe_load(f)
            if data:
                cfg.update(data)

    # Load postprocess.yaml if present (optional, phase-5 settings).
    pp_fpath = config_dir / "postprocess.yaml"
    if pp_fpath.exists():
        with open(pp_fpath) as f:
            data = yaml.safe_load(f)
            if data:
                cfg.update(data)

    return cfg


def list_months(cfg: dict):
    """
    Return an ordered list of 'YYYYMM' strings for every calendar month
    between start_date and end_date (inclusive of both endpoints' months).
    """
    start = date.fromisoformat(cfg["start_date"])
    end = date.fromisoformat(cfg["end_date"])
    months = []
    current = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while current <= last:
        months.append(current.strftime("%Y%m"))
        current += relativedelta(months=1)
    return months


def model_dir(cfg: dict) -> Path:
    """Return the M{ID} directory path."""
    return Path(cfg["project_dir"]) / f"M{cfg['project_id']}"


def _fmt_hms(seconds: float) -> str:
    """Format a duration in seconds as H:MM:SS."""
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


class ProgressTracker:
    """
    Dependency-free, log-friendly progress reporter.

    Prints one NEW line per completed item (good for `tail -f` on a redirected
    log, unlike a redrawing tqdm bar). Each line shows count, percent, elapsed
    time, ETA, and the current item label.

    Usage:
        prog = ProgressTracker(total=len(items), label="HYCOM download")
        for it in items:
            ... do work ...
            prog.update(str(it))
    """

    def __init__(self, total: int, label: str = "progress"):
        self.total = max(1, int(total))
        self.label = label
        self.done = 0
        self.start = time.time()

    def update(self, item: str = ""):
        """Mark one item complete and print a progress line."""
        self.done += 1
        elapsed = time.time() - self.start
        frac = self.done / self.total
        per_item = elapsed / self.done
        remaining = per_item * (self.total - self.done)
        pct = 100.0 * frac
        tag = f"  ({item})" if item else ""
        print(f"  [{self.done:>4}/{self.total}] {pct:5.1f}%  "
              f"elapsed {_fmt_hms(elapsed)}  ETA {_fmt_hms(remaining)}"
              f"{tag}", flush=True)


class DebugLog:
    """
    Append-only debug log for the verbose command trace.

    Keeps the screen clean: detailed lines (every shell command run, plus
    stderr on failures) go to a timestamped file under M{ID}/logs/, while
    only meaningful summaries are printed to stdout.

    Usage:
        dbg = DebugLog(model_dir / "logs", "download_hycom")
        dbg.write("CMD: " + " ".join(cmd))
        print(f"  debug trace -> {dbg.path}")
    """

    def __init__(self, logs_dir: Path, name: str):
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = logs_dir / f"{name}_debug_{stamp}.log"
        # Touch the file so it exists even if nothing is written.
        self._fh = open(self.path, "a", buffering=1)  # line-buffered

    def write(self, line: str):
        self._fh.write(line.rstrip("\n") + "\n")

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass
