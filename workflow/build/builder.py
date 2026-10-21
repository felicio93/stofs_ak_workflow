"""
build/builder.py
================
Build targets of the `build:` recipe (see recipe.py).

For each target the login node prepares and submits, and the job compiles:

  1. sync_source     clone/fetch the source and check out `ref` (needs network)
  2. a fresh build directory <source_root>/_build/<target>/ with
     source_info.json (what was checked out)
  3. one SLURM job (templates/build_<kind>.sbatch) that compiles and then runs
     workflow.build.finish, which installs the executable into M{ID}/bin/
     under its `executables:` name and writes BUILD_INFO.yaml + the sentinel
     M{ID}/build/<target>.done (the recipe hash).

A target is skipped when its sentinel holds the current recipe hash and the
installed executables are present.
"""

import json
import shutil
from pathlib import Path

from workflow.build.recipe import load_machine, recipe_hash
from workflow.build.sources import source_dir, sync_source
from workflow.core.config import model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import (
    DEFAULT_ACCOUNT, DEFAULT_MAIL_USER, DEFAULT_PARTITION, SlurmSubmitter,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def build_dir(cfg: dict, target: str) -> Path:
    return Path(cfg["build"]["source_root"]) / "_build" / target


def sentinel(cfg: dict, target: str) -> Path:
    return model_dir(cfg) / "build" / f"{target}.done"


def installed_names(cfg: dict, target: str) -> list:
    """Names (in M{ID}/bin/) of the executables a target installs."""
    t = cfg["build"]["targets"][target]
    if t["kind"] == "schism":
        return [cfg["executables"][t["exe_key"]]]
    raise NotImplementedError(f"build kind {t['kind']!r} is not implemented yet")


def is_current(cfg: dict, target: str) -> bool:
    s = sentinel(cfg, target)
    if not s.exists() or s.read_text().strip() != recipe_hash(cfg, target):
        return False
    return all((model_dir(cfg) / "bin" / n).exists()
               for n in installed_names(cfg, target))


def _cmake_defs(options: dict) -> str:
    defs = []
    for key, val in options.items():
        if isinstance(val, bool):
            val = "ON" if val else "OFF"
        defs.append(f"-D{key}={val}")
    return " ".join(defs)


def _slurm_subs(cfg: dict, target: str) -> dict:
    slurm = cfg.get("slurm", {})
    mdir  = model_dir(cfg)
    return {
        "TARGET":    target,
        "JOBNAME":   f"build_{target}_M{cfg['project_id']}",
        "ACCOUNT":   slurm.get("account",   DEFAULT_ACCOUNT),
        "PARTITION": slurm.get("partition", DEFAULT_PARTITION),
        "MAILUSER":  slurm.get("mail_user", DEFAULT_MAIL_USER),
        "CPUS":      slurm.get("build_cpus", 8),
        "MEM":       slurm.get("build_mem", "16G"),
        "WALLTIME":  slurm.get("build_walltime", "02:00:00"),
        "LOGDIR":    str(mdir / "logs"),
        "PY":        env_python(cfg, "build", default="swf_main"),
    }


def _build_schism(cfg: dict, config_dir: Path, target: str,
                  dry_run: bool) -> str:
    t       = cfg["build"]["targets"][target]
    machine = load_machine(str(cfg["build"]["machine"]))
    tc      = machine["toolchains"][t["toolchain"]]
    sdir    = source_dir(cfg, t["source"])
    bdir    = build_dir(cfg, target)

    if dry_run:
        info = {"commit": "dry-run"}
    else:
        info = sync_source(cfg, t["source"])
        if bdir.exists():
            shutil.rmtree(bdir)              # always a fresh configure
    bdir.mkdir(parents=True, exist_ok=True)
    (model_dir(cfg) / "logs").mkdir(parents=True, exist_ok=True)
    if not dry_run:
        (bdir / "source_info.json").write_text(json.dumps(info, indent=2))

    subs = _slurm_subs(cfg, target)
    subs.update({
        "BUILD_DIR":     str(bdir),
        "MODULE_LINES":  "\n".join(f"module load {m}" for m in tc["modules"]),
        "SRC_DIR":       str(sdir),
        "SCHISM_CACHE":  str(sdir / "cmake" / tc["schism_cache"]),
        "CMAKE_DEFS":    _cmake_defs(t["options"]),
        "SOURCE_COMMIT": info["commit"][:8],
        "CONFIG_DIR":    str(config_dir),
    })
    submitter = SlurmSubmitter(TEMPLATES_DIR, require_sbatch=not dry_run)
    script = bdir / f"build_{target}.sbatch"
    if dry_run:
        submitter.write_rendered("build_schism.sbatch", subs, script)
        print(f"  [dry run] rendered {script}; nothing submitted.")
        return ""
    out = submitter.render_and_submit("build_schism.sbatch", subs, script)
    print(f"  Log: {subs['LOGDIR']}/build_{target}.out")
    return SlurmSubmitter.parse_jobid(out)


def build_target(cfg: dict, config_dir: Path, target: str,
                 dry_run: bool = False) -> str:
    """Prepare and submit one target. Returns the job ID ('' if skipped)."""
    t = cfg["build"]["targets"][target]
    print(f"\n--- build target {target} ({t['kind']}) ---")
    if not dry_run and is_current(cfg, target):
        print(f"  {target}: up to date (recipe {recipe_hash(cfg, target)}), "
              f"skipping.")
        return ""
    if t["kind"] == "schism":
        return _build_schism(cfg, Path(config_dir), target, dry_run)
    raise NotImplementedError(f"build kind {t['kind']!r} is not implemented yet")
