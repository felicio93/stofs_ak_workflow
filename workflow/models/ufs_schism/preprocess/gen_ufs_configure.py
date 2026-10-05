"""
models/ufs_schism/preprocess/gen_ufs_configure.py
==================================================
Generate ufs.configure file for one group.

Reads fix/ufs.configure as a template, substitutes coupling parameters
from ufs_schism.yaml (or ufs_schism_ww3.yaml) and the forecast length
from the already-generated model_configure, and writes
I{ID}/I{ID}_{group_id}/ufs.configure.

Supports both:
  - ATM + OCN          (model_type: ufs_schism)
  - ATM + OCN + WAV    (model_type: ufs_schism_ww3)

WAV component is included when cfg contains 'wav_model' key.

Restart configuration
---------------------
  Group 1 (first group): start_type = startup  (cold start)
  Groups 2+ :            start_type = continue (hotstart)
                         restart_n  = ndays * 24 (write restart at
                         end of each group so it is available for
                         the next group)

  restart_n is set to ndays*24 for ALL groups so the mediator always
  writes a restart file at the end of the group regardless of whether
  it is a cold or hot start. This ensures RESTART/ufs.cpld.cpl.r.*.nc
  is present for chaining.

Mediator PIO
------------
  med_pio_typename and med_pio_stride are read from config and written
  into MED_attributes. Using PNETCDF + stride=8 gives ~480 I/O tasks
  across 3834 total tasks, reducing mediator restart write time from
  ~60 min (4 tasks, NETCDF) to ~5 min (480 tasks, PNETCDF).

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

The runSeq coupling interval (@N) is set from coupling_dt in
ufs_schism.yaml (default 3600 seconds = hourly).

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
    get_group_ndays,
)


# =============================================================================
# Helpers
# =============================================================================

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


def _has_wav(cfg: dict) -> bool:
    """Return True if a WAV component is configured."""
    return bool(cfg.get("wav_model", "").strip())


def _is_first_group(cfg: dict, group_id: str) -> bool:
    """Return True if group_id is the first group."""
    groups = list_groups(cfg)
    return group_id == groups[0]


# =============================================================================
# ufs.configure builder
# =============================================================================

def _build_ufs_configure(cfg: dict,
                          nhours_fcst: int,
                          schism_dt: int,
                          group_id: str) -> str:
    """Build the full ufs.configure content from config values.

    Generates the file from scratch rather than substituting into the
    fix/ template. This avoids the fragile line-by-line regex approach
    and ensures the WAV block and mediator PIO are always consistent
    with the config.

    start_type is 'startup' for the first group and 'continue' for
    all subsequent groups.

    restart_n is set to ndays*24 for all groups so the mediator always
    writes a restart file at the end of each group.

    med_pio_typename and med_pio_stride are written into MED_attributes
    to enable parallel NetCDF restart writes (much faster than default).
    """
    coupling_dt      = int(cfg.get("coupling_dt", 3600))
    with_wav         = _has_wav(cfg)
    ndays            = get_group_ndays(cfg, group_id)
    is_first         = _is_first_group(cfg, group_id)

    # start_type: cold start for first group, continue for rest
    start_type       = "startup" if is_first else "continue"

    # restart_n: write mediator restart at end of every group
    restart_n        = ndays * 24

    # Mediator PIO settings
    med_pio_typename = cfg.get("med_pio_typename", "PNETCDF")
    med_pio_stride   = cfg.get("med_pio_stride",   8)

    # Component list
    if with_wav:
        comp_list = "ATM OCN WAV MED"
    else:
        comp_list = "ATM OCN MED"

    # MED attributes
    med_attrs = f"""\
  ATM_model = {cfg['atm_model']}
  OCN_model = {cfg['ocn_model']}"""
    if with_wav:
        med_attrs += f"\n  WAV_model = {cfg['wav_model']}"
    med_attrs += f"""
  history_n = 9999
  history_option = nhours
  history_ymd = -999
  coupling_mode = {cfg['cpl_mode']}
  pio_typename = {med_pio_typename}
  pio_stride = {med_pio_stride}"""

    # WAV block
    wav_block = ""
    if with_wav:
        wav_mesh     = cfg.get("wav_mesh_file", "scrip.nc")
        pio_typename = cfg.get("wav_pio_typename",  "pnetcdf")
        pio_numio    = cfg.get("wav_pio_numiotasks", 4)
        pio_stride   = cfg.get("wav_pio_stride",    -99)
        pio_rearr    = cfg.get("wav_pio_rearranger", "box")
        pio_root     = cfg.get("wav_pio_root",      -99)
        wav_omp      = cfg.get("wav_omp_num_threads", 1)
        wav_plist    = cfg.get("wav_petlist_bounds", "")

        wav_block = f"""
