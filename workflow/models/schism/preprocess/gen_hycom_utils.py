"""
models/schism/preprocess/gen_hycom_utils.py
===========================================
Steps F, G, H — Submit SLURM jobs for SCHISM Fortran preprocessing
utilities that consume the aggregated HYCOM group stacks.

  Step F: gen_hotstart  — single SLURM job, first group only
              gen_hot_from_hycom_0_noscaling.exe
              output: hotstart.nc

  Step G: gen_3Dth      — SLURM job array, one task per group
              gen_3Dth_from_hycom_noscaling.exe
              output: elev2D.th.nc  uv3D.th.nc  TEM_3D.th.nc  SAL_3D.th.nc

  Step H: gen_nudge     — SLURM job array, one task per group
              gen_nudge_from_hycom_noscaling.exe
              output: TEM_nu.nc  SAL_nu.nc

Key changes from the previous version
--------------------------------------
* gen_3Dth_from_nc.in and gen_nudge_from_nc.in are now written
  per-group into I{ID}_{group_id}/ rather than once into bin/.
  This is necessary because they encode the stack ceiling, which
  depends on the group length.
* gen_hot_from_nc.in is still read from bin/ (no ceiling inside it).
* All list_months() calls replaced with list_groups().
* stack_ceiling() and group_date_range() used instead of monthrange.

Longitude preflight
-------------------
The utilities do not convert longitudes: hgrid.ll and the HYCOM stack must
be on the same convention, otherwise every node falls "outside" the HYCOM
grid and the utilities silently fill constants.  Before submitting, each
step compares the longitudes of fix/hgrid.ll with the xlon axis of the first
available TS_1.nc and picks the executable:

  <name>.exe            mesh and HYCOM on the same convention (e.g. both 0-360)
  <name>_lonshift.exe   HYCOM 0-360 shifted to -180..180 to match the mesh

`hycom_lon_shift` (project config) is "auto" (default), true or false.
It stops before submitting if the mesh does not overlap the HYCOM grid.
"""

import sys
from pathlib import Path

import netCDF4 as nc4
import numpy as np

from workflow.core.config import (
    list_groups,
    model_dir,
    stack_ceiling,
    group_date_range,
)
from workflow.core.slurm import SlurmSubmitter, write_manifest
from workflow.core.slurm import (
    DEFAULT_ACCOUNT,
    DEFAULT_PARTITION,
    DEFAULT_MAIL_USER,
)

TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent / "templates" / "slurm"
)

REMINDERS = """
  REMINDER: _noscaling executables expect UNPACKED float data (ncpdq -U done
            at download). DO NOT use stock SCHISM executables.
  REMINDER: the executables do not convert longitudes; mesh and HYCOM must
            share a convention (checked below, see hycom_lon_shift).
"""


# =============================================================================
# .in file generators (per-group)
# =============================================================================

def _write_3dth_in(idir: Path, cfg: dict, group_id: str):
    """Write gen_3Dth_from_nc.in into the group input directory.

    The stack ceiling is group-specific, so this file must be written
    per-group rather than once into bin/.
    """
    ot  = float(cfg.get("outside_temp", 10.0))
    os_ = float(cfg.get("outside_sal",  0.0))
    dt  = float(cfg.get("hycom_dt",     86400.0))
    obs = cfg.get("open_boundaries", [1, 2])
    nob = len(obs)
    obs_str = " ".join(str(i) for i in obs)
    ceil    = stack_ceiling(cfg, group_id)

    content = (
        f"{ot} {os_}             "
        f"!T,S for nodes outside HYCOM grid\n"
        f"{dt}                   "
        f"!time step in .nc [sec]\n"
        f"{nob} {obs_str}        "
        f"!# of open bnds; list of IDs\n"
        f"{ceil}                 "
        f"!# of days needed (stack ceiling for this group)\n"
        f"1                      "
        f"!# of HYCOM stacks\n"
    )
    out = idir / "gen_3Dth_from_nc.in"
    out.write_text(content)
    return out


