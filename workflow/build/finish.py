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


def update_build_info(info_file: Path, entry: dict, timeout: float = 300.0):
    """Add `entry` to BUILD_INFO.yaml without losing concurrent updates.

    The build jobs of a project finish at about the same time and all write
    this file.  A lock directory (mkdir is atomic on any file system, unlike
    flock on some Lustre mounts) serialises the read-modify-write, and the
    file is replaced atomically.  A file that is not valid YAML is kept as
    BUILD_INFO.yaml.corrupt and a new record is started.
    """
    import time
    info_file = Path(info_file)
    info_file.parent.mkdir(parents=True, exist_ok=True)
    lock = info_file.parent / (info_file.name + ".lock")
    t0 = time.time()
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.time() - t0 > timeout:      # a dead job left it behind
                print(f"WARNING: removing stale lock {lock}")
                shutil.rmtree(lock, ignore_errors=True)
                t0 = time.time()
            time.sleep(0.5)
    try:
        record = {}
        if info_file.exists():
            try:
                record = yaml.safe_load(info_file.read_text()) or {}
            except yaml.YAMLError as e:
                bad = info_file.parent / (info_file.name + ".corrupt")
                shutil.copy2(info_file, bad)
                print(f"WARNING: {info_file.name} is not valid YAML "
                      f"({type(e).__name__}); kept as {bad.name}, starting "
                      f"a new record.")
        record.update(entry)
        tmp = info_file.parent / (info_file.name + f".tmp{os.getpid()}")
        tmp.write_text(yaml.safe_dump(record, sort_keys=False))
        os.replace(tmp, info_file)
    finally:
        shutil.rmtree(lock, ignore_errors=True)


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


def _ufs_details(bdir: Path) -> dict:
    """Facts only the finished UFS build knows (from its CMake cache)."""
    out = {}
    cache = bdir / "ufs_build" / "CMakeCache.txt"
    if cache.exists():
        for line in cache.read_text().splitlines():
            if line.startswith("ESMFMKFILE"):
                out["esmfmkfile"] = line.split("=", 1)[1]
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
        if t["kind"] == "ufs":
            for name, _ in installed:
                metas[name] = {**metas.get(name, {}), **_ufs_details(bdir)}

    now   = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    mods  = [m for m in os.environ.get("LOADEDMODULES", "").split(":") if m]
    if t["kind"] == "ufs":      # compile.sh purges and loads its own modulefile
        mods = ["see details.modulefile (loaded by compile.sh)"]
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
    update_build_info(info_file, entry)

    s = sentinel(cfg, target)
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text(h + "\n")
    for name, _ in installed:
        print(f"Installed {model_dir(cfg) / 'bin' / name}")
    print(f"BUILD_INFO: {info_file}")
    print(f"Sentinel:   {s}")


if __name__ == "__main__":
    main()
