"""
core/scaffold.py
================
Project scaffolding and the fix/ check.

  new_project   stofs-ak --new-project --model <type> --id NN --dir <dir>
                creates <dir>/M{NN}/config (from templates/<model>/config) and
                <dir>/M{NN}/fix (the starter files of templates/<model>/fix)

  check_fix     stofs-ak --check-fix --config <dir>/M{NN}/config
                reports which fix/ files the model needs are present

The file lists come from the model's setup_run.py (FIX_LINKS, ww3 files) and
from the steps that read fix/ (see FIX_SPEC), so there is one source of truth.
The templates live in the repository (templates/), outside the python
package: an editable install (pip install -e) is needed.
"""

import re
import shutil
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
MODELS = ("schism", "schism_wwm", "ufs_schism", "ufs_schism_ww3")

# Files made by a step (absence before that step is not an error).
GENERATED = {
    "estuary.gr3": "gen_estuary",
    "wwmbnd.gr3":  "gen_wwmbnd",
}

# Starter copies shipped in templates/<model>/fix/ (they encode choices, so
# they must be reviewed for every project).
_JOB_CARDS = ["param.nml", "run_test", "run_comb"]
TEMPLATE_FILES = {
    "schism":     _JOB_CARDS,
    "schism_wwm": _JOB_CARDS + ["wwminput.nml"],
    "ufs_schism": _JOB_CARDS + ["datm_in", "datm.streams", "fd_ufs.yaml",
                                "noahmptable.tbl", "model_configure"],
    "ufs_schism_ww3": _JOB_CARDS + [
        "datm_in", "datm.streams", "fd_ufs.yaml", "noahmptable.tbl",
        "model_configure", "ww3_shel.nml", "ww3_ounp.inp", "namelists.nml"],
}

# Files whose absence stops a step or the setup (everything else in the
# model's list is "expected": setup_run skips a missing one with a NOTE, and
# param.nml decides whether it is really needed).
_REQUIRED_BASE = ["hgrid.gr3", "hgrid.ll", "vgrid.in", "partition.prop",
                  "param.nml", "run_test", "run_comb"]
REQUIRED = {
    "schism":     _REQUIRED_BASE,
    "schism_wwm": _REQUIRED_BASE + ["wwminput.nml", "hgrid_WWM.gr3",
                                    "station.in"],
    "ufs_schism": _REQUIRED_BASE + ["datm_in", "datm.streams", "fd_ufs.yaml",
                                    "noahmptable.tbl", "model_configure"],
    "ufs_schism_ww3": _REQUIRED_BASE + [
        "datm_in", "datm.streams", "fd_ufs.yaml", "noahmptable.tbl",
        "model_configure", "ww3_shel.nml", "ww3_ounp.inp"],
}
# Read by gen_source (step gen_source) in every model.
_SOURCE_CSV = ["source_glofas.csv", "source_schism.csv"]
_WW3_DEFAULT_FILES = ["mod_def.ww3", "namelists.nml", "ww3_points.list",
                      "ST4TABUHF2.bin", "ww3_ESMFmesh.nc", "mesh.msh"]


def _linked_files(model: str, cfg: dict = None) -> list:
    """Static files setup_run links from fix/ (the model's own list)."""
    if model == "schism":
        from workflow.models.schism.run.setup_run import FIX_LINKS as f
    elif model == "schism_wwm":
        from workflow.models.schism_wwm.run.setup_run import FIX_LINKS as f
    elif model == "ufs_schism":
        from workflow.models.ufs_schism.run.setup_run import FIX_LINKS as f
    else:
        from workflow.models.ufs_schism_ww3.run.setup_run import (
            FIX_LINKS_BASE as f)
    files = list(f)
    if model == "ufs_schism_ww3":
        files += list((cfg or {}).get("ww3_fix_files", _WW3_DEFAULT_FILES))
    return files


def fix_spec(model: str, cfg: dict = None) -> dict:
    """{'required': [...], 'expected': [...], 'template': [...],
    'generated': {name: step}} for a model, in display order."""
    required = list(REQUIRED[model])
    templ    = TEMPLATE_FILES[model]
    seen     = set(required) | set(GENERATED)
    expected = []
    for name in _linked_files(model, cfg) + _SOURCE_CSV + [
            n for n in templ]:
        if name not in seen and name not in expected:
            expected.append(name)
    return {"required": required, "expected": expected,
            "template": list(templ),
            "generated": {k: v for k, v in GENERATED.items()
                          if k in _linked_files(model, cfg)}}