def _write_nudge_in(idir: Path, cfg: dict, group_id: str):
    """Write gen_nudge_from_nc.in into the group input directory."""
    ot  = float(cfg.get("outside_temp", 10.0))
    os_ = float(cfg.get("outside_sal",  0.0))
    dt  = float(cfg.get("hycom_dt",     86400.0))
    ns  = int(cfg.get("nudge_stride",   1))

    content = (
        f"0                      "
        f"!inu_or_surf (0=nudging output; 1=surface restore)\n"
        f"{ot} {os_}             "
        f"!T,S for nodes outside HYCOM grid\n"
        f"{dt} {ns}              "
        f"!time step in .nc [sec]; output stride\n"
        f"1                      "
        f"!# of nc files (stacks)\n"
    )
    out = idir / "gen_nudge_from_nc.in"
    out.write_text(content)
    return out


# =============================================================================
# Common SLURM substitutions
# =============================================================================

def _common_subs(cfg: dict, mdir: Path) -> dict:
    slurm = cfg.get("slurm", {})
    pid   = cfg["project_id"]
    return {
        "ACCOUNT":           slurm.get("account",           DEFAULT_ACCOUNT),
        "PARTITION":         slurm.get("partition",         DEFAULT_PARTITION),
        "MEM":               slurm.get("schism_mem",        "8G"),
        "WALLTIME":          slurm.get("schism_walltime",   "02:00:00"),
        "HOTSTART_MEM":      slurm.get("hotstart_mem",      "64G"),
        "HOTSTART_WALLTIME": slurm.get("hotstart_walltime", "04:00:00"),
        "MAILUSER":          slurm.get("mail_user",
                             DEFAULT_MAIL_USER),
        "FIXDIR":            str(mdir / "fix"),
        "BINDIR":            str(mdir / "bin"),
        "PID":               pid,
        "IBASEDIR":          str(mdir / f"I{pid}"),
    }


_MESH_LON_CACHE = {}


def _mesh_lons(path: Path):
    """Longitudes of every node in hgrid.ll (cached per file and mtime)."""
    key = (str(path), path.stat().st_mtime)
    if key not in _MESH_LON_CACHE:
        with open(path) as f:
            f.readline()
            npts = int(f.readline().split()[1])     # "ne np"
        _MESH_LON_CACHE[key] = np.loadtxt(
            path, skiprows=2, max_rows=npts, usecols=1)
    return _MESH_LON_CACHE[key]


def _lonshift_name(exe: str) -> str:
    """gen_3Dth_from_hycom_noscaling.exe -> ..._noscaling_lonshift.exe"""
    stem, dot, ext = exe.rpartition(".")
    return f"{stem}_lonshift.{ext}" if dot else f"{exe}_lonshift"


def _fraction_inside(lons, xlon) -> float:
    """Fraction of mesh nodes that fall inside the HYCOM longitude range."""
    return float(np.mean((lons >= xlon.min()) & (lons <= xlon.max())))


