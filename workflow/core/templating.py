"""
core/templating.py
==================
Helpers for the ``{{KEY}}`` placeholder convention used by every rendered
template (``.sbatch`` job scripts and ``auto_hotstart.py``).
"""

import re
from pathlib import Path

_PLACEHOLDER = re.compile(r"\{\{[A-Za-z0-9_]+\}\}")


def unreplaced(text: str) -> list:
    """Return the sorted unique ``{{KEY}}`` placeholders left in `text`."""
    return sorted(set(_PLACEHOLDER.findall(text)))


def ufs_module_name(cfg: dict) -> str:
    """Modulefile that goes with the UFS executable of this run.

    UFS-Coastal's compile.sh installs tests/fv3_<id>.exe together with
    modules.fv3_<id>.lua, so the module name follows from the executable
    name: fv3_datm2sch.exe -> modules.fv3_datm2sch,
    fv3_datm2sch2ww3.exe -> modules.fv3_datm2sch2ww3.
    """
    exe = (cfg.get("executables") or {}).get(str(cfg.get("model_type")))
    if not exe:
        raise KeyError(f"executables.{cfg.get('model_type')} is not set")
    return "modules." + Path(exe).stem
