"""
models/schism_mice/driver.py
============================
PLACEHOLDER — SCHISM + MICE (internally coupled sea-ice model).

Inherits from SchismDriver to reuse standard SCHISM steps. Override to add
ice-specific inputs, coupled-run submission, and ice diagnostics.
"""

from workflow.models.schism.driver import SchismDriver
from typing import Optional


class SchismMiceDriver(SchismDriver):
    name = "SCHISM+MICE"

    def preprocess(self, only: Optional[str] = None):
        raise NotImplementedError(
            "SCHISM+MICE preprocessing is not implemented yet. Extend "
            "SchismDriver.preprocess and add MICE-specific steps here."
        )

    def run(self, only: Optional[str] = None):
        raise NotImplementedError(
            "SCHISM+MICE run management is not implemented yet "
            "(it would otherwise silently run standalone SCHISM)."
        )

    def postprocess(self, only: Optional[str] = None):
        raise NotImplementedError(
            "SCHISM+MICE postprocessing is not implemented yet."
        )