def _select_hycom_exe(cfg: dict, mdir: Path, groups: list, key: str) -> str:
    """Return the executable name for `key`, after the longitude preflight.

    Compares fix/hgrid.ll with the xlon axis of the first existing TS_1.nc
    and chooses between the plain executable and its _lonshift variant (see
    the module docstring).  Exits with an error if the mesh does not overlap
    the HYCOM grid or the chosen executable is missing from bin/.
    """
    base = cfg.get("executables", {}).get(key)
    if not base:
        print(f"  ERROR: executables.{key} not set in project.yaml")
        sys.exit(1)

    mode = cfg.get("hycom_lon_shift", "auto")
    if isinstance(mode, bool):                 # YAML turns yes/no into bool
        mode = "true" if mode else "false"
    mode = str(mode).lower()
    if mode not in ("auto", "true", "false"):
        print(f"  ERROR: hycom_lon_shift must be auto, true or false "
              f"(got {cfg.get('hycom_lon_shift')!r})")
        sys.exit(1)

    pid      = cfg["project_id"]
    hgrid_ll = mdir / "fix" / "hgrid.ll"
    if not hgrid_ll.exists():
        print(f"  ERROR: {hgrid_ll} not found (needed for the longitude check).")
        sys.exit(1)
    ts_files = [mdir / f"I{pid}" / f"I{pid}_{g}" / "TS_1.nc" for g in groups]
    ts_file  = next((f for f in ts_files if f.exists()), None)
    if ts_file is None:
        print("  ERROR: no TS_1.nc found in any group directory "
              "(run aggregate_hycom first).")
        sys.exit(1)

    mesh = _mesh_lons(hgrid_ll)
    with nc4.Dataset(ts_file) as ds:
        xlon = np.array(ds.variables["xlon"][:], dtype="f8")
    xshift   = np.where(xlon > 180.0, xlon - 360.0, xlon)
    shift_ok = bool(np.all(np.diff(xshift) > 0))   # shifted axis still usable
    f_plain  = _fraction_inside(mesh, xlon)
    f_shift  = _fraction_inside(mesh, xshift) if shift_ok else 0.0

    if mode == "auto":
        use_shift = shift_ok and f_shift > f_plain
    elif mode == "true":
        if not shift_ok:
            print("  ERROR: hycom_lon_shift is true but the shifted HYCOM "
                  "longitudes are not increasing (the grid crosses 180).")
            sys.exit(1)
        use_shift = True
    else:
        use_shift = False

    best = f_shift if use_shift else f_plain
    print(f"  Longitude check ({key}): mesh {mesh.min():.2f}..{mesh.max():.2f}, "
          f"HYCOM {xlon.min():.2f}..{xlon.max():.2f} ({ts_file.parent.name})")
    print(f"    mesh nodes inside HYCOM: unshifted {100*f_plain:.1f} %, "
          f"shifted {100*f_shift:.1f} %  -> hycom_lon_shift={mode}, "
          f"{'USING lonshift' if use_shift else 'no shift'}")
    if best == 0.0:
        print("  ERROR: no mesh node falls inside the HYCOM longitude range; "
              "the utilities would fill every node with constants.")
        sys.exit(1)
    if best < 1.0:
        print(f"  WARNING: {100*(1-best):.1f} % of mesh nodes are outside the "
              f"HYCOM extent and will get the constant outside_temp/outside_sal "
              f"(widen the download box; a mesh crossing 180 needs "
              f"lon_reference \"360\").")

    name = _lonshift_name(base) if use_shift else base
    if not (mdir / "bin" / name).exists():
        print(f"  ERROR: executable not found in M*/bin/: {name}")
        print("  Copy the compiled _noscaling executables into bin/ first.")
        sys.exit(1)
    return name


def _check_hot_in(bin_dir: Path):
    """Verify gen_hot_from_nc.in exists in bin/ (written by gen_estuary)."""
    f = bin_dir / "gen_hot_from_nc.in"
    if not f.exists():
        print("  ERROR: bin/gen_hot_from_nc.in not found.")
        print("  Run gen_estuary first (step gen_estuary in steps.yaml).")
        sys.exit(1)


# =============================================================================
# Step F — gen_hotstart (single job, first group only)
# =============================================================================

def submit_gen_hotstart(cfg: dict, config_dir: Path) -> str:
    """Submit gen_hotstart. Returns the job ID string, or '' if skipped."""
    print(REMINDERS)

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    groups = list_groups(cfg)
    first  = groups[0]

    exe = _select_hycom_exe(cfg, mdir, groups, "gen_hotstart")
    _check_hot_in(mdir / "bin")

    idir   = mdir / f"I{pid}" / f"I{pid}_{first}"
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    if (idir / "gen_hotstart.done").exists():
        print(f"  gen_hotstart already complete (sentinel found in "
              f"I{pid}_{first}). Skipping.")
        return ""

    subs = _common_subs(cfg, mdir)
    subs.update({
        "JOBNAME":      f"hotstart_M{pid}",
        "IDIR":         str(idir),
        "LOGDIR":       str(logdir),
        "GROUP":        first,
        "HOTSTART_EXE": exe,
    })

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_hotstart for group {first} ...")
    out = submitter.render_and_submit(
        "gen_hotstart.sbatch", subs,
        logdir / "gen_hotstart.sbatch")
    print(f"  Log: {logdir}/gen_hotstart.out")
    return SlurmSubmitter.parse_jobid(out)


# =============================================================================
# Step G — gen_3Dth (array, every group)
# =============================================================================

