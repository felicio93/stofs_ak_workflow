import re
import sys
from pathlib import Path
from calendar import monthrange

from workflow.core.config import load_config, model_dir

def gen_model_configure_month(cfg: dict, ym: str):
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    year = int(ym[:4])
    month = int(ym[4:])

    template_path = mdir / "fix" / "model_configure"
    if not template_path.exists():
        print(f"ERROR: Template file not found: {template_path}")
        sys.exit(1)

    _, ndays = monthrange(year, month)
    nhours = ndays * 24
    dt_atmos = cfg.get("dt_atmos", 720)

    out_path = mdir / f"I{pid}" / f"I{pid}_{ym}" / "model_configure"
    sentinel = out_path.parent / "gen_model_configure.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_model_configure: {ym} already complete. Skipping.")
        return

    print(f"--- gen_model_configure {ym} -> {out_path} ---")

    content = template_path.read_text()
    content = re.sub(r"^(start_year\s*:\s*).+$", r"\\1{}".format(year), content, flags=re.MULTILINE)
    content = re.sub(r"^(start_month\s*:\s*).+$", r"\\1{}".format(month), content, flags=re.MULTILINE)
    content = re.sub(r"^(start_day\s*:\s*).+$", r"\\11", content, flags=re.MULTILINE)
    content = re.sub(r"^(start_hour\s*:\s*).+$", r"\\10", content, flags=re.MULTILINE)
    content = re.sub(r"^(nhours_fcst\s*:\s*).+$", r"\\1{}".format(nhours), content, flags=re.MULTILINE)
    content = re.sub(r"^(dt_atmos\s*:\s*).+$", r"\\1{}".format(dt_atmos), content, flags=re.MULTILINE)

    out_path.write_text(content)
    sentinel.touch()

    print(f"  Wrote {out_path}")
    print(f"  Sentinel: {sentinel}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate model_configure file for a given month.")
    parser.add_argument("--config", required=True, help="Path to the config directory.")
    parser.add_argument("--month", required=True, help="Month in YYYYMM format.")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    gen_model_configure_month(config, args.month)
