"""
models/ufs_schism_cice/preprocess/gen_ice_in.py
===============================================
CICE namelist per group: fix/ice_in (template) -> I{ID}_{group}/ice_in.

Filled in per group:
  year/month/day_init, sec_init   group start (00:00)
  runtype, restart, ice_ic,       first group: 'initial', ice free
  use_restart_time                (ice_initial: none) or from a CICE
                                  restart file (ice_initial: <path>);
                                  later groups: 'continue' from the restart
                                  the previous group wrote (ice.restart_file,
                                  chained by auto_hotstart)
  dt                              ice_dt
  histfreq, histfreq_n            ice_history_freq, ice_history_n
  nprocs                          size of ice_petlist_bounds
  nx_global, ny_global            the CICE grid (fix/cice/, gen_cice_grid)
  block_size_x/y                  ice_block_size_x/y
  restore_ice, sea_ice_time_bry,  ice_restore_bry, ice_bry_dir
  sea_ice_bry_dir, fyear_init

Under NUOPC, CICE takes the run type from ufs.configure (start_type startup
-> initial, continue -> continue); the namelist values here agree with it.

Sentinel: I{ID}_{group}/gen_ice_in.done
"""

import argparse
import re
import sys
from pathlib import Path

from workflow.core.config import (
    group_date_range, list_groups, load_config, model_dir,
)

_PH = re.compile(r"@\[([A-Z_0-9]+)\]")


def grid_size(cfg: dict):
    """(nx, ny) of the CICE grid in fix/cice/."""
    d = model_dir(cfg) / "fix" / "cice"
    sent = d / "gen_cice_grid.done"
    if sent.exists():
        vals = dict(l.split() for l in sent.read_text().splitlines()
                    if len(l.split()) == 2)
        if "nx" in vals and "ny" in vals:
            return int(vals["nx"]), int(vals["ny"])
    grid = d / "grid_cice.nc"
    if not grid.exists():
        raise SystemExit(f"ERROR: {grid} not found; run gen_cice_grid "
                         f"(--phase setup) first.")
    import netCDF4 as nc4
    with nc4.Dataset(str(grid)) as nc:
        ny, nx = nc.variables["tlat"].shape
    return nx, ny


def ice_tasks(cfg: dict) -> int:
    a, b = (int(v) for v in str(cfg["ice_petlist_bounds"]).split())
    return b - a + 1


def _initial(cfg: dict):
    """None (ice free) or the Path of the initial CICE restart."""
    v = str(cfg.get("ice_initial", "none") or "none").strip()
    if v.lower() in ("none", ""):
        return None
    return Path(v)


def render(cfg: dict, group_id: str, template: str) -> str:
    gstart, gend = group_date_range(cfg, group_id)
    first = group_id == list_groups(cfg)[0]
    nx, ny = grid_size(cfg)
    init = _initial(cfg) if first else None

    restore = bool(cfg.get("ice_restore_bry", False))
    bry_dir = str(cfg.get("ice_bry_dir", "") or "").strip()
    if restore:
        if not bry_dir:
            raise SystemExit("ERROR: ice_restore_bry is true but ice_bry_dir "
                             "is empty.")
        bry_dir = bry_dir.rstrip("/") + "/"

    hf = str(cfg.get("ice_history_freq", "d"))
    if hf not in ("1", "h", "d", "m", "y"):
        raise SystemExit(f"ERROR: ice_history_freq must be one of 1 h d m y, "
                         f"got {hf!r}")
    vals = {
        "YEAR_INIT":        gstart.year,
        "MONTH_INIT":       gstart.month,
        "DAY_INIT":         gstart.day,
        "SEC_INIT":         0,
        "ICE_DT":           int(cfg.get("ice_dt", 180)),
        "RUNTYPE":          "initial" if first else "continue",
        "ICE_IC":           str(init) if init else "none",
        "RESTART":          ".true." if (init or not first) else ".false.",
        "USE_RESTART_TIME": ".false." if first else ".true.",
        "HISTFREQ":         hf,
        "HISTFREQ_N":       int(cfg.get("ice_history_n", 1)),
        "RESTORE_ICE":      ".true." if restore else ".false.",
        "SEA_ICE_TIME_BRY": ".true." if restore else ".false.",
        "BRY_DIR":          bry_dir or "./",
        "FYEAR_INIT":       gstart.year,
        "NPROCS":           ice_tasks(cfg),
        "NX_GLOBAL":        nx,
        "NY_GLOBAL":        ny,
        "BLOCK_SIZE_X":     int(cfg.get("ice_block_size_x", 20)),
        "BLOCK_SIZE_Y":     int(cfg.get("ice_block_size_y", 20)),
    }
    out = _PH.sub(lambda m: str(vals[m.group(1)]) if m.group(1) in vals
                  else m.group(0), template)
    left = sorted(set(_PH.findall(out)))
    if left:
        raise SystemExit(f"ERROR: fix/ice_in has unknown placeholder(s): "
                         f"{', '.join(left)}")
    return out


def _checks(cfg: dict, group_id: str) -> list:
    """Warnings for one group (inputs the run will need)."""
    warns = []
    gstart, gend = group_date_range(cfg, group_id)
    first = group_id == list_groups(cfg)[0]
    init = _initial(cfg) if first else None
    if init and not init.exists():
        warns.append(f"ice_initial {init} does not exist")
    if cfg.get("ice_restore_bry", False):
        bry = Path(str(cfg.get("ice_bry_dir")))
        for yr in sorted({gstart.year, gend.year}):
            if not (bry / f"BRY_{yr}.nc").exists():
                warns.append(f"{bry}/BRY_{yr}.nc not found (boundary "
                             f"restoring)")
        if gend.year != gstart.year:
            warns.append("the group crosses 1 January: this CICE reads the "
                         f"boundary file of {gstart.year} for the whole run "
                         f"(fyear is set once at start-up), so the January "
                         f"days use January {gstart.year}")
    return warns


def gen_ice_in_group(cfg: dict, group_id: str) -> bool:
    pid = cfg["project_id"]
    mdir = model_dir(cfg)
    idir = mdir / f"I{pid}" / f"I{pid}_{group_id}"
    out = idir / "ice_in"
    sentinel = idir / "gen_ice_in.done"
    if sentinel.exists() and out.exists():
        print(f"  gen_ice_in: {group_id} already complete. Skipping.")
        return True
    tmpl = mdir / "fix" / "ice_in"
    if not tmpl.exists():
        print(f"ERROR: {tmpl} not found (starter copy: "
              f"templates/ufs_schism_cice/fix/ice_in).")
        return False
    idir.mkdir(parents=True, exist_ok=True)
    text = render(cfg, group_id, tmpl.read_text())
    for w in _checks(cfg, group_id):
        print(f"  WARNING {group_id}: {w}")
    out.write_text(text)
    sentinel.touch()
    first = group_id == list_groups(cfg)[0]
    how = ("initial, " + (f"from {_initial(cfg)}" if _initial(cfg)
                          else "ice free")) if first else "continue (restart)"
    print(f"  gen_ice_in: {out}  ({how})")
    return True


def run_gen_ice_in(cfg: dict) -> bool:
    ok = True
    for g in list_groups(cfg):
        ok = gen_ice_in_group(cfg, g) and ok
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--group", default=None)
    a = ap.parse_args(argv)
    cfg = load_config(Path(a.config))
    ok = (gen_ice_in_group(cfg, a.group) if a.group else run_gen_ice_in(cfg))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
