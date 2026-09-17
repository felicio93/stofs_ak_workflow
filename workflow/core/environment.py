"""
core/environment.py
====================
Shared environment plumbing for the workflow.

swf_main provides everything needed for --phase all from a single
conda environment without switching:
  - NCO/CDO:     data processing tools
  - cdsapi:      ERA5/GloFAS downloads
  - netcdf4/xarray/scipy: scientific computing
  - ocstrack:    Argo float collocation (DTN + interactive)
  - matplotlib/pandas: station skill plots + data manipulation

swf_plot adds:
  - cartopy:     geographic map projections (SLURM plot jobs)
  - imageio:     GIF assembly (SLURM plot jobs)
  - mpi4py:      MPI parallel frame generation (SLURM plot jobs)
  - gsw/dask:    Argo profile analysis (SLURM collocate jobs)

check_dtn() raises DtnRequiredError instead of sys.exit(1) so that
drivers can catch it and skip gracefully when running --phase all
on a login node with all steps.yaml flags set to true.
"""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


DTN_HOSTNAME_HINT = "dtn"


# =============================================================================
# Custom exception for DTN-only steps
# =============================================================================

class DtnRequiredError(RuntimeError):
    """Raised when a DTN-only step is attempted on a non-DTN node."""
    pass


# =============================================================================
# Runtime environment guards
# =============================================================================

def check_dtn(what: str = "This step"):
    """Raise DtnRequiredError on a non-DTN host unless ALLOW_NON_DTN=1."""
    hostname = socket.gethostname()
    if DTN_HOSTNAME_HINT in hostname.lower():
        print(f"  Host check: '{hostname}' (DTN). OK.")
        return
    if os.environ.get("ALLOW_NON_DTN") == "1":
        print("  Host check: not a DTN but ALLOW_NON_DTN=1 "
              "set. Proceeding.")
        return
    raise DtnRequiredError(
        f"'{hostname}' is not a DTN. {what} needs internet.\n"
        f"  Run on the DTN: "
        f"ssh hercules-dtn.hpc.msstate.edu "
        f"&& conda activate swf_main\n"
        f"  Or bypass with: export ALLOW_NON_DTN=1"
    )


def check_cdsapi(
        api_url: str = "https://cds.climate.copernicus.eu/api"):
    """Verify cdsapi is importable and ~/.cdsapirc exists."""
    try:
        import cdsapi  # noqa: F401
    except ImportError:
        print("ERROR: cdsapi not installed. "
              "Run: conda install -c conda-forge cdsapi")
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
    """Soft-warn if the active conda env doesn't match envs.yaml."""
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
    """Ensure a list of CLI tools is on PATH."""
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        print("ERROR: required command-line tools not found "
              "on PATH:")
        for t in missing:
            print(f"    - {t}")
        print(f"These are provided by {provider}. "
              f"Activate swf_main or 'module load nco cdo'.")
        sys.exit(1)


def env_python(cfg: dict, step: str,
               default: str = "swf_main") -> str:
    """Full path to the Python interpreter for a step's conda env."""
    conda_base = Path(cfg["conda_base"])
    env = cfg.get("conda_envs", {}).get(step, default)
    if env == "base":
        return str(conda_base / "bin" / "python")
    return str(conda_base / "envs" / env / "bin" / "python")


# =============================================================================
# Conda environment setup (stofs-ak --setup-envs)
# =============================================================================

ENV_SPECS = {
    "swf_main": {
        # Core scientific stack + everything needed for interactive
        # postprocess steps (station_skill, collocate_argo, etc.)
        # so --phase all works from a single environment.
        "conda_packages": [
            "python=3.11", "pyyaml", "python-dateutil",
            "nco", "cdo",
            "cdsapi", "netcdf4", "xarray", "scipy",
            "matplotlib", "pandas",
        ],
        # ocstrack: Argo float collocation (download_argo,
        # collocate_argo). pip-only, no conda-forge package.
        "pip_packages": ["ocstrack"],
        "verify_imports": [
            "yaml", "dateutil", "cdsapi", "netCDF4",
            "xarray", "scipy",
            "matplotlib", "pandas",
            "ocstrack",
        ],
        "verify_tools": [
            "ncks", "ncpdq", "ncap2", "ncrename",
            "ncatted", "ncrcat", "ncwa", "cdo",
        ],
    },
    "swf_plot": {
        # Full plotting stack for SLURM jobs that render frames
        # and assemble GIFs. Adds cartopy, imageio, mpi4py, gsw,
        # dask on top of what swf_main provides.
        "conda_packages": [
            "python=3.11", "xarray", "matplotlib",
            "cartopy", "imageio", "netcdf4", "h5netcdf",
            "pandas", "numpy", "pyyaml",
            "python-dateutil", "mpi4py",
            "gsw", "tqdm", "requests", "dask", "scipy",
        ],
        # ocstrack also in swf_plot for SLURM collocate jobs
        # that use the swf_plot interpreter.
        "pip_packages": ["ocstrack"],
        "verify_imports": [
            "xarray", "matplotlib", "cartopy", "imageio",
            "netCDF4", "pandas", "numpy", "yaml",
            "dateutil", "mpi4py", "gsw", "ocstrack",
        ],
        "verify_tools": [],
    },
}


