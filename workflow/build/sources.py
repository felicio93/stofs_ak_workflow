"""
build/sources.py
================
Source checkouts for the build steps (login node: needs network and git).

One checkout per entry of `build.sources`, at <source_root>/<name>, detached
at the commit that `ref` (branch, tag or commit) resolves to.  The checkout
is owned by the workflow: it is never reset over local modifications, the
build stops instead.

A source can move some of its submodules to another commit, e.g. the SCHISM
inside UFS-Coastal to an upstream SCHISM commit::

    ufs-coastal-schism-master:
      repo: https://github.com/oceanmodeling/ufs-coastal.git
      ref: feature/coastal_app
      submodules:
        SCHISM-interface/SCHISM:                    # path in the checkout
          repo: https://github.com/schism-dev/schism.git   # optional
          ref: 3f25bd24

`repo` defaults to the submodule's own remote. Use a source name of its own
for such a variant: the checkout directory is named after the source, so
other projects' checkouts are not touched.
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


def _resolve(dest: Path, ref: str, remote: str = "origin") -> str:
    """Commit for `ref`: <remote>/<ref> (branch) first, then ref (tag/commit)."""
    for cand in (f"{remote}/{ref}", ref):
        r = _git(["rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}"],
                 cwd=dest, check=False)
        if r.returncode == 0:
            return r.stdout.strip()
    print(f"  ERROR: ref {ref!r} not found in {dest} "
          f"(neither a branch, tag nor commit).")
    sys.exit(1)


_SYNCED = {}        # (source dir, ref) -> info; one sync per source per run

# Remote added to an overridden submodule when `repo` is given.
_OVERRIDE_REMOTE = "workflow"


def _dirty(dest: Path, skip=()) -> str:
    """Tracked local modifications of `dest`, without the lines of the
    submodule paths in `skip` (moved on purpose by a submodule override)."""
    out = _git(["status", "--porcelain", "--untracked-files=no"],
               cwd=dest).stdout
    return "\n".join(l for l in out.splitlines()
                     if l.strip() and l[3:].strip() not in skip)


def _override_submodule(dest: Path, path: str, spec: dict) -> dict:
    """Check out submodule `path` of `dest` at spec['ref'] (from spec['repo']
    if given). Runs after `git submodule update`, which resets it."""
    sub = dest / path
    if not (sub / ".git").exists():
        print(f"  ERROR: submodule {path} not found in {dest}")
        sys.exit(1)
    remote = "origin"
    if spec.get("repo"):
        remote = _OVERRIDE_REMOTE
        have = _git(["remote", "get-url", remote], cwd=sub, check=False)
        if have.returncode != 0:
            _git(["remote", "add", remote, spec["repo"]], cwd=sub)
        elif have.stdout.strip() != spec["repo"]:
            _git(["remote", "set-url", remote, spec["repo"]], cwd=sub)
    print(f"  Fetching submodule {path} from {spec.get('repo') or remote} ...")
    _git(["fetch", "--tags", "--force", remote], cwd=sub, capture=False)
    commit = _resolve(sub, spec["ref"], remote)
    _git(["checkout", "--detach", commit], cwd=sub)
    _git(["submodule", "update", "--init", "--recursive"], cwd=sub,
         capture=False)
    describe = _git(["describe", "--tags", "--always"], cwd=sub).stdout.strip()
    print(f"  submodule {path}: {spec['ref']} -> {commit[:8]} ({describe})")
    return {"repo": spec.get("repo") or "(submodule remote)",
            "ref": spec["ref"], "commit": commit, "describe": describe}


def sync_source(cfg: dict, name: str) -> dict:
    """Clone or update source `name` and check out its ref.

    Returns a dict describing exactly what was checked out (repo, ref,
    resolved commit, describe, commit date, submodule commits).  Targets
    sharing a source trigger a single fetch per invocation.
    """
    src  = cfg["build"]["sources"][name]
    dest = source_dir(cfg, name)
    subs = src.get("submodules") or {}
    key  = (str(dest), src["ref"], repr(sorted(subs.items())))
    if key in _SYNCED:
        return _SYNCED[key]
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
        dirty = _dirty(dest, skip=subs)
        for path in subs:
            if (dest / path / ".git").exists() and _dirty(dest / path):
                dirty += f"\n(in {path})\n" + _dirty(dest / path)
        if dirty.strip():
            print(f"  ERROR: {dest} has local modifications:")
            print("  " + dirty.strip().replace("\n", "\n  "))
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
    overrides = {path: _override_submodule(dest, path, spec)
                 for path, spec in subs.items()}

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
    if overrides:
        info["submodule_overrides"] = overrides
    print(f"  {name}: {src['ref']} -> {commit[:8]} ({info['describe']})")
    _SYNCED[key] = info
    return info
