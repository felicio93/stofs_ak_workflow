"""
models/schism_mice/mice_nml.py
==============================
Reading, checking and editing mice.nml (&mice_in), the main parameter file
of SCHISM's multi-class sea-ice module (src/Multi_ice/mice_init.F90).

SCHISM initialises every &mice_in key to a junk value before reading the
file and aborts on any that is still junk, so all of MICE_KEYS must be
present, including the ones the module never uses.
"""

import re
from pathlib import Path

# &mice_in in mice_init.F90 (master 3f25bd24), in that order.
MICE_KEYS = (
    "ice_tests", "ihot_mice", "ice_advection", "ice_therm_on", "ievp",
    "ice_cutoff", "evp_rheol_steps", "mevp_rheol_steps", "delta_min",
    "theta_io", "mevp_alpha1", "mevp_alpha2", "pstar", "ellipse",
    "c_pressure", "niter_fct", "ice_gamma_fct", "h_ml0", "salt_ice",
    "salt_water",
)

IHOT_MICE_MEANING = {
    0: "cold start, ice-free",
    1: "restart from a previous MICE run's hotstart.nc",
    2: "initialise from HYCOM (sic2/sih2/siu2/siv2 in hotstart.nc)",
}


def _key_re(key: str):
    return re.compile(r"(?mi)^(\s*" + re.escape(key) + r"\s*=\s*)([^\s!/]+)")


def read_value(text: str, key: str):
    """Value string of `key` (first uncommented assignment), or None."""
    m = _key_re(key).search(text)
    return m.group(2) if m else None


def set_value(text: str, key: str, value) -> str:
    """`text` with the value of `key` replaced (comment kept); raises
    KeyError when the key is not in the file."""
    new, n = _key_re(key).subn(lambda m: m.group(1) + str(value), text,
                               count=1)
    if n == 0:
        raise KeyError(key)
    return new


def ihot_mice(text: str):
    """ihot_mice as an int, or None when missing or not an integer."""
    v = read_value(text, "ihot_mice")
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def check(path: Path) -> list:
    """Problems with mice.nml that would stop SCHISM at start-up."""
    if not path.exists():
        return [f"{path.name} not found"]
    text = path.read_text()
    probs = []
    missing = [k for k in MICE_KEYS if read_value(text, k) is None]
    if missing:
        probs.append(f"{path.name} lacks {', '.join(missing)} (SCHISM aborts "
                     f"on any &mice_in key that is not set, used or not)")
    ih = ihot_mice(text)
    if read_value(text, "ihot_mice") is not None and ih not in IHOT_MICE_MEANING:
        probs.append(f"{path.name} ihot_mice = {read_value(text, 'ihot_mice')}"
                     f" (must be 0, 1 or 2)")
    return probs
