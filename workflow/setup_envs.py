"""
setup_envs.py
=============
Create and/or verify the conda environments the workflow needs.

Invoked via:  python orchestrator.py --setup-envs --config <config_dir>

Environments (names come from envs.yaml -> conda_envs, deduplicated):
  * swf_main : orchestrator + NCO/CDO (download + aggregate)
      packages: python, pyyaml, python-dateutil, nco, cdo
  * swf_plot : plotting (matplotlib/cartopy) for plotting_debug
      packages: python, xarray, matplotlib, cartopy, imageio, netcdf4,
                pandas, numpy, pyyaml, python-dateutil

Behaviour:
  * Uses the `conda` executable from conda_base in envs.yaml.
  * If an env is missing, it is created with the right packages.
  * If an env exists, its required python imports / CLI tools are verified;
    missing ones are reported (env is NOT modified automatically).
  * Requires internet -> run on the DTN, like the download step.
"""

import subprocess
import sys
from pathlib import Path


# Required package spec per known environment. Extend here if you add envs.
ENV_SPECS = {
    "swf_main": {
        "conda_packages": [
            "python=3.11", "pyyaml", "python-dateutil", "nco", "cdo",
            "cdsapi", "netcdf4", "xarray",
        ],
        # (python import name, pip/conda name) for verification
        "verify_imports": ["yaml", "dateutil", "cdsapi", "netCDF4", "xarray"],
        "verify_tools": ["ncks", "ncpdq", "ncap2", "ncrename", "ncatted",
                         "ncrcat", "ncwa", "cdo"],
    },
    "swf_plot": {
        "conda_packages": [
            "python=3.11", "xarray", "matplotlib", "cartopy", "imageio",
            "netcdf4", "pandas", "numpy", "pyyaml", "python-dateutil",
        ],
        "verify_imports": ["xarray", "matplotlib", "cartopy", "imageio",
                           "netCDF4", "pandas", "numpy", "yaml", "dateutil"],
        "verify_tools": [],
    },
}


def conda_exe(cfg: dict) -> Path:
    """Path to the conda executable inside conda_base."""
    return Path(cfg["conda_base"]) / "bin" / "conda"


def env_python(cfg: dict, env_name: str) -> Path:
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


def verify_env(cfg: dict, name: str, spec: dict) -> bool:
    """Verify required imports and CLI tools inside an existing env."""
    ok = True
    py = env_python(cfg, name)
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
            if not verify_env(cfg, name, spec):
                all_ok = False
                print(f"  [{name}] verification found problems (see above).")
        else:
            if not create_env(conda, name, spec["conda_packages"]):
                all_ok = False
                continue
            verify_env(cfg, name, spec)

    print(f"\n{'='*60}")
    if all_ok:
        print("  Environment setup complete. All required envs present & verified.")
    else:
        print("  Environment setup finished WITH ISSUES (see warnings above).")
    print(f"{'='*60}\n")