def _same(a: Path, b: Path) -> bool:
    return a.read_bytes() == b.read_bytes()


def check_fix(cfg: dict) -> bool:
    """Print the fix/ status of a project; return False if a required file
    is missing."""
    model = str(cfg.get("model_type", "schism")).lower()
    if model not in MODELS:
        print(f"ERROR: no fix/ specification for model_type {model!r}")
        return False
    from workflow.core.config import model_dir
    fix  = model_dir(cfg) / "fix"
    spec = fix_spec(model, cfg)
    tdir = TEMPLATES_DIR / model / "fix"
    ok   = True

    def status(name):
        p = fix / name
        if not p.exists():
            return "MISSING"
        tp = tdir / name
        if name in spec["template"] and tp.exists():
            return "ok (same as template)" if _same(p, tp) else "ok (edited)"
        return "ok"

    print(f"\nfix/ check: M{cfg['project_id']} ({model})  {fix}")
    print("\n  Required (a step or the run setup stops without them):")
    for n in spec["required"]:
        s = status(n)
        print(f"    {s:22s} {n}")
        ok &= s != "MISSING"
    print("\n  Expected (setup_run skips a missing one with a NOTE; "
          "param.nml decides if it is needed):")
    for n in spec["expected"]:
        print(f"    {status(n):22s} {n}")
    print("\n  Made by a step (absent until the step has run):")
    for n, step in spec["generated"].items():
        s = "present" if (fix / n).exists() else "not yet"
        print(f"    {s:22s} {n}   ({step})")
    if tdir.is_dir():
        print("\n  Template files (starting points from "
              f"templates/{model}/fix/) must be reviewed for every project.")
    print(f"\n  {'OK: all required files present.' if ok else 'MISSING required files.'}\n")
    return ok


def new_project(model: str, pid: str, root: Path) -> Path:
    """Create <root>/M{pid}/config and /fix from the model's templates."""
    if model not in MODELS:
        print(f"ERROR: --model must be one of {', '.join(MODELS)}")
        sys.exit(1)
    if not re.fullmatch(r"\d{2}", str(pid)):
        print(f"ERROR: --id must be two digits, got {pid!r}")
        sys.exit(1)
    src_cfg = TEMPLATES_DIR / model / "config"
    if not src_cfg.is_dir():
        print(f"ERROR: templates not found: {src_cfg}\n"
              "  --new-project needs the repository checkout "
              "(pip install -e).")
        sys.exit(1)
    root  = Path(root).resolve()
    mdir  = root / f"M{pid}"
    cfgd  = mdir / "config"
    if cfgd.exists() and any(cfgd.iterdir()):
        print(f"ERROR: {cfgd} already exists and is not empty.")
        sys.exit(1)
    cfgd.mkdir(parents=True, exist_ok=True)

    for f in sorted(src_cfg.glob("*.yaml")):
        text = f.read_text()
        if f.name == "project.yaml":
            text = re.sub(r'(?m)^project_id:.*$', f'project_id: "{pid}"', text)
            text = re.sub(r'(?m)^project_dir:.*$', f"project_dir: {root}", text)
        if f.name == "build.yaml":
            text = re.sub(r'(?m)^  source_root:.*$',
                          f"  source_root: {root / 'src'}", text)
        (cfgd / f.name).write_text(text)
        print(f"  Wrote {cfgd / f.name}")

    fixd = mdir / "fix"
    fixd.mkdir(parents=True, exist_ok=True)
    src_fix = TEMPLATES_DIR / model / "fix"
    if src_fix.is_dir():
        for f in sorted(src_fix.iterdir()):
            if f.is_file() and not (fixd / f.name).exists():
                shutil.copy2(f, fixd / f.name)
                print(f"  Copied template {fixd / f.name}")

    print(f"""
  Project M{pid} ({model}) created under {root}

  Next steps
    1. Edit {cfgd}/project.yaml, domain.yaml and steps.yaml
       (dates, grouping, domain box, SLURM account, steps to enable).
    2. Put your mesh files in {fixd}  (hgrid.gr3, hgrid.ll, vgrid.in,
       partition.prop, ...) and review the template files copied there.
    3. stofs-ak --check-fix --config {cfgd}
    4. stofs-ak --init --config {cfgd}
    5. stofs-ak --run --phase all --config {cfgd}   (builds the executables first)
""")
    return mdir
