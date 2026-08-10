"""
models/schism_mice/driver.py
===========================
PLACEHOLDER — SCHISM + MICE (internally coupled sea-ice model).

Inherits from SchismDriver to reuse standard SCHISM steps. Override to add
ice-specific inputs, coupled-run submission, and ice diagnostics.
"""

from workflow.models.schism.driver import SchismDriver


class SchismMiceDriver(SchismDriver):
    name = "SCHISM+MICE"

    def preprocess(self, only: str = None):
        raise NotImplementedError(
            "SCHISM+MICE preprocessing is not implemented yet. Extend "
            "SchismDriver.preprocess and add MICE-specific steps here."
        )
