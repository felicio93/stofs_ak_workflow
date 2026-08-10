"""
models/ufs_coastal/driver.py
===========================
PLACEHOLDER — UFS-Coastal based configurations (e.g. SCHISM within UFS,
UFS-Coastal SCHISM+WW3, SCHISM+CICE).

Unlike the SCHISM-native drivers, UFS-Coastal uses the UFS build/run
machinery (NEMS/CMEPS mediator, model_configure, ufs.configure, etc.), so this
derives directly from ModelDriver rather than SchismDriver. Concrete
UFS-Coastal variants can subclass this once implemented.
"""

from workflow.models.base import ModelDriver


class UfsCoastalDriver(ModelDriver):
    name = "UFS-Coastal"

    def preprocess(self, only: str = None):
        raise NotImplementedError(
            "UFS-Coastal preprocessing is not implemented yet."
        )

    def run(self, only: str = None):
        raise NotImplementedError(
            "UFS-Coastal run management is not implemented yet."
        )

    def postprocess(self, only: str = None):
        raise NotImplementedError(
            "UFS-Coastal post-processing is not implemented yet."
        )