# WAV #
WAV_model:                      {cfg['wav_model']}
WAV_petlist_bounds:             {wav_plist}
WAV_omp_num_threads:            {wav_omp}
WAV_attributes::
  Verbosity = 0
  DumpFields = false
  ProfileMemory = false
  mesh_wav = {wav_mesh}
  multigrid = false
  user_histname = 'false'
  use_historync = 'true'
  use_restartnc = 'true'
  restart_from_binary = 'false'
  pio_typename = '{pio_typename}'
  pio_numiotasks = {pio_numio}
  pio_stride = {pio_stride}
  pio_rearranger = '{pio_rearr}'
  pio_root = {pio_root}
::
"""

    # runSeq
    if with_wav:
        run_seq = f"""\
runSeq::
@{coupling_dt}
  MED med_phases_prep_atm
  MED med_phases_prep_ocn_accum
  MED med_phases_prep_ocn_avg
  MED med_phases_prep_wav_accum
  MED med_phases_prep_wav_avg
  MED -> ATM :remapMethod=redist
  MED -> OCN :remapMethod=redist
  MED -> WAV :remapMethod=redist
  ATM
  OCN
  WAV
  ATM -> MED :remapMethod=redist
  OCN -> MED :remapMethod=redist
  WAV -> MED :remapMethod=redist
  MED med_phases_post_atm
  MED med_phases_post_ocn
  MED med_phases_post_wav
  MED med_phases_restart_write
@
::"""
    else:
        run_seq = f"""\
runSeq::
@{coupling_dt}
  MED med_phases_prep_atm
  MED med_phases_prep_ocn_accum
  MED med_phases_prep_ocn_avg
  MED -> ATM :remapMethod=redist
  MED -> OCN :remapMethod=redist
  ATM
  OCN
  ATM -> MED :remapMethod=redist
  OCN -> MED :remapMethod=redist
  MED med_phases_post_atm
  MED med_phases_post_ocn
  MED med_phases_restart_write
@
::"""

    # orb_iyear from start_date
    start_year = int(str(cfg.get("start_date", "2025-01-01"))[:4])

    content = f"""\
#############################################
####  NEMS Run-Time Configuration File  #####
#############################################

# ESMF #
logKindFlag:            ESMF_LOGKIND_MULTI
globalResourceControl:  true

# EARTH #
EARTH_component_list: {comp_list}
EARTH_attributes::
  Verbosity = 0
::

# MED #
MED_model:                      {cfg['med_model']}
MED_petlist_bounds:             {cfg['med_petlist_bounds']}
MED_omp_num_threads:            {cfg.get('med_omp_num_threads', 1)}
MED_attributes::
{med_attrs}
::

# ATM #
ATM_model:                      {cfg['atm_model']}
ATM_petlist_bounds:             {cfg['atm_petlist_bounds']}
ATM_omp_num_threads:            {cfg.get('atm_omp_num_threads', 1)}
ATM_attributes::
  Verbosity = 0
  DumpFields = false
  ProfileMemory = false
  OverwriteSlice = true
::

# OCN #
OCN_model:                      {cfg['ocn_model']}
OCN_petlist_bounds:             {cfg['ocn_petlist_bounds']}
OCN_omp_num_threads:            {cfg.get('ocn_omp_num_threads', 1)}
OCN_attributes::
  Verbosity = 0
  DumpFields = false
  ProfileMemory = false
  OverwriteSlice = true
  meshloc = element
  CouplingConfig = {cfg.get('coupling_config', 'none')}
::{wav_block}
# Run Sequence #
{run_seq}

