"""
models/ufs_schism/preprocess/gen_ufs_configure.py
==================================================
Generate ufs.configure file for one month.

Reads fix/ufs.configure as a template, substitutes coupling parameters
(MED/ATM/OCN petlist bounds, coupling mode, run type, etc.) from
ufs_schism.yaml and the forecast length from the already-generated
model_configure, and writes I{ID}/I{ID}_YYYYMM/ufs.configure.

Prerequisite: gen_model_configure must have run for this month.

Sentinel: I{ID}_YYYYMM/gen_ufs_configure.done
"""

import argparse
import re
import sys
from pathlib import Path

from workflow.core.config import load_config, model_dir


def _read_param_nml(mdir: Path) -> dict:
    """Read key=value pairs from fix/param.nml, stripping inline comments."""
    param_nml_path = mdir / "fix" / "param.nml"
    if not param_nml_path.exists():
        print(f"ERROR: param.nml not found in {mdir / 'fix'}")
        sys.exit(1)
    params = {}
    for line in param_nml_path.read_text().splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        params[key.strip()] = value.split("!")[0].strip()
    return params


def gen_ufs_configure_month(cfg: dict, ym: str) -> bool:
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "ufs.configure"
    if not template_path.exists():
        print(f"ERROR: Template file not found: {template_path}")
        return False

    model_configure_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "model_configure"
    if not model_configure_path.exists():
        print(f"ERROR: model_configure not found: {model_configure_path}")
        print("  Run gen_model_configure for this month first.")
        return False

    # Read forecast length from model_configure.
    mc_content = model_configure_path.read_text()
    m = re.search(r"^nhours_fcst\s*:\s*(\d+)", mc_content, re.MULTILINE)
    if not m:
        print(f"ERROR: could not find nhours_fcst in {model_configure_path}")
        return False
    nhours_fcst = int(m.group(1))

    # Read dt from fix/param.nml.
    param_nml = _read_param_nml(mdir)
    if "dt" not in param_nml:
        print(f"ERROR: 'dt' not found in {mdir / 'fix' / 'param.nml'}")
        return False
    dt = int(float(param_nml["dt"]))

    out_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "ufs.configure"
    sentinel = out_path.parent / "gen_ufs_configure.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_ufs_configure: {ym} already complete. Skipping.")
        return True

    print(f"--- gen_ufs_configure {ym} -> {out_path} ---")

    # Values to substitute — read from ufs_schism.yaml via the merged cfg.
    replacements = {
        "MED_model":            cfg["med_model"],
        "MED_petlist_bounds":   cfg["med_petlist_bounds"],
        "MED_omp_num_threads":  cfg["med_omp_num_threads"],
        "ATM_model":            cfg["atm_model"],
        "ATM_petlist_bounds":   cfg["atm_petlist_bounds"],
        "ATM_omp_num_threads":  cfg["atm_omp_num_threads"],
        "OCN_model":            cfg["ocn_model"],
        "OCN_petlist_bounds":   cfg["ocn_petlist_bounds"],
        "OCN_omp_num_threads":  cfg["ocn_omp_num_threads"],
        "coupling_mode":        cfg["cpl_mode"],
        "meshloc":              "element",
        "CouplingConfig":       cfg["coupling_config"],
        "start_type":           cfg["run_type"],
        "case_name":            cfg["case_name"],
        "restart_n":            cfg["restart_n"],
        "stop_n":               nhours_fcst,
    }

    lines = template_path.read_text().splitlines()
    new_lines = []
    in_runseq = False
    runseq_timestep_written = False

    for line in lines:
        # --- runSeq block: replace the @<N> timestep ---
        if line.strip() == "runSeq::":
            new_lines.append(line)
            in_runseq = True
            runseq_timestep_written = False
            continue

        if in_runseq:
            if re.match(r"^\s*@\d+\s*$", line):
                if not runseq_timestep_written:
                    new_lines.append(f"@{dt}")
                    runseq_timestep_written = True
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
            if re.match(rf"^(\s*){re.escape(key)}(\s*[:=])", line):
                # Preserve indentation and separator from the template line.
                m2 = re.match(rf"^(\s*){re.escape(key)}(\s*[:=])", line)
                new_lines.append(
                    f"{m2.group(1)}{key}{m2.group(2)} {value}"
                )
                replaced = True
                break
        if not replaced:
            new_lines.append(line)

    out_path.write_text("\n".join(new_lines) + "\n")
    sentinel.touch()
    print(f"  Wrote {out_path}  (stop_n={nhours_fcst}, dt={dt})")
    print(f"  Sentinel: {sentinel}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate ufs.configure file for a given month.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--month",  required=True, help="YYYYMM")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    if not gen_ufs_configure_month(cfg, args.month):
        sys.exit(1)
