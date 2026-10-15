"""
core/templating.py
==================
Helpers for the ``{{KEY}}`` placeholder convention used by every rendered
template (``.sbatch`` job scripts and ``auto_hotstart.py``).
"""

import re

_PLACEHOLDER = re.compile(r"\{\{[A-Za-z0-9_]+\}\}")


def unreplaced(text: str) -> list:
    """Return the sorted unique ``{{KEY}}`` placeholders left in `text`."""
    return sorted(set(_PLACEHOLDER.findall(text)))
