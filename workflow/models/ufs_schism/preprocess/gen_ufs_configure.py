import re
import sys
from pathlib import Path

from workflow.core.config import load_config, model_dir

def read_param_nml(mdir: Path) -> dict:
    param_nml_path = mdir / "fix" / "param.nml"
    if not param_nml_path.exists():
        print(f"ERROR: param.nml not found in {mdir / 'fix'}")
        sys.exit(1)

    params = {}
    content = param_nml_path.read_text()
    for line in content.splitlines():
        if '=' not in line:
            continue
        key, value = [x.strip() for x in line.split('=')[:2]]
        params[key] = value.split('!')[0].strip()
    return params

def gen_ufs_configure_month(cfg: dict, ym: str):
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    
    template_path = mdir / "fix" / "ufs.configure"
    if not template_path.exists():
        print(f"ERROR: Template file not found: {template_path}")
        sys.exit(1)

    model_configure_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "model_configure"
    if not model_configure_path.exists():
        print(f"ERROR: model_configure not found: {model_configure_path}")
        sys.exit(1)

    mc_content = model_configure_path.read_text()
    match = re.search(r"^nhours_fcst\s*:\s*(\d+)", mc_content, re.MULTILINE)
    if not match:
        print(f"ERROR: Could not find nhours_fcst in {model_configure_path}")
        return False
    nhours_fcst = int(match.group(1))

    param_nml = read_param_nml(mdir)
    dt = int(float(param_nml["dt"]))

    replacements = {
        "MED_model": cfg["med_model"],
        "ATM_model": cfg["atm_model"],
        "OCN_model": cfg["ocn_model"],
        "MED_petlist_bounds": cfg["med_petlist_bounds"],
        "ATM_petlist_bounds": cfg["atm_petlist_bounds"],
        "OCN_petlist_bounds": cfg["ocn_petlist_bounds"],
        "MED_omp_num_threads": cfg["med_omp_num_threads"],
        "ATM_omp_num_threads": cfg["atm_omp_num_threads"],
        "OCN_omp_num_threads": cfg["ocn_omp_num_threads"],
        "coupling_mode": cfg["cpl_mode"],
        "meshloc": "element",
        "CouplingConfig": cfg["coupling_config"],
        "start_type": cfg["run_type"],
        "case_name": cfg["case_name"],
        "restart_n": cfg["restart_n"],
        "stop_n": nhours_fcst,
    }

    out_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "ufs.configure"
    sentinel = out_path.parent / "gen_ufs_configure.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_ufs_configure: {ym} already complete. Skipping.")
        return True

    print(f"
--- gen_ufs_configure {ym} -> {out_path} ---")

    lines = template_path.read_text().splitlines()
    new_lines = []
    for line in lines:
        if any(key in line for key in replacements):
            for key, value in replacements.items():
                if key in line:
                    new_lines.append(f"{key}: {value}")
                    break
        elif "runSeq::" in line:
            new_lines.append(f"runSeq::
 @{dt}")
        else:
            new_lines.append(line)

    out_path.write_text("
".join(new_lines))
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate ufs.configure file for a given month.")
    parser.add_argument("--config", required=True, help="Path to the config directory.")
    parser.add_argument("--month", required=True, help="Month in YYYYMM format.")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    if gen_ufs_configure_month(config, args.month):
        sentinel = model_dir(config) / f"I{config['project_id']}" / f"I{config['project_id']}_{args.month}" / "gen_ufs_configure.done"
        sentinel.touch()
        print(f"  Wrote {sentinel.parent / 'ufs.configure'}")
        print(f"  Sentinel: {sentinel}")