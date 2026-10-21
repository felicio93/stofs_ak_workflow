"""
build/sources.py
================
Source checkouts for the build steps (login node: needs network and git).

One checkout per entry of `build.sources`, at <source_root>/<name>, detached
at the commit that `ref` (branch, tag or commit) resolves to.  The checkout
is owned by the workflow: it is never reset over local modifications, the
build stops instead.
"""

import subprocess
import sys
from pathlib import Path


def _git(args, cwd=None, check=True, capture=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=capture,
                       text=True)
    if check and r.returncode != 0:
        print(f"  ERROR: git {' '.join(args)} failed:")
        print(f"  {(r.stderr or '').strip()}")
        sys.exit(1)
    return r


def source_dir(cfg: dict, name: str) -> Path:
    return Path(cfg["build"]["source_root"]) / name


def _resolve(dest: Path, ref: str) -> str:
    """Commit for `ref`: origin/<ref> (branch) first, then ref (tag/commit)."""
    for cand in (f"origin/{ref}", ref):
        r = _git(["rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"],
                 cwd=dest, check=False)
        if r.returncode == 0:
            return r.stdout.strip()
    print(f"  ERROR: ref {ref!r} not found in {dest} "
          f"(neither a branch, tag nor commit).")
    sys.exit(1)


def sync_source(cfg: dict, name: str) -> dict:
    """Clone or update source `name` and check out its ref.

    Returns a dict describing exactly what was checked out (repo, ref,
    resolved commit, describe, commit date, submodule commits).
    """
    src  = cfg["build"]["sources"][name]
    dest = source_dir(cfg, name)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not dest.exists():
        print(f"  Cloning {src['repo']} -> {dest} (with submodules; "
              f"this can take a while) ...")
        _git(["clone", "--recurse-submodules", src["repo"], str(dest)],
             capture=False)
    else:
        origin = _git(["remote", "get-url", "origin"], cwd=dest).stdout.strip()
        if origin != src["repo"]:
            print(f"  WARNING: {dest} has origin {origin}, but build.sources."
                  f"{name}.repo is {src['repo']}")
        dirty = _git(["status", "--porcelain", "--untracked-files=no"],
                     cwd=dest).stdout.strip()
        if dirty:
            print(f"  ERROR: {dest} has local modifications:")
            print("  " + dirty.replace("\n", "\n  "))
            print("  This checkout is managed by the build steps. Clean it, "
                  "or use another build.source_root.")
            sys.exit(1)
        print(f"  Fetching {name} ...")
        _git(["fetch", "--tags", "--force", "origin"], cwd=dest,
             capture=False)

    commit = _resolve(dest, src["ref"])
    _git(["checkout", "--detach", commit], cwd=dest)
    _git(["submodule", "update", "--init", "--recursive"], cwd=dest,
         capture=False)

    out = lambda *a: _git(list(a), cwd=dest).stdout.strip()
    info = {
        "name": name,
        "repo": src["repo"],
        "ref": src["ref"],
        "commit": commit,
        "describe": out("describe", "--tags", "--always"),
        "commit_date": out("log", "-1", "--format=%cI"),
        "submodules": [l.strip() for l in
                       out("submodule", "status", "--recursive").splitlines()],
    }
    print(f"  {name}: {src['ref']} -> {commit[:8]} ({info['describe']})")
    return info
