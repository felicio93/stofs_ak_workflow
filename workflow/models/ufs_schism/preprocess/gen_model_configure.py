"""
models/ufs_schism/preprocess/gen_model_configure.py
====================================================
Generate model_configure file for one month.

Reads fix/model_configure as a template, substitutes the start date,
forecast length (nhours = calendar days in month * 24), and atmospheric
time step, and writes I{ID}/I{ID}_YYYYMM/model_configure.

Sentinel: I{ID}_YYYYMM/gen_model_configure.done
"""

import argparse
import sys
from calendar import monthrange
from pathlib import Path

from workflow.core.config import load_config, model_dir


def gen_model_configure_month(cfg: dict, ym: str) -> bool:
    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    year  = int(ym[:4])
    month = int(ym[4:])

    template_path = mdir / "fix" / "model_configure"
    if not template_path.exists():
        print(f"ERROR: Template file not found: {template_path}")
        return False

    _, ndays  = monthrange(year, month)
    nhours    = ndays * 24
    dt_atmos  = int(cfg.get("dt_atmos", 720))

    out_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "model_configure"
    sentinel = out_path.parent / "gen_model_configure.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_model_configure: {ym} already complete. Skipping.")
        return True

    print(f"--- gen_model_configure {ym} -> {out_path} ---")

    lines = template_path.read_text().splitlines()
    new_lines = []
    for line in lines:
        if "start_year:" in line:
            new_lines.append(f"start_year:              {year}")
        elif "start_month:" in line:
            new_lines.append(f"start_month:             {month:02d}")
        elif "start_day:" in line:
            new_lines.append("start_day:               1")
        elif "start_hour:" in line:
            new_lines.append("start_hour:              0")
        elif "nhours_fcst:" in line:
            new_lines.append(f"nhours_fcst:             {nhours}")
        elif "dt_atmos:" in line:
            new_lines.append(f"dt_atmos:                {dt_atmos}")
        else:
            new_lines.append(line)

    out_path.write_text("\n".join(new_lines))
    sentinel.touch()
    print(f"  Wrote {out_path}  (nhours={nhours}, dt_atmos={dt_atmos})")
    print(f"  Sentinel: {sentinel}")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate model_configure file for a given month.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--month",  required=True, help="YYYYMM")
    args = parser.parse_args()
    cfg = load_config(Path(args.config))
    if not gen_model_configure_month(cfg, args.month):
        sys.exit(1)
