"""
build/recipe.py
===============
Build recipe layer: machine profiles and the `build:` block of build.yaml.

  machine profile   workflow/build/machines/<name>.yaml (modules, compilers,
                    UFS modulefile).  Everything that depends on the HPC.
  build recipe      `build:` in <config_dir>/build.yaml: which sources
                    (repo + ref) and targets (what to build, how).
  recipe hash       fingerprint of everything that determines a target's
                    result; stored in the target's sentinel so that editing
                    the recipe rebuilds and an unchanged recipe is skipped.

build.yaml layout::

    build:
      machine: hercules               # machine profile name, or a path to one
      source_root: /abs/path          # clones and build dirs (no default)
      sources:
        schism: {repo: <url>, ref: master}      # one checkout per source
      targets:
        schism:
          kind: schism
          source: schism
          toolchain: schism_intel
          exe_key: schism             # executables.<exe_key> in project.yaml
          options: {NO_PARMETIS: true, TVD_LIM: VL}
"""

import hashlib
import json
import sys
from pathlib import Path

import yaml

MACHINES_DIR = Path(__file__).resolve().parent / "machines"

# kind -> (required keys, optional keys) of a target, besides kind itself.
KINDS = {
    "schism":             ({"source", "toolchain", "exe_key", "options"}, set()),
    "ufs":                ({"source", "toolchain", "compile_id", "make_opt",
                            "exe_key"}, {"install_tools"}),
    "hycom_utils":        ({"source", "toolchain"}, {"ssh_max_3Dth"}),
    "combine_hotstart":   ({"source", "toolchain"}, set()),
    "combine_output_mpi": ({"source", "toolchain"}, set()),
    "partition_tools":    ({"source", "toolchain"}, set()),
}
# Executable keys (project.yaml `executables:`) a kind installs besides exe_key.
KIND_EXE_KEYS = {
    "hycom_utils":        ("gen_hotstart", "gen_3Dth", "gen_nudge"),
    "combine_hotstart":   ("combine_hotstart",),
    "combine_output_mpi": ("combine_output",),
}


def load_machine(name: str) -> dict:
    """Machine profile by name (packaged) or by path to a .yaml file."""
    path = Path(name)
    if path.suffix != ".yaml":
        path = MACHINES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"machine profile not found: {path}")
    prof = yaml.safe_load(path.read_text())
    prof["_dir"] = str(path.parent)
    return prof


def modulefile_text(machine: dict, toolchain: str) -> str:
    """Verbatim text of a toolchain's modulefile (UFS toolchains)."""
    tc = machine["toolchains"][toolchain]
    return (Path(machine["_dir"]) / tc["modulefile"]).read_text()


def validate_build(cfg: dict) -> list:
    """Return a list of error strings for cfg['build'] (empty if valid)."""
    errs = []
    b = cfg.get("build")
    if not isinstance(b, dict):
        return ["build: block missing (config_dir/build.yaml)"]
    for k in ("machine", "source_root", "sources", "targets"):
        if k not in b:
            errs.append(f"build.{k} is required")
    if errs:
        return errs

    machine = None
    try:
        machine = load_machine(str(b["machine"]))
    except FileNotFoundError as exc:
        errs.append(str(exc))
    if not Path(str(b["source_root"])).is_absolute():
        errs.append("build.source_root must be an absolute path")

    sources = b["sources"] if isinstance(b["sources"], dict) else {}
    for name, src in sources.items():
        for k in ("repo", "ref"):
            if not isinstance(src, dict) or not src.get(k):
                errs.append(f"build.sources.{name}.{k} is required")
    targets = b["targets"] if isinstance(b["targets"], dict) else {}
    if not targets:
        errs.append("build.targets is empty")
    exes = cfg.get("executables") or {}

    for name, t in targets.items():
        kind = t.get("kind") if isinstance(t, dict) else None
        if kind not in KINDS:
            errs.append(f"build.targets.{name}.kind must be one of "
                        f"{sorted(KINDS)} (got {kind!r})")
            continue
        req, opt = KINDS[kind]
        for k in sorted(req - set(t)):
            errs.append(f"build.targets.{name}: missing {k}")
        for k in sorted(set(t) - req - opt - {"kind"}):
            errs.append(f"build.targets.{name}: unknown key {k}")
        if t.get("source") not in sources:
            errs.append(f"build.targets.{name}.source {t.get('source')!r} "
                        f"is not in build.sources")
        if machine is not None and t.get("toolchain") not in machine["toolchains"]:
            errs.append(f"build.targets.{name}.toolchain {t.get('toolchain')!r} "
                        f"is not in machine profile {machine.get('name')}")
        elif machine is not None and kind == "ufs":
            if "modulefile" not in machine["toolchains"][t["toolchain"]]:
                errs.append(f"build.targets.{name}: toolchain "
                            f"{t['toolchain']} has no modulefile (needed by ufs)")
        keys = list(KIND_EXE_KEYS.get(kind, ()))
        if t.get("exe_key"):
            keys.append(t["exe_key"])
        keys += list(t.get("install_tools") or [])
        for k in keys:
            if k not in exes:
                errs.append(f"build.targets.{name}: executables.{k} is not "
                            f"set in project.yaml")
    return errs


def recipe_hash(cfg: dict, target: str) -> str:
    """Fingerprint of everything that determines the result of `target`.

    Covers the target block, its source (repo + ref, not the resolved commit:
    a moving branch does not trigger rebuilds by itself), the toolchain
    definition and the modulefile text.
    """
    b = cfg["build"]
    t = b["targets"][target]
    machine = load_machine(str(b["machine"]))
    tc = machine["toolchains"][t["toolchain"]]
    parts = {
        "machine": machine.get("name"),
        "target": t,
        "source": b["sources"][t["source"]],
        "toolchain": tc,
        "modulefile": (modulefile_text(machine, t["toolchain"])
                       if "modulefile" in tc else None),
    }
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def check_build(cfg: dict):
    """Exit with a message if the build block is invalid."""
    errs = validate_build(cfg)
    if errs:
        print("ERROR: invalid build.yaml:")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)
