"""
core/environment.py
====================
Shared environment plumbing for the workflow. Two responsibilities:

1. Conda environment management (`setup_envs`, invoked via
   `stofs-ak --setup-envs`). Creates/verifies the conda envs the workflow
   needs:
     * swf_main : orchestrator + NCO/CDO + downloads + TPXO (bctides)
     * swf_plot : plotting (matplotlib/cartopy) + mesh diagnostics

2. Runtime environment guards shared by the downloaders and preprocessors:
     * check_dtn            -- refuse to run internet steps off the DTN
     * check_cdsapi         -- verify cdsapi + ~/.cdsapirc for CDS/EWDS
     * check_active_env     -- warn (soft) if the wrong conda env is active
     * check_required_tools -- ensure NCO/CDO binaries are on PATH
     * env_python           -- full path to a step's conda interpreter

These guards were previously duplicated in download_hycom/era5/glofas and
aggregate_hycom; they now live here so every step shares one implementation.
"""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


DTN_HOSTNAME_HINT = "dtn"


# =============================================================================
# Runtime environment guards (shared by downloaders + preprocessors)
# =============================================================================

def check_dtn(what: str = "This step"):
    """Refuse to run on a non-DTN host unless ALLOW_NON_DTN=1 is set.

    `what` is used only for the error message (e.g. 'HYCOM download').
    """
    hostname = socket.gethostname()
    if DTN_HOSTNAME_HINT in hostname.lower():
        print(f"  Host check: '{hostname}' (DTN). OK.")
        return
    if os.environ.get("ALLOW_NON_DTN") == "1":
        print("  Host check: not a DTN but ALLOW_NON_DTN=1 set. Proceeding.")
        return
    print(f"ERROR: '{hostname}' is not a DTN. {what} needs internet.")
    print("  ssh hercules-dtn.hpc.msstate.edu && conda activate swf_main")
    print("  Bypass on other systems with:  export ALLOW_NON_DTN=1")
    sys.exit(1)


def check_cdsapi(api_url: str = "https://cds.climate.copernicus.eu/api"):
    """Verify cdsapi is importable and ~/.cdsapirc exists.

    `api_url` is only used in the guidance printed when the file is missing
    (CDS for ERA5, EWDS for GloFAS).
    """
    try:
        import cdsapi  # noqa: F401
    except ImportError:
        print("ERROR: cdsapi not installed. Run: conda install -c conda-forge cdsapi")
        sys.exit(1)
    cdsapirc = Path.home() / ".cdsapirc"
    if not cdsapirc.exists():
        print(f"ERROR: {cdsapirc} not found.")
        print("  Create it with your Copernicus credentials:")
        print(f"  url: {api_url}")
        print("  key: <your-api-key>")
        sys.exit(1)
    print("  CDS/EWDS API check: ~/.cdsapirc found. OK.")


def check_active_env(cfg: dict, step: str):
    """Soft-warn if the active conda env doesn't match the one configured for
    `step` in envs.yaml. Never exits; this is only a guard rail."""
    expected = cfg.get("conda_envs", {}).get(step)
    if not expected:
        return
    active = os.environ.get("CONDA_DEFAULT_ENV", "")
    if active == expected:
        print(f"  Env check: '{active}' matches config. OK.")
        return
    print(f"  {'='*56}")
    print(f"  WARNING: wrong conda environment for {step}.")
    print(f"     active:   '{active or '(none)'}'")
    print(f"     expected: '{expected}'")
    print(f"  Activate:  conda activate {expected}")
    print(f"  {'='*56}")


def check_required_tools(tools, provider: str = "NCO/CDO"):
    """Ensure a list of CLI tools is on PATH; exit with guidance if not."""
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        print("ERROR: required command-line tools not found on PATH:")
        for t in missing:
            print(f"    - {t}")
        print(f"These are provided by {provider}. "
              f"Activate swf_main or 'module load nco cdo'.")
        sys.exit(1)


def env_python(cfg: dict, step: str, default: str = "swf_main") -> str:
    """Full path to the Python interpreter of the conda env configured for
    `step` in envs.yaml. Used by SLURM launchers that call interpreters by
    absolute path instead of `conda activate`."""
    conda_base = Path(cfg["conda_base"])
    env = cfg.get("conda_envs", {}).get(step, default)
    if env == "base":
        return str(conda_base / "bin" / "python")
    return str(conda_base / "envs" / env / "bin" / "python")


# =============================================================================
# Conda environment setup (stofs-ak --setup-envs)
# =============================================================================


# Required package spec per known environment. Extend here if you add envs.
ENV_SPECS = {
    "swf_main": {
        "conda_packages": [
            "python=3.11", "pyyaml", "python-dateutil", "nco", "cdo",
            "cdsapi", "netcdf4", "xarray", "scipy",
        ],
        # (python import name, pip/conda name) for verification
        "verify_imports": ["yaml", "dateutil", "cdsapi", "netCDF4", "xarray", "scipy"],
        "verify_tools": ["ncks", "ncpdq", "ncap2", "ncrename", "ncatted",
                         "ncrcat", "ncwa", "cdo"],
    },
    "swf_plot": {
        "conda_packages": [
            "python=3.11", "xarray", "matplotlib", "cartopy", "imageio",
            "netcdf4", "h5netcdf", "pandas", "numpy", "pyyaml",
            "python-dateutil", "mpi4py",
            # Argo/OCSTrack collocation (download_argo / collocate_argo).
            # gsw (conda-forge) gives accurate pressure->depth; ocstrack is
            # pip-installed after env creation (see the pip_packages note below).
            "gsw", "tqdm", "requests", "dask", "scipy",
        ],
        # Installed with pip after conda create (not on conda-forge under this
        # name). setup_envs runs this automatically for swf_plot.
        "pip_packages": ["ocstrack"],
        "verify_imports": ["xarray", "matplotlib", "cartopy", "imageio",
                           "netCDF4", "pandas", "numpy", "yaml", "dateutil",
                           "mpi4py", "gsw", "ocstrack"],
        "verify_tools": [],
    },
}