def conda_exe(cfg: dict) -> Path:
    return Path(cfg["conda_base"]) / "bin" / "conda"


def _env_python_by_name(cfg: dict,
                         env_name: str) -> Path:
    if env_name == "base":
        return Path(cfg["conda_base"]) / "bin" / "python"
    return (Path(cfg["conda_base"]) / "envs"
            / env_name / "bin" / "python")


def existing_envs(conda: Path):
    result = subprocess.run(
        [str(conda), "env", "list"],
        capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: '{conda} env list' failed: "
              f"{result.stderr.strip()}")
        sys.exit(1)
    names = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace("*", " ").split()
        if parts:
            names.add(parts[0])
    return names


def create_env(conda: Path, name: str, packages):
    print(f"\n  Creating env '{name}' ...")
    cmd = ([str(conda), "create", "-y", "-n", name,
            "-c", "conda-forge"] + packages)
    print("  CMD:", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  ERROR: failed to create env '{name}'.")
        return False
    print(f"  Created env '{name}'.")
    return True


def pip_install(cfg: dict, name: str,
                packages) -> bool:
    if not packages:
        return True
    py = _env_python_by_name(cfg, name)
    if not py.exists():
        print(f"  ERROR: interpreter not found for env "
              f"'{name}': {py}")
        return False
    cmd = ([str(py), "-m", "pip", "install", "--upgrade"]
           + list(packages))
    print(f"  pip install into '{name}': "
          f"{' '.join(packages)}")
    print("  CMD:", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"  ERROR: pip install into '{name}' "
              f"failed for: {packages}")
        return False
    return True


def verify_env(cfg: dict, name: str,
               spec: dict) -> bool:
    ok = True
    py = _env_python_by_name(cfg, name)
    if not py.exists():
        print(f"  ERROR: interpreter not found for env "
              f"'{name}': {py}")
        return False

    imports = spec.get("verify_imports", [])
    if imports:
        code = ("import " + ", ".join(imports)
                + "; print('ok')")
        result = subprocess.run(
            [str(py), "-c", code],
            capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  [{name}] python imports OK: "
                  f"{', '.join(imports)}")
        else:
            ok = False
            print(f"  [{name}] MISSING python packages:")
            lines = result.stderr.strip().splitlines()
            print("    " + (lines[-1] if lines
                             else "(no details)"))

    tools   = spec.get("verify_tools", [])
    env_bin = py.parent
    for tool in tools:
        if (env_bin / tool).exists():
            continue
        result = subprocess.run(
            [str(conda_exe(cfg)), "run", "-n", name,
             "which", tool],
            capture_output=True, text=True)
        if result.returncode != 0:
            ok = False
            print(f"  [{name}] MISSING CLI tool: {tool}")
    if tools and ok:
        print(f"  [{name}] CLI tools OK: "
              f"{', '.join(tools)}")

    return ok


def setup_envs(cfg: dict):
    conda = conda_exe(cfg)
    if not conda.exists():
        print(f"ERROR: conda not found at {conda} "
              f"(check conda_base in envs.yaml)")
        sys.exit(1)

    referenced = set(cfg.get("conda_envs", {}).values())
    targets    = [e for e in referenced
                  if e in ENV_SPECS]
    unknown    = [e for e in referenced
                  if e not in ENV_SPECS
                  and e != "base"]

    print(f"\n{'='*60}")
    print(f"  Conda environment setup")
    print(f"  conda: {conda}")
    print(f"  envs referenced by config: "
          f"{sorted(referenced)}")
    print(f"{'='*60}")

    if unknown:
        print(f"  NOTE: no package spec for env(s): "
              f"{unknown}")
        print(f"        They will not be auto-created; "
              f"create them manually.")

    have   = existing_envs(conda)
    all_ok = True

    for name in targets:
        spec = ENV_SPECS[name]
        if name in have:
            print(f"\n  Env '{name}' exists -> "
                  f"verifying libraries...")
            if not pip_install(
                    cfg, name,
                    spec.get("pip_packages", [])):
                all_ok = False
            if not verify_env(cfg, name, spec):
                all_ok = False
                print(f"  [{name}] verification found "
                      f"problems (see above).")
        else:
            if not create_env(
                    conda, name,
                    spec["conda_packages"]):
                all_ok = False
                continue
            if not pip_install(
                    cfg, name,
                    spec.get("pip_packages", [])):
                all_ok = False
            verify_env(cfg, name, spec)

    print(f"\n{'='*60}")
    if all_ok:
        print("  Environment setup complete. "
              "All required envs present & verified.")
    else:
        print("  Environment setup finished WITH ISSUES "
              "(see warnings above).")
    print(f"{'='*60}\n")
