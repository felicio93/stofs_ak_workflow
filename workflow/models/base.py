"""
models/base.py
==============
Abstract base class for all model drivers.
"""

from abc import ABC, abstractmethod
from pathlib import Path


class ModelDriver(ABC):
    """Common interface for every model configuration."""

    name: str = "base"

    def __init__(self, cfg: dict, config_dir: Path):
        self.cfg = cfg
        self.config_dir = Path(config_dir)

    def enabled(self, step: str, only: str = None) -> bool:
        if only is not None:
            return step == only
        return bool(self.cfg.get(step, False))

    @abstractmethod
    def preprocess(self, only: str = None):
        raise NotImplementedError

    @abstractmethod
    def run(self, only: str = None):
        raise NotImplementedError

    @abstractmethod
    def postprocess(self, only: str = None):
        raise NotImplementedError


def make_driver(cfg: dict, config_dir: Path) -> ModelDriver:
    """Instantiate the driver named by cfg['model_type'] (default: schism)."""
    model_type = str(cfg.get("model_type", "schism")).lower()

    # Imported lazily so that importing this module doesn't drag in every
    # model's dependencies.
    from workflow.models.schism.driver import SchismDriver
    from workflow.models.schism_wwm.driver import SchismWwmDriver
    from workflow.models.schism_mice.driver import SchismMiceDriver
    from workflow.models.ufs_schism.driver import UfsSchismDriver

    registry = {
        "schism":      SchismDriver,
        "schism_wwm":  SchismWwmDriver,
        "schism_mice": SchismMiceDriver,
        "ufs_schism":  UfsSchismDriver,
    }

    cls = registry.get(model_type)
    if cls is None:
        import sys
        print(f"ERROR: unknown model_type '{model_type}'. "
              f"Valid options: {', '.join(sorted(registry))}")
        sys.exit(1)
    return cls(cfg, config_dir)
