"""
models/ufs_schism/preprocess/gen_ufs_configure.py
==================================================
Generate ufs.configure file for one group.

Reads fix/ufs.configure as a template, substitutes coupling parameters
from ufs_schism.yaml and the forecast length from the already-generated
model_configure, and writes I{ID}/I{ID}_{group_id}/ufs.configure.

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

The runSeq coupling interval (@N) is set from coupling_dt in
ufs_schism.yaml (default 3600 seconds = hourly). This is intentionally
decoupled from SCHISM's internal dt — the coupling interval should match
the atmospheric forcing frequency (hourly ERA5/DATM), NOT SCHISM's dt.

Prerequisite: gen_model_configure must have run for this group.

Sentinel: I{ID}_{group_id}/gen_ufs_configure.done
"""

import argparse
import re
import sys
from pathlib import Path

from workflow.core.config import (
    load_config,
    model_dir,
    list_groups,
)


def _read_param_nml(mdir: Path) -> dict:
    """Read key=value pairs from fix/param.nml."""
    param_nml_path = mdir / "fix" / "param.nml"
    if not param_nml_path.exists():
        print(f"ERROR: param.nml not found in "
              f"{mdir / 'fix'}")
        sys.exit(1)
    params = {}
    for line in param_nml_path.read_text().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        params[key.strip()] = value.split("!")[0].strip()
    return params


def gen_ufs_configure_group(cfg: dict,
                             group_id: str) -> bool:
    """Generate ufs.configure for one group.

    Returns True on success, False on failure.
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "ufs.configure"
    if not template_path.exists():
        print(f"ERROR: Template file not found: "
              f"{template_path}")
        return False

    # Read nhours_fcst from already-generated model_configure
    model_configure_path = (
        mdir / f"I{pid}" / f"I{pid}_{group_id}"
        / "model_configure"
    )
    if not model_configure_path.exists():
        print(f"ERROR: model_configure not found: "
              f"{model_configure_path}")
        print("  Run gen_model_configure for this group "
              "first.")
        return False

    mc_content = model_configure_path.read_text()
    m = re.search(
        r"^nhours_fcst\s*:\s*(\d+)",
        mc_content, re.MULTILINE)
    if not m:
        print(f"ERROR: could not find nhours_fcst in "
              f"{model_configure_path}")
        return False
    nhours_fcst = int(m.group(1))

    # Read SCHISM dt from fix/param.nml
    param_nml = _read_param_nml(mdir)
    if "dt" not in param_nml:
        print(f"ERROR: 'dt' not found in "
              f"{mdir / 'fix' / 'param.nml'}")
        return False
    schism_dt = int(float(param_nml["dt"]))

    # Coupling interval from ufs_schism.yaml
    coupling_dt = int(cfg.get("coupling_dt", 3600))

    if coupling_dt == schism_dt and schism_dt < 600:
        print(f"  WARNING: coupling_dt={coupling_dt}s equals "
              f"SCHISM dt={schism_dt}s.")
        print(f"  This causes ESMF field exchange every "
              f"timestep and is very slow.")
        print(f"  Consider setting coupling_dt: 3600 in "
              f"ufs_schism.yaml.")

    out_path = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                / "ufs.configure")
    sentinel = out_path.parent / "gen_ufs_configure.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_ufs_configure: {group_id} already "
              f"complete. Skipping.")
        return True

    print(f"--- gen_ufs_configure {group_id} -> "
          f"{out_path} ---")
    print(f"  SCHISM dt={schism_dt}s, "
          f"coupling_dt={coupling_dt}s, "
          f"stop_n={nhours_fcst}h")

    # Values to substitute in the template
    replacements = {
        "MED_model":           cfg["med_model"],
        "MED_petlist_bounds":  cfg["med_petlist_bounds"],
        "MED_omp_num_threads": cfg["med_omp_num_threads"],
        "ATM_model":           cfg["atm_model"],
        "ATM_petlist_bounds":  cfg["atm_petlist_bounds"],
        "ATM_omp_num_threads": cfg["atm_omp_num_threads"],
        "OCN_model":           cfg["ocn_model"],
        "OCN_petlist_bounds":  cfg["ocn_petlist_bounds"],
        "OCN_omp_num_threads": cfg["ocn_omp_num_threads"],
        "coupling_mode":       cfg["cpl_mode"],
        "meshloc":             "element",
        "CouplingConfig":      cfg["coupling_config"],
        "start_type":          cfg["run_type"],
        "case_name":           cfg["case_name"],
        "restart_n":           cfg["restart_n"],
        "stop_n":              nhours_fcst,
    }

    lines     = template_path.read_text().splitlines()
    new_lines = []
    in_runseq          = False
    runseq_dt_written  = False

    for line in lines:
        # --- runSeq block: replace @N with coupling_dt ---
        if line.strip() == "runSeq::":
            new_lines.append(line)
            in_runseq         = True
            runseq_dt_written = False
            continue

        if in_runseq:
            if re.match(r"^\s*@\d+\s*$", line):
                if not runseq_dt_written:
                    new_lines.append(f"@{coupling_dt}")
                    runseq_dt_written = True
                continue
            if line.strip() == "::":
                new_lines.append(line)
                in_runseq = False
                continue
            new_lines.append(line)
            continue

        # --- Normal configuration lines ---
        replaced = False
        for key, value in replacements.items():
            if re.match(
                    rf"^(\s*){re.escape(key)}(\s*[:=])",
                    line):
                m2 = re.match(
                    rf"^(\s*){re.escape(key)}(\s*[:=])",
                    line)
                new_lines.append(
                    f"{m2.group(1)}{key}"
                    f"{m2.group(2)} {value}")
                replaced = True
                break
        if not replaced:
            new_lines.append(line)

    out_path.write_text("\n".join(new_lines) + "\n")
    sentinel.touch()
    print(f"  Wrote {out_path}  "
          f"(coupling_dt={coupling_dt}s, "
          f"stop_n={nhours_fcst}h, "
          f"schism_dt={schism_dt}s)")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point (all groups)
# =============================================================================

def run_gen_ufs_configure(cfg: dict):
    """Generate ufs.configure for every group."""
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    failed   = []

    print(f"\n{'='*60}")
    print(f"  gen_ufs_configure: {groups[0]} -> "
          f"{groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"{'='*60}\n")

    for group_id in groups:
        ok = gen_ufs_configure_group(cfg, group_id)
        if not ok:
            failed.append(group_id)

    print(f"\n{'='*60}")
    if not failed:
        print("  gen_ufs_configure complete. No failures.")
    else:
        print(f"  gen_ufs_configure complete with "
              f"{len(failed)} failure(s):")
        for g in failed:
            print(f"    {g}")
    print(f"{'='*60}\n")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate ufs.configure file for a "
                    "given group.")
    parser.add_argument("--config", required=True)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--group", dest="group_id",
                     help="Group ID (YYYYMM or YYYYMMDD)")
    grp.add_argument("--month", dest="group_id",
                     help="Group ID — legacy alias for --group")
    args = parser.parse_args()
    cfg  = load_config(Path(args.config))
    if not gen_ufs_configure_group(cfg, args.group_id):
        sys.exit(1)
