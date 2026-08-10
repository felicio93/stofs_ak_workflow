"""
models/schism_wwm/driver.py
==========================
PLACEHOLDER — SCHISM + WWM (internally coupled wind-wave model).

Inherits from SchismDriver so all standard SCHISM preprocessing/run/postprocess
steps are reused for free. Override only what WWM adds or changes, e.g.:
  * wwminput.nml generation in preprocess()
  * spectral wave boundary forcing
  * coupled-run submission in run()
  * wave-specific diagnostics in postprocess()
"""

from workflow.models.schism.driver import SchismDriver


class SchismWwmDriver(SchismDriver):
    name = "SCHISM+WWM"

    def preprocess(self, only: str = None):
        raise NotImplementedError(
            "SCHISM+WWM preprocessing is not implemented yet. Extend "
            "SchismDriver.preprocess and add WWM-specific steps here."
        )
