"""
models/base.py
==============
Abstract base class for all model drivers.

A *driver* encapsulates everything the workflow knows about running one model
configuration end-to-end: pre-processing, model execution, and
post-processing. The orchestrator selects a driver from the project's
`model_type` (project.yaml) via `make_driver`, then calls one of the three
phase methods depending on the CLI mode.

Adding a new model
------------------
1. Create workflow/models/<name>/driver.py with a subclass of ModelDriver
   (or of an existing driver, e.g. SchismDriver, to reuse its steps).
2. Register it in the `registry` dict in make_driver() below, and add the
   name to KNOWN_MODEL_TYPES in workflow/core/config.py.
3. Add a <name>.yaml template under that model's templates/config/ if it
   needs model-specific config keys (loaded automatically for that
   model_type).
"""

from abc import ABC, abstractmethod
from pathlib import Path


class ModelDriver(ABC):
    """Common interface for every model configuration.

    Subclasses receive the merged config dict and the config directory (so
    they can locate the YAML files and render SLURM templates relative to the
    project). Each phase method honours an optional `only` argument used by
    `--only <step>` to run a single step regardless of the steps.yaml flags.
    """

    #: Human-readable name, e.g. "SCHISM" or "SCHISM+WWM".
    name: str = "base"

    def __init__(self, cfg: dict, config_dir: Path):
        self.cfg = cfg
        self.config_dir = Path(config_dir)

    # -- helpers -----------------------------------------------------------

    def enabled(self, step: str, only: str = None) -> bool:
        """Whether `step` should run: True if it matches `only`, or (when
        `only` is None) if its flag is true in steps.yaml."""
        if only is not None:
            return step == only
        return bool(self.cfg.get(step, False))

    # -- phases ------------------------------------------------------------

    @abstractmethod
    def preprocess(self, only: str = None):
        """Generate all model inputs (forcing, boundary conditions, etc.)."""
        raise NotImplementedError

    @abstractmethod
    def run(self, only: str = None):
        """Submit/monitor the model run and manage hotstart chaining."""
        raise NotImplementedError

    @abstractmethod
    def postprocess(self, only: str = None):
        """Plot outputs, validate against observations, compute skill, etc."""
        raise NotImplementedError


# =============================================================================
# Driver registry / factory
# =============================================================================

def make_driver(cfg: dict, config_dir: Path) -> ModelDriver:
    """Instantiate the driver named by cfg['model_type'] (default: schism)."""
    model_type = str(cfg.get("model_type", "schism")).lower()

    # Imported lazily so that importing this module doesn't drag in every
    # model's dependencies.
    from workflow.models.schism.driver import SchismDriver
    from workflow.models.schism_wwm.driver import SchismWwmDriver
    from workflow.models.schism_mice.driver import SchismMiceDriver
    from workflow.models.ufs_coastal.driver import UfsCoastalDriver

    registry = {
        "schism":       SchismDriver,
        "schism_wwm":   SchismWwmDriver,
        "schism_mice":  SchismMiceDriver,
        "ufs_coastal":  UfsCoastalDriver,
    }

    cls = registry.get(model_type)
    if cls is None:
        import sys
        print(f"ERROR: unknown model_type '{model_type}'. "
              f"Valid options: {', '.join(sorted(registry))}")
        sys.exit(1)
    return cls(cfg, config_dir)
