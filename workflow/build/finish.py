"""
build/finish.py
===============
Last command of a build job: install the executables of a target into
M{ID}/bin/ under their `executables:` names, record BUILD_INFO.yaml and write
the sentinel M{ID}/build/<target>.done (the recipe hash).

    python -m workflow.build.finish --config <config_dir> --target <target>
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from workflow.build.builder import build_dir, sentinel, target_hash
from workflow.build.recipe import load_machine
from workflow.core.config import load_config, model_dir


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _first_line(cmd: list) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except Exception:
        return "unknown"


def _install_schism(cfg: dict, t: dict, bdir: Path) -> list:
    """Copy the pschism executable; return [(installed name, built name)]."""
    exes = sorted((bdir / "bin").glob("pschism*"))
    if len(exes) != 1:
        print(f"ERROR: expected one pschism executable in {bdir / 'bin'}, "
              f"found {[e.name for e in exes]}")
        sys.exit(1)
    name = cfg["executables"][t["exe_key"]]
    dest = model_dir(cfg) / "bin" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exes[0], dest)
    dest.chmod(0o755)
    return [(name, exes[0].name)]


def _install_manifest(cfg: dict, bdir: Path, metas: dict) -> list:
    """Install the files listed in install_manifest.json (utility kinds)."""
    manifest = json.loads((bdir / "install_manifest.json").read_text())
    out = []
    for item in manifest:
        built = bdir / item["built"]
        if not built.exists():
            print(f"ERROR: expected build product missing: {built}")
            sys.exit(1)
        dest = model_dir(cfg) / "bin" / item["name"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, dest)
        dest.chmod(0o755)
        metas[item["name"]] = item.get("meta") or {}
        out.append((item["name"], built.name))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--target", required=True)
    args = ap.parse_args(argv)

    cfg     = load_config(Path(args.config))
    target  = args.target
    t       = cfg["build"]["targets"][target]
    bdir    = build_dir(cfg, target)
    info    = json.loads((bdir / "source_info.json").read_text())
    machine = load_machine(str(cfg["build"]["machine"]))
    tc      = machine["toolchains"][t["toolchain"]]

    metas = {}
    if t["kind"] == "schism":
        installed = _install_schism(cfg, t, bdir)
    else:
        installed = _install_manifest(cfg, bdir, metas)

    now   = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    mods  = [m for m in os.environ.get("LOADEDMODULES", "").split(":") if m]
    h     = target_hash(cfg, target)
    entry = {}
    for name, built in installed:
        path = model_dir(cfg) / "bin" / name
        entry[name] = {
            "target": target,
            "kind": t["kind"],
            "built": now,
            "recipe_hash": h,
            "built_name": built,
            "sha256": _sha256(path),
            "machine": machine.get("name"),
            "toolchain": t["toolchain"],
            "modules": mods,
            "fortran": _first_line([tc.get("fortran", "ifort"), "--version"]),
            "cmake": _first_line(["cmake", "--version"]),
            "options": t.get("options"),
            "details": metas.get(name) or None,
            "source": info,
        }

    info_file = model_dir(cfg) / "bin" / "BUILD_INFO.yaml"
    record = {}
    if info_file.exists():
        record = yaml.safe_load(info_file.read_text()) or {}
    record.update(entry)
    info_file.write_text(yaml.safe_dump(record, sort_keys=False))

    s = sentinel(cfg, target)
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text(h + "\n")
    for name, _ in installed:
        print(f"Installed {model_dir(cfg) / 'bin' / name}")
    print(f"BUILD_INFO: {info_file}")
    print(f"Sentinel:   {s}")


if __name__ == "__main__":
    main()
