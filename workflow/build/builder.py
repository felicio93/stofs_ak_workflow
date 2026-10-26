"""
build/builder.py
================
Build targets of the `build:` recipe (see recipe.py).

For each target the login node prepares and submits, and the job compiles:

  1. sync_source     clone/fetch the source and check out `ref` (needs network)
  2. a fresh build directory <source_root>/_build/M{ID}/<target>/ with
     source_info.json (what was checked out) and, for utilities,
     install_manifest.json (what the job produces and where it is installed)
  3. one SLURM job (templates/build_<kind>.sbatch) that compiles and then runs
     workflow.build.finish, which installs the executables into M{ID}/bin/
     under their `executables:` names and writes BUILD_INFO.yaml + the
     sentinel M{ID}/build/<target>.done (the recipe hash).

A target is skipped when its sentinel holds the current recipe hash and the
installed files are present.

Utility kinds are described by a "plan": the shell commands of the job plus
the list of files to install.  Flags are the ones validated by hand on
Hercules (ifort / mpiifort, -mcmodel=medium -assume byterecl).
"""

import hashlib
import inspect
import json
import re
import shutil
import sys
from pathlib import Path

from workflow.build import hycom_patches
from workflow.build.recipe import (
    load_machine, modulefile_text, recipe_hash as _recipe_hash,
)
from workflow.build.sources import source_dir, sync_source
from workflow.core.config import model_dir
from workflow.core.environment import env_python
from workflow.core.slurm import (
    DEFAULT_ACCOUNT, DEFAULT_MAIL_USER, DEFAULT_PARTITION, SlurmSubmitter,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Files installed by partition_tools (fixed names, no executables: key)
PARTITION_FILES = ("metis_prep", "gpmetis", "partition_offline.pl")

_NETCDF_LINK = ("-I$NC/include -I$NF/include -L$NF/lib -L$NC/lib "
                "-lnetcdf -lnetcdff")


def build_dir(cfg: dict, target: str) -> Path:
    # One build directory per project: projects sharing a source_root (and so
    # the clones) must not compile into the same directory at the same time.
    return (Path(cfg["build"]["source_root"]) / "_build"
            / f"M{cfg['project_id']}" / target)


def sentinel(cfg: dict, target: str) -> Path:
    return model_dir(cfg) / "build" / f"{target}.done"


def _code_fingerprint(kind: str) -> str:
    """Hash of the code that builds a kind: plan, patches, job template."""
    parts = []
    fn = _PLANNERS.get(kind)
    if kind == "ufs":
        parts.append(inspect.getsource(_build_ufs))
        parts.append((TEMPLATES_DIR / "build_ufs.sbatch").read_text())
    elif fn is not None:
        parts.append(inspect.getsource(fn))
        parts.append((TEMPLATES_DIR / "build_utils.sbatch").read_text())
    else:
        parts.append((TEMPLATES_DIR / "build_schism.sbatch").read_text())
    if kind == "hycom_utils":
        parts.append(Path(hycom_patches.__file__).read_text())
    if kind == "partition_tools":
        parts.append(inspect.getsource(_patched_partition_script))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def target_hash(cfg: dict, target: str) -> str:
    """Recipe hash of a target, including the code that builds it."""
    kind = cfg["build"]["targets"][target]["kind"]
    return _recipe_hash(cfg, target, _code_fingerprint(kind))


def installed_names(cfg: dict, target: str) -> list:
    """Names (in M{ID}/bin/) of the files a target installs."""
    t, exes = cfg["build"]["targets"][target], cfg["executables"]
    kind = t["kind"]
    if kind == "schism":
        return [exes[t["exe_key"]]]
    if kind == "ufs":
        return ([exes[t["exe_key"]], f"modules.fv3_{t['compile_id']}.lua"]
                + [exes[k] for k in t.get("install_tools") or []])
    if kind == "hycom_utils":
        names = []
        for key in hycom_patches.UTILS:
            names += [exes[key], hycom_patches.lonshift_name(exes[key])]
        return names
    if kind == "combine_hotstart":
        return [exes["combine_hotstart"]]
    if kind == "combine_output_mpi":
        return [exes["combine_output"]]
    if kind == "partition_tools":
        return list(PARTITION_FILES)
    raise NotImplementedError(f"build kind {kind!r} is not implemented yet")


def is_current(cfg: dict, target: str) -> bool:
    s = sentinel(cfg, target)
    if not s.exists() or s.read_text().strip() != target_hash(cfg, target):
        return False
    return all((model_dir(cfg) / "bin" / n).exists()
               for n in installed_names(cfg, target))


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


def _prepare(cfg: dict, target: str, dry_run: bool):
    """Common preparation. Returns (source info, source dir, build dir)."""
    t    = cfg["build"]["targets"][target]
    sdir = source_dir(cfg, t["source"])
    bdir = build_dir(cfg, target)
    if dry_run:
        info = {"commit": "dry-run"}
    else:
        info = sync_source(cfg, t["source"])
        if bdir.exists():
            shutil.rmtree(bdir)              # always a fresh build
    bdir.mkdir(parents=True, exist_ok=True)
    (model_dir(cfg) / "logs").mkdir(parents=True, exist_ok=True)
    if not dry_run:
        (bdir / "source_info.json").write_text(json.dumps(info, indent=2))
    return info, sdir, bdir


def _submit(cfg, config_dir, target, template, subs, bdir, dry_run) -> str:
    submitter = SlurmSubmitter(TEMPLATES_DIR, require_sbatch=not dry_run)
    script = bdir / f"build_{target}.sbatch"
    if dry_run:
        submitter.write_rendered(template, subs, script)
        print(f"  [dry run] rendered {script}; nothing submitted.")
        return ""
    out = submitter.render_and_submit(template, subs, script)
    print(f"  Log: {subs['LOGDIR']}/build_{target}.out")
    return SlurmSubmitter.parse_jobid(out)


def _build_schism(cfg, config_dir, target, dry_run) -> str:
    t       = cfg["build"]["targets"][target]
    machine = load_machine(str(cfg["build"]["machine"]))
    tc      = machine["toolchains"][t["toolchain"]]
    info, sdir, bdir = _prepare(cfg, target, dry_run)
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
    return _submit(cfg, config_dir, target, "build_schism.sbatch", subs,
                   bdir, dry_run)


def _build_ufs(cfg, config_dir, target, dry_run) -> str:
    """UFS-Coastal executable through its own tests/compile.sh."""
    t       = cfg["build"]["targets"][target]
    machine = load_machine(str(cfg["build"]["machine"]))
    tc      = machine["toolchains"][t["toolchain"]]
    info, sdir, bdir = _prepare(cfg, target, dry_run)
    cid     = t["compile_id"]

    # compile.sh loads modulefiles/ufs_<machine_id>.<rt_compiler>.lua from the
    # checkout: write the profile's modulefile there (untracked file).
    lua_name = f"ufs_{tc['machine_id']}.{tc['rt_compiler']}.lua"
    lua_text = modulefile_text(machine, t["toolchain"])
    if not dry_run:
        (sdir / "modulefiles" / lua_name).write_text(lua_text)

    tests = sdir / "tests"
    installs = [
        {"built": str(tests / f"fv3_{cid}.exe"),
         "name": cfg["executables"][t["exe_key"]],
         "meta": {"make_opt": t["make_opt"], "compile_id": cid,
                  "modulefile": lua_name,
                  "modulefile_sha256": hashlib.sha256(
                      lua_text.encode()).hexdigest()}},
        {"built": str(tests / f"modules.fv3_{cid}.lua"),
         "name": f"modules.fv3_{cid}.lua", "meta": {}},
    ]
    for tool in t.get("install_tools") or []:
        installs.append({"built": str(bdir / "ufs_build" / "bin" / tool),
                         "name": cfg["executables"][tool],
                         "meta": {"from": "UFS WW3 tools"}})
    if not dry_run:
        (bdir / "install_manifest.json").write_text(
            json.dumps(installs, indent=2))

    slurm = cfg.get("slurm", {})
    subs = _slurm_subs(cfg, target)
    subs.update({
        "BUILD_DIR":      str(bdir),
        "UFS_BUILD_DIR":  str(bdir / "ufs_build"),
        "SRC_DIR":        str(sdir),
        "MACHINE_ID":     tc["machine_id"],
        "RT_COMPILER":    tc["rt_compiler"],
        "COMPILE_ID":     cid,
        "MAKE_OPT":       t["make_opt"],
        "SOURCE_COMMIT":  info["commit"][:8],
        "CONFIG_DIR":     str(config_dir),
        "MEM":            slurm.get("build_ufs_mem", "32G"),
        "WALLTIME":       slurm.get("build_ufs_walltime", "03:00:00"),
    })
    return _submit(cfg, config_dir, target, "build_ufs.sbatch", subs, bdir,
                   dry_run)


# -- utility plans -------------------------------------------------------------
# A plan is (commands, installs): `commands` are shell lines run in the build
# dir (after NC/NF are set); `installs` are dicts {built, name, meta} with
# `built` relative to the build dir.

def _plan_hycom(cfg, t, tc, sdir, bdir, dry_run):
    exes  = cfg["executables"]
    util  = sdir / "src" / "Utility"
    ssh   = t.get("ssh_max_3Dth")
    srcs  = bdir / "src"
    if dry_run:                 # no checkout: file names only
        made = [{"key": k, "variant": v, "stem": stem, "deps": deps,
                 "source": srcs / f"{stem}_{v}.f90"}
                for k, (stem, _, deps) in hycom_patches.UTILS.items()
                for v in hycom_patches.VARIANTS]
    else:
        made = hycom_patches.write_variants(util, srcs, ssh)
    cmds, installs = [], []
    flags = "-O2 -mcmodel=medium -assume byterecl -CB -g -traceback"
    for m in made:
        label = f"{m['stem']}_{m['variant']}"
        deps  = " ".join(str(util / "UtilLib" / f"{d}.f90") for d in m["deps"])
        cmds.append(
            f"mkdir -p v/{label} && (cd v/{label} && {tc['fortran']} {flags} "
            f"-o ../../bin/{label}.exe {deps} {m['source']} {_NETCDF_LINK})")
        base = exes[m["key"]]
        name = (base if m["variant"] == "noscaling"
                else hycom_patches.lonshift_name(base))
        meta = {"variant": m["variant"], "flags": flags,
                "patched_source": str(m["source"])}
        if not dry_run:
            meta["patched_source_sha256"] = _sha256(m["source"])
        if m["key"] == "gen_3Dth" and ssh is not None:
            meta["ssh_max"] = ssh
        installs.append({"built": f"bin/{label}.exe", "name": name,
                         "meta": meta})
    return cmds, installs


def _plan_combine_hotstart(cfg, t, tc, sdir, bdir, dry_run):
    util  = sdir / "src" / "Utility"
    flags = "-O2 -cpp -CB -mcmodel=medium -assume byterecl -g -traceback"
    cmds = [f"mkdir -p v/ch && (cd v/ch && {tc['fortran']} {flags} "
            f"-o ../../bin/combine_hotstart7 "
            f"{util / 'UtilLib' / 'argparse.f90'} "
            f"{util / 'Combining_Scripts' / 'combine_hotstart7.f90'} "
            f"{_NETCDF_LINK})"]
    return cmds, [{"built": "bin/combine_hotstart7",
                   "name": cfg["executables"]["combine_hotstart"],
                   "meta": {"flags": flags}}]


def _plan_combine_output(cfg, t, tc, sdir, bdir, dry_run):
    util  = sdir / "src" / "Utility"
    flags = "-cpp -O2 -mcmodel=medium -assume byterecl"
    cmds = [f"mkdir -p v/co && (cd v/co && {tc['mpi_fortran']} {flags} "
            f"-o ../../bin/combine_output11_MPI "
            f"{util / 'UtilLib' / 'argparse.f90'} "
            f"{util / 'UtilLib' / 'schism_geometry.f90'} "
            f"{util / 'Combining_Scripts' / 'combine_output11_MPI.F90'} "
            f"{_NETCDF_LINK})"]
    return cmds, [{"built": "bin/combine_output11_MPI",
                   "name": cfg["executables"]["combine_output"],
                   "meta": {"flags": flags, "mpi_fortran": tc["mpi_fortran"]}}]


def _patched_partition_script(util: Path, bin_dir: Path) -> str:
    """partition_offline.pl pointing at the installed metis_prep / gpmetis."""
    text = (util / "Grid_Scripts" / "partition_offline.pl").read_text()
    for var, exe in (("prep", "metis_prep"), ("gpmetis", "gpmetis")):
        pat = re.compile(rf'^\${var}=.*$', re.M)
        if len(pat.findall(text)) != 1:
            raise ValueError(f"partition_offline.pl: expected one '${var}=' "
                             f"line (upstream changed)")
        text = pat.sub(lambda _m: f'${var}="{bin_dir / exe}";', text)
    return text


def _plan_partition(cfg, t, tc, sdir, bdir, dry_run):
    util    = sdir / "src" / "Utility"
    bin_dir = model_dir(cfg) / "bin"
    pf = "-mcmodel=medium -O2 -CB -g -traceback"
    cmds = [
        f"mkdir -p v/prep && (cd v/prep && {tc['fortran']} {pf} "
        f"-o ../../bin/metis_prep {util / 'Grid_Scripts' / 'metis_prep.f90'})",
        # METIS builds inside its source tree: work on a copy
        f"cp -r {sdir / 'src' / 'metis-5.1.0'} metis && rm -rf metis/build",
        f"(cd metis && make config cc={tc['cc']} && make)",
        "cp metis/build/*/programs/gpmetis bin/gpmetis",
    ]
    if not dry_run:
        (bdir / "bin").mkdir(exist_ok=True)
        (bdir / "bin" / "partition_offline.pl").write_text(
            _patched_partition_script(util, bin_dir))
    installs = [{"built": f"bin/{n}", "name": n, "meta": {}}
                for n in PARTITION_FILES]
    installs[0]["meta"]["flags"] = pf
    installs[1]["meta"]["cc"] = tc["cc"]
    return cmds, installs


_PLANNERS = {
    "hycom_utils":        _plan_hycom,
    "combine_hotstart":   _plan_combine_hotstart,
    "combine_output_mpi": _plan_combine_output,
    "partition_tools":    _plan_partition,
}


def _build_utility(cfg, config_dir, target, dry_run) -> str:
    t       = cfg["build"]["targets"][target]
    machine = load_machine(str(cfg["build"]["machine"]))
    tc      = machine["toolchains"][t["toolchain"]]
    info, sdir, bdir = _prepare(cfg, target, dry_run)
    try:
        cmds, installs = _PLANNERS[t["kind"]](cfg, t, tc, sdir, bdir, dry_run)
    except ValueError as exc:             # upstream changed a patched line
        print(f"  ERROR: {exc}")
        sys.exit(1)
    if not dry_run:
        (bdir / "install_manifest.json").write_text(
            json.dumps(installs, indent=2))
    subs = _slurm_subs(cfg, target)
    subs.update({
        "BUILD_DIR":     str(bdir),
        "KIND":          t["kind"],
        "MODULE_LINES":  "\n".join(f"module load {m}" for m in tc["modules"]),
        "COMMANDS":      "\n".join(cmds),
        "SOURCE_COMMIT": info["commit"][:8],
        "CONFIG_DIR":    str(config_dir),
    })
    return _submit(cfg, config_dir, target, "build_utils.sbatch", subs, bdir,
                   dry_run)


def build_target(cfg: dict, config_dir: Path, target: str,
                 dry_run: bool = False) -> str:
    """Prepare and submit one target. Returns the job ID ('' if skipped)."""
    t = cfg["build"]["targets"][target]
    print(f"\n--- build target {target} ({t['kind']}) ---")
    if t["kind"] not in ("schism", "ufs", *_PLANNERS):
        raise NotImplementedError(f"build kind {t['kind']!r} is not "
                                  f"implemented yet")
    if not dry_run and is_current(cfg, target):
        print(f"  {target}: up to date (recipe {target_hash(cfg, target)}), "
              f"skipping.")
        return ""
    if t["kind"] == "schism":
        return _build_schism(cfg, Path(config_dir), target, dry_run)
    if t["kind"] == "ufs":
        return _build_ufs(cfg, Path(config_dir), target, dry_run)
    return _build_utility(cfg, Path(config_dir), target, dry_run)