def submit_gen_3Dth(cfg: dict, config_dir: Path) -> str:
    """Submit gen_3Dth array. Returns the job ID string, or '' if skipped."""
    print(REMINDERS)

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    groups = list_groups(cfg)

    exe = _select_hycom_exe(cfg, mdir, groups, "gen_3Dth")

    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    # Write per-group .in files and filter pending groups
    pending = []
    for group_id in groups:
        idir = mdir / f"I{pid}" / f"I{pid}_{group_id}"
        idir.mkdir(parents=True, exist_ok=True)

        if (idir / "gen_3Dth.done").exists():
            print(f"  {group_id}: gen_3Dth already complete, skipping.")
            continue

        # Write the group-specific .in file
        in_file = _write_3dth_in(idir, cfg, group_id)
        ceil    = stack_ceiling(cfg, group_id)
        gstart, gend = group_date_range(cfg, group_id)
        print(f"  {group_id}: wrote {in_file.name}  "
              f"(start={gstart}, ceiling={ceil})")

        pending.append(group_id)

    if not pending:
        print("  All groups already complete. Nothing to submit.")
        return ""

    ngroups  = len(pending)
    manifest = write_manifest(
        pending, logdir / "gen_3Dth_groups.manifest")

    subs = _common_subs(cfg, mdir)
    subs.update({
        "JOBNAME":     f"gen3Dth_M{pid}",
        "WORKDIR":     str(mdir),
        "NGROUPS":     str(ngroups),
        "LOGDIR":      str(logdir),
        "MANIFEST":    str(manifest),
        "GEN3DTH_EXE": exe,
    })

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_3Dth array: {ngroups} group(s) "
          f"({pending[0]} -> {pending[-1]})")
    out = submitter.render_and_submit(
        "gen_3Dth.sbatch", subs,
        logdir / "gen_3Dth.sbatch")
    print(f"  Monitor: squeue -u $USER")
    print(f"  Logs: {logdir}/gen_3Dth_*.out")
    return SlurmSubmitter.parse_jobid(out)


# =============================================================================
# Step H — gen_nudge (array, every group)
# =============================================================================

def submit_gen_nudge(cfg: dict, config_dir: Path) -> str:
    """Submit gen_nudge array. Returns the job ID string, or '' if skipped."""
    print(REMINDERS)

    pid    = cfg["project_id"]
    mdir   = model_dir(cfg)
    groups = list_groups(cfg)

    exe = _select_hycom_exe(cfg, mdir, groups, "gen_nudge")

    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    # Write per-group .in files and filter pending groups
    pending = []
    for group_id in groups:
        idir = mdir / f"I{pid}" / f"I{pid}_{group_id}"
        idir.mkdir(parents=True, exist_ok=True)

        if (idir / "gen_nudge.done").exists():
            print(f"  {group_id}: gen_nudge already complete, skipping.")
            continue

        # Write the group-specific .in file
        in_file = _write_nudge_in(idir, cfg, group_id)
        print(f"  {group_id}: wrote {in_file.name}")

        pending.append(group_id)

    if not pending:
        print("  All groups already complete. Nothing to submit.")
        return ""

    ngroups  = len(pending)
    manifest = write_manifest(
        pending, logdir / "gen_nudge_groups.manifest")

    subs = _common_subs(cfg, mdir)
    subs.update({
        "JOBNAME":      f"gennudge_M{pid}",
        "WORKDIR":      str(mdir),
        "NGROUPS":      str(ngroups),
        "LOGDIR":       str(logdir),
        "MANIFEST":     str(manifest),
        "GENNUDGE_EXE": exe,
    })

    submitter = SlurmSubmitter(TEMPLATES_DIR)
    print(f"  Submitting gen_nudge array: {ngroups} group(s) "
          f"({pending[0]} -> {pending[-1]})")
    out = submitter.render_and_submit(
        "gen_nudge.sbatch", subs,
        logdir / "gen_nudge.sbatch")
    print(f"  Monitor: squeue -u $USER")
    print(f"  Logs: {logdir}/gen_nudge_*.out")
    return SlurmSubmitter.parse_jobid(out)