ALLCOMP_attributes::
  ScalarFieldCount = 3
  ScalarFieldIdxGridNX = 1
  ScalarFieldIdxGridNY = 2
  ScalarFieldIdxNextSwCday = 3
  ScalarFieldName = cpl_scalars
  start_type = {start_type}
  restart_dir = RESTART/
  case_name = {cfg.get('case_name', 'ufs.cpld')}
  restart_n = {restart_n}
  restart_option = nhours
  restart_ymd = -999
  orb_eccen = 1.e36
  orb_iyear = {start_year}
  orb_iyear_align = {start_year}
  orb_mode = fixed_year
  orb_mvelp = 1.e36
  orb_obliq = 1.e36
  stop_n = {nhours_fcst}
  stop_option = nhours
  stop_ymd = -999
::
"""
    return content


# =============================================================================
# Per-group entry point
# =============================================================================

def gen_ufs_configure_group(cfg: dict,
                             group_id: str) -> bool:
    """Generate ufs.configure for one group.

    Returns True on success, False on failure.
    """
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

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
    schism_dt   = int(float(param_nml["dt"]))
    coupling_dt = int(cfg.get("coupling_dt", 3600))
    with_wav    = _has_wav(cfg)
    is_first    = _is_first_group(cfg, group_id)
    ndays       = get_group_ndays(cfg, group_id)
    restart_n   = ndays * 24

    # Mediator PIO settings
    med_pio_typename = cfg.get("med_pio_typename", "PNETCDF")
    med_pio_stride   = cfg.get("med_pio_stride",   8)

    if coupling_dt == schism_dt and schism_dt < 600:
        print(f"  WARNING: coupling_dt={coupling_dt}s "
              f"equals SCHISM dt={schism_dt}s.")
        print(f"  This causes ESMF field exchanges every "
              f"timestep — very slow.")
        print(f"  Consider setting coupling_dt: 3600.")

    out_path = (mdir / f"I{pid}" / f"I{pid}_{group_id}"
                / "ufs.configure")
    sentinel = out_path.parent / "gen_ufs_configure.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_ufs_configure: {group_id} already "
              f"complete. Skipping.")
        return True

    wav_str    = (f" + WAV({cfg.get('wav_model', '')})"
                  if with_wav else "")
    start_type = "startup" if is_first else "continue"

    print(f"--- gen_ufs_configure {group_id} -> "
          f"{out_path} ---")
    print(f"  Components: ATM({cfg['atm_model']}) "
          f"+ OCN({cfg['ocn_model']}){wav_str}")
    print(f"  SCHISM dt={schism_dt}s, "
          f"coupling_dt={coupling_dt}s, "
          f"stop_n={nhours_fcst}h")
    print(f"  start_type = {start_type} "
          f"({'first group' if is_first else 'hotstart'})")
    print(f"  restart_n  = {restart_n}h "
          f"(write mediator restart at end of group)")
    print(f"  med_pio    = {med_pio_typename} "
          f"stride={med_pio_stride}")
    if with_wav:
        print(f"  WAV petlist: "
              f"{cfg.get('wav_petlist_bounds', '?')}")
        print(f"  WAV mesh:    "
              f"{cfg.get('wav_mesh_file', 'scrip.nc')}")

    content = _build_ufs_configure(
        cfg, nhours_fcst, schism_dt, group_id)

    out_path.write_text(content)
    sentinel.touch()
    print(f"  Written: {out_path}")
    print(f"  Sentinel: {sentinel}")
    return True


# =============================================================================
# Batch entry point (all groups)
# =============================================================================

def run_gen_ufs_configure(cfg: dict):
    """Generate ufs.configure for every group."""
    groups   = list_groups(cfg)
    grouping = cfg.get("grouping", "monthly")
    with_wav = _has_wav(cfg)
    failed   = []

    # Mediator PIO settings for display
    med_pio_typename = cfg.get("med_pio_typename", "PNETCDF")
    med_pio_stride   = cfg.get("med_pio_stride",   8)

    print(f"\n{'='*60}")
    print(f"  gen_ufs_configure: {groups[0]} -> "
          f"{groups[-1]}  "
          f"({len(groups)} group(s), grouping={grouping})")
    print(f"  WAV component: "
          f"{'YES (' + cfg.get('wav_model','') + ')' if with_wav else 'NO'}")
    print(f"  Group 1: start_type=startup (cold start)")
    print(f"  Groups 2+: start_type=continue (hotstart)")
    print(f"  Mediator PIO: {med_pio_typename} "
          f"stride={med_pio_stride}")
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
