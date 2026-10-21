#!/usr/bin/env python3
"""
utils/check_config_templates.py
===============================
Consistency check for the per-model config templates in
templates/config_example/<model>/.

For every model folder it verifies that:
  * the seven expected files exist (and no stray yaml, e.g. a separate
    schism.yaml in a derived-model folder, which would be ignored);
  * the folder loads through workflow.core.config.load_config and passes
    orchestrator.validate_config, and project.yaml model_type matches the
    folder name;
  * every steps.yaml key is a known step, is a boolean, and applies to the
    model (e.g. no UFS steps in schism, diag_run_plots only for schism);
  * known dead/removed config keys are absent;
  * the keys the model needs are present (executables, model yaml keys);
  * domain.yaml is identical in all folders and the SCHISM keys repeated in
    each model yaml have the same values (drift check).

Usage (needs pyyaml + python-dateutil, e.g. the swf_main env):
    python utils/check_config_templates.py
Exit status is non-zero if any check fails.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import orchestrator as orch                       # noqa: E402
from workflow.core.config import (               # noqa: E402
    load_config, KNOWN_MODEL_TYPES,
)
from workflow.build.recipe import validate_build  # noqa: E402

TEMPLATES = ROOT / "templates" / "config_example"
MODELS = ("schism", "schism_wwm", "ufs_schism", "ufs_schism_ww3")

UFS = {"ufs_schism", "ufs_schism_ww3"}
WAVES = {"schism_wwm", "ufs_schism_ww3"}

UFS_STEPS = {
    "gen_datm", "plot_datm", "gen_esmf_mesh", "gen_datm_in",
    "gen_datm_streams", "copy_fd_ufs", "copy_noahmptable",
    "gen_model_configure", "copy_modulefiles", "gen_ufs_configure",
}
WWM_STEPS = {"gen_wwmbnd", "gen_wwminput"}
WW3_STEPS = {"gen_ww3_shel", "gen_ww3_ounp", "plot_outputs_ww3"}
WAVE_STEPS = {"wave_skill", "download_altimetry", "collocate_altimetry",
              "plot_altimetry"}

DEAD_KEYS = {
    "combine_diag_nranks", "combine_diag_walltime", "compare_sst_max_ntasks",
    "datm_time_units_template", "datm_fill_value", "run_type", "restart_n",
    "collocate_argo_plot_temp_hist_color", "collocate_argo_plot_psal_hist_color",
}

SCHISM_KEYS = {
    "estuary_temp", "estuary_sal", "outside_temp", "outside_sal",
    "open_boundary_flags", "tobc", "sobc", "tidal_constituents",
    "tidal_cutoff_depth", "open_boundaries", "hycom_dt", "hycom_lon_shift",
    "nudge_stride",
    "nbin", "mne_bin", "chain_hotstart",
}
MODEL_KEYS = {
    "schism": set(),
    "schism_wwm": {"wwm_cutoff_freq", "wwm_outstyle_station", "wwm_lsp1d",
                   "wwm_lsp2d", "wwm_deltc_hotfile", "wwm_procname"},
    "ufs_schism": {"datm_subdir", "datm_filename_template", "dt_atmos",
                   "coupling_dt", "med_model", "atm_model", "ocn_model",
                   "cpl_mode", "coupling_config", "case_name",
                   "med_petlist_bounds", "atm_petlist_bounds",
                   "ocn_petlist_bounds", "med_omp_num_threads",
                   "atm_omp_num_threads", "ocn_omp_num_threads"},
}
MODEL_KEYS["ufs_schism_ww3"] = MODEL_KEYS["ufs_schism"] | {
    "wav_model", "wav_petlist_bounds", "wav_omp_num_threads",
    "med_pio_typename", "med_pio_stride", "wav_mesh_file",
    "wav_pio_typename", "wav_pio_numiotasks", "wav_pio_stride",
    "wav_pio_rearranger", "wav_pio_root", "ww3_point_output_dt",
    "ww3_fix_files", "chain_ww3_restart",
}
BUILD_KINDS = {
    "schism": {"schism", "hycom_utils", "combine_hotstart", "partition_tools"},
    "schism_wwm": {"schism", "hycom_utils", "combine_hotstart", "partition_tools"},
    "ufs_schism": {"ufs", "hycom_utils", "combine_hotstart",
                   "combine_output_mpi", "partition_tools"},
    "ufs_schism_ww3": {"ufs", "hycom_utils", "combine_hotstart",
                       "combine_output_mpi", "partition_tools"},
}
EXE_KEY = {"schism": "schism", "schism_wwm": "schism_wwm",
           "ufs_schism": "ufs_schism", "ufs_schism_ww3": "ufs_schism_ww3"}

ALL_STEPS = (orch.BUILD_STEPS | orch.PREPROCESS_STEPS | orch.RUN_PHASE_STEPS
             | orch.POSTPROCESS_STEPS)


def step_applies(step: str, model: str) -> bool:
    if step in UFS_STEPS:
        return model in UFS
    if step in WWM_STEPS:
        return model == "schism_wwm"
    if step in WW3_STEPS:
        return model == "ufs_schism_ww3"
    if step in WAVE_STEPS:
        return model in WAVES
    if step == "diag_run_plots":
        return model == "schism"
    return True


def check_model(model: str, errors: list) -> dict:
    d = TEMPLATES / model

    def err(msg):
        errors.append(f"[{model}] {msg}")

    expected = {"project.yaml", "domain.yaml", "steps.yaml", "envs.yaml",
                "postprocess.yaml", "build.yaml", f"{model}.yaml"}
    present = {p.name for p in d.glob("*.yaml")} if d.is_dir() else set()
    for name in sorted(expected - present):
        err(f"missing file {name}")
    for name in sorted(present - expected):
        err(f"unexpected file {name} (only {model}.yaml is loaded)")
    if expected - present:
        return {}

    cfg = load_config(d)
    if str(cfg.get("model_type")) != model:
        err(f"project.yaml model_type is {cfg.get('model_type')!r}")
    if model not in KNOWN_MODEL_TYPES:
        err("model not in KNOWN_MODEL_TYPES")
    try:
        orch.validate_config(cfg)
    except SystemExit:
        err("orchestrator.validate_config rejected the config")

    steps = {k: v for k, v in
             __import__("yaml").safe_load((d / "steps.yaml").read_text()).items()}
    for k, v in steps.items():
        if k not in ALL_STEPS:
            err(f"steps.yaml: unknown step {k!r}")
        elif not step_applies(k, model):
            err(f"steps.yaml: step {k!r} does not apply to {model}")
        if not isinstance(v, bool):
            err(f"steps.yaml: {k!r} is not a boolean")
    applicable = {s for s in ALL_STEPS if step_applies(s, model)}
    # steps handled in the dispatchers but not stand-alone flags
    for s in sorted(applicable - set(steps)):
        err(f"steps.yaml: applicable step {s!r} is missing")

    for k in sorted(DEAD_KEYS & set(cfg)):
        err(f"dead key present: {k}")

    for k in sorted((SCHISM_KEYS | MODEL_KEYS[model]) - set(cfg)):
        err(f"{model}.yaml: missing key {k}")

    exes = cfg.get("executables") or {}
    needed = {EXE_KEY[model], "combine_hotstart",
              "gen_hotstart", "gen_3Dth", "gen_nudge"}
    if model in UFS:
        needed.add("combine_output")
    for k in sorted(needed - set(exes)):
        err(f"project.yaml executables: missing {k}")
    if model not in UFS and "combine_output" in exes:
        err("project.yaml executables: combine_output is UFS-only")

    if cfg.get("grouping") not in ("monthly", "ndays"):
        err(f"unexpected grouping {cfg.get('grouping')!r}")

    for e in validate_build(cfg):
        err(f"build.yaml: {e}")
    got = {t["kind"] for t in (cfg.get("build", {}).get("targets") or {}).values()}
    if got != BUILD_KINDS[model]:
        err(f"build.yaml: target kinds {sorted(got)} != expected "
            f"{sorted(BUILD_KINDS[model])}")
    return cfg


def main() -> int:
    errors: list = []
    cfgs = {m: check_model(m, errors) for m in MODELS}

    doms = {m: (TEMPLATES / m / "domain.yaml").read_text()
            for m in MODELS if (TEMPLATES / m / "domain.yaml").exists()}
    if len(set(doms.values())) > 1:
        errors.append("domain.yaml differs between model folders")

    ref = None
    for m in MODELS:
        c = cfgs.get(m) or {}
        vals = {k: c.get(k) for k in SCHISM_KEYS}
        if ref is None:
            ref = vals
        elif vals != ref:
            diff = sorted(k for k in SCHISM_KEYS if vals[k] != ref[k])
            errors.append(f"[{m}] SCHISM keys differ from schism: {diff}")

    if errors:
        print("\nTEMPLATE CHECK FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nAll config templates OK "
          f"({len(MODELS)} models: {', '.join(MODELS)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