def conda_exe(cfg: dict) -> Path:
    """Path to the conda executable inside conda_base."""
    return Path(cfg["conda_base"]) / "bin" / "conda"


def _env_python_by_name(cfg: dict, env_name: str) -> Path:
    """Interpreter path for a conda env *by name* (used by setup_envs)."""
    if env_name == "base":
        return Path(cfg["conda_base"]) / "bin" / "python"
    return Path(cfg["conda_base"]) / "envs" / env_name / "bin" / "python"


def existing_envs(conda: Path):
    """Return the set of existing conda env names."""
    result = subprocess.run([str(conda), "env", "list"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: '{conda} env list' failed: {result.stderr.strip()}")
        sys.exit(1)
    names = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: "name    /path/to/env"  (active env may have a '*')
        parts = line.replace("*", " ").split()
        if parts:
            names.add(parts[0])
    return names


def create_env(conda: Path, name: str, packages):
    print(f"\n  Creating env '{name}' ...")
    cmd = [str(conda), "create", "-y", "-n", name, "-c", "conda-forge"] + packages
    print("  CMD:", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  ERROR: failed to create env '{name}'.")
        return False
    print(f"  Created env '{name}'.")
    return True


def pip_install(cfg: dict, name: str, packages) -> bool:
    """pip-install `packages` into an existing conda env `name`.

    Used for packages that are not on conda-forge under the same name
    (e.g. ocstrack). Idempotent: pip skips already-satisfied requirements.
    Returns True on success.
    """
    if not packages:
        return True
    py = _env_python_by_name(cfg, name)
    if not py.exists():
        print(f"  ERROR: interpreter not found for env '{name}': {py}")
        return False
    cmd = [str(py), "-m", "pip", "install", "--upgrade"] + list(packages)
    print(f"  pip install into '{name}': {' '.join(packages)}")
    print("  CMD:", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  ERROR: pip install into '{name}' failed for: {packages}")
        return False
    return True


def verify_env(cfg: dict, name: str, spec: dict) -> bool:
    """Verify required imports and CLI tools inside an existing env."""
    ok = True
    py = _env_python_by_name(cfg, name)
    if not py.exists():
        print(f"  ERROR: interpreter not found for env '{name}': {py}")
        return False

    # Verify python imports
    imports = spec.get("verify_imports", [])
    if imports:
        code = "import " + ", ".join(imports) + "; print('ok')"
        result = subprocess.run([str(py), "-c", code],
                                capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  [{name}] python imports OK: {', '.join(imports)}")
        else:
            ok = False
            print(f"  [{name}] MISSING python packages:")
            print("    " + result.stderr.strip().splitlines()[-1])

    # Verify CLI tools (must be on the env's bin)
    tools = spec.get("verify_tools", [])
    env_bin = py.parent
    for tool in tools:
        if (env_bin / tool).exists():
            continue
        # tool might still be resolvable via conda run; check with `which`
        result = subprocess.run([str(conda_exe(cfg)), "run", "-n", name,
                                 "which", tool],
                                capture_output=True, text=True)
        if result.returncode != 0:
            ok = False
            print(f"  [{name}] MISSING CLI tool: {tool}")
    if tools and ok:
        print(f"  [{name}] CLI tools OK: {', '.join(tools)}")

    return ok


def setup_envs(cfg: dict):
    conda = conda_exe(cfg)
    if not conda.exists():
        print(f"ERROR: conda not found at {conda} (check conda_base in envs.yaml)")
        sys.exit(1)

    # Which envs are actually referenced by the config's steps?
    referenced = set(cfg.get("conda_envs", {}).values())
    # Only manage envs we have specs for; warn about any unknown ones.
    targets = [e for e in referenced if e in ENV_SPECS]
    unknown = [e for e in referenced if e not in ENV_SPECS and e != "base"]

    print(f"\n{'='*60}")
    print(f"  Conda environment setup")
    print(f"  conda: {conda}")
    print(f"  envs referenced by config: {sorted(referenced)}")
    print(f"{'='*60}")

    if unknown:
        print(f"  NOTE: no package spec for env(s): {unknown}")
        print(f"        They will not be auto-created; create them manually.")

    have = existing_envs(conda)
    all_ok = True

    for name in targets:
        spec = ENV_SPECS[name]
        if name in have:
            print(f"\n  Env '{name}' exists -> verifying libraries...")
            # Ensure any pip-only packages (e.g. ocstrack) are present/updated
            # even for an already-existing env, so re-running --setup-envs adds
            # newly-required pip packages.
            if not pip_install(cfg, name, spec.get("pip_packages", [])):
                all_ok = False
            if not verify_env(cfg, name, spec):
                all_ok = False
                print(f"  [{name}] verification found problems (see above).")
        else:
            if not create_env(conda, name, spec["conda_packages"]):
                all_ok = False
                continue
            if not pip_install(cfg, name, spec.get("pip_packages", [])):
                all_ok = False
            verify_env(cfg, name, spec)

    print(f"\n{'='*60}")
    if all_ok:
        print("  Environment setup complete. All required envs present & verified.")
    else:
        print("  Environment setup finished WITH ISSUES (see warnings above).")
    print(f"{'='*60}\n")
