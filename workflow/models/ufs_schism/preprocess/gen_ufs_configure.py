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

    # Read nhours_fcst from model_configure
    mc_content = model_configure_path.read_text()
    match = re.search(r"^nhours_fcst\s*:\s*(\d+)", mc_content, re.MULTILINE)
    if not match:
        print(f"ERROR: Could not find nhours_fcst in {model_configure_path}")
        sys.exit(1)
    nhours_fcst = int(match.group(1))

    # Read dt from param.nml
    param_nml = read_param_nml(mdir)
    dt = int(float(param_nml.get("dt", 180))) # Default to 180 if not found

    replacements = {
        "med_model": cfg.get("med_model", "cmeps"),
        "atm_model": cfg.get("atm_model", "datm"),
        "ocn_model": cfg.get("ocn_model", "schism"),
        "med_petlist_bounds": cfg.get("med_petlist_bounds", "0 159"),
        "atm_petlist_bounds": cfg.get("atm_petlist_bounds", "0 159"),
        "ocn_petlist_bounds": cfg.get("ocn_petlist_bounds", "160 3352"),
        "med_omp_num_threads": cfg.get("med_omp_num_threads", 1),
        "atm_omp_num_threads": cfg.get("atm_omp_num_threads", 1),
        "ocn_omp_num_threads": cfg.get("ocn_omp_num_threads", 1),
        "CPLMODE": cfg.get("cpl_mode", "coastal"),
        "meshloc": "element",
        "coupling_config": cfg.get("coupling_config", "none"),
        "coupling_interval_slow_sec": dt,
        "RUNTYPE": cfg.get("run_type", "startup"),
        "casename": cfg.get("case_name", "ufs.cpld"),
        "RESTART_N": cfg.get("restart_n", 9999),
        "FHMAX": nhours_fcst,
    }

    out_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "ufs.configure"
    sentinel = out_path.parent / "gen_ufs_configure.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_ufs_configure: {ym} already complete. Skipping.")
        return

    print(f"--- gen_ufs_configure {ym} -> {out_path} ---")

    content = template_path.read_text()
    for key, value in replacements.items():
        content = re.sub(f"^({key}\s*=\s*).+$", f"\1{value}", content, flags=re.MULTILINE, count=1)
        # Handle run sequence block
        if key == "coupling_interval_slow_sec":
             content = re.sub(r"^(runSeq::\s*@).+$", f"\1{value}", content, flags=re.MULTILINE, count=1)


    out_path.write_text(content)
    sentinel.touch()

    print(f"  Wrote {out_path}")
    print(f"  Sentinel: {sentinel}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate ufs.configure file for a given month.")
    parser.add_argument("--config", required=True, help="Path to the config directory.")
    parser.add_argument("--month", required=True, help="Month in YYYYMM format.")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    gen_ufs_configure_month(config, args.month)
