"""
config.py
=========
Shared configuration loading and month-enumeration helpers used by all
workflow steps.
"""

import sys
from datetime import date
from pathlib import Path

import yaml
from dateutil.relativedelta import relativedelta


CONFIG_FILES = ("project.yaml", "domain.yaml", "steps.yaml", "envs.yaml")


def load_config(config_dir: Path) -> dict:
    """Load and merge all YAML config files from the config directory."""
    cfg = {}
    for fname in CONFIG_FILES:
        fpath = config_dir / fname
        if not fpath.exists():
            print(f"ERROR: Config file not found: {fpath}")
            sys.exit(1)
        with open(fpath) as f:
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
