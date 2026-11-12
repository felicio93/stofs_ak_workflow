"""
models/schism_mice/preprocess/gen_ice_hotstart.py
=================================================
Phase 3 — Initial sea ice for SCHISM's multi-class ice module from HYCOM.

With ihot_mice = 2 in the first group's mice.nml, SCHISM initialises the ice
from four 1-D node variables of hotstart.nc (icedrv_init.F90,
mice_hotstart_HYCOM_var):

    sic2   ice concentration [0-1]
    sih2   ice thickness [m]; SCHISM takes it as volume per unit area and
           puts all of it in the one thickness category matching sih2/sic2
    siu2   eastward ice velocity [m/s]
    siv2   northward ice velocity [m/s]

This step interpolates raw/hycom/ice/ice_<start>.nc (download_hycom_ice) to
the nodes of fix/hgrid.ll and adds the four variables to
I{ID}_{first}/hotstart.nc, made by gen_hotstart. Bilinear interpolation
inside HYCOM's ocean; a node next to HYCOM land, or outside the HYCOM box,
takes the value of the nearest HYCOM ocean cell. Where sic2 = 0 the other
three are set to 0.

Sentinel: I{ID}_{first}/gen_ice_hotstart.done. Re-running gen_hotstart
rewrites hotstart.nc, so move the sentinel aside and re-run this step.

The driver submits it as a SLURM job (after gen_hotstart when that job was
just submitted). Run by hand:
    python -m workflow.models.schism_mice.preprocess.gen_ice_hotstart \
        --config <config_dir>
"""

import argparse
import sys
from pathlib import Path

import numpy as np

from workflow.core.config import list_groups, load_config, model_dir

ICE_VARS = {"sic2": "sic", "sih2": "sih", "siu2": "siu", "siv2": "siv"}
ICE_ATTRS = {
    "sic2": ("sea ice concentration (HYCOM sic)", "1"),
    "sih2": ("sea ice thickness (HYCOM sih)", "m"),
    "siu2": ("eastward sea ice velocity (HYCOM siu)", "m/s"),
    "siv2": ("northward sea ice velocity (HYCOM siv)", "m/s"),
}


def _first_dir(cfg: dict) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"I{pid}" / f"I{pid}_{list_groups(cfg)[0]}"


def _read_nodes(hgrid_ll: Path):
    with open(hgrid_ll) as f:
        f.readline()
        ne, np_ = (int(v) for v in f.readline().split()[:2])
    import pandas as pd
    a = pd.read_csv(hgrid_ll, sep=r"\s+", skiprows=2, nrows=np_, header=None,
                    usecols=[1, 2], engine="c").to_numpy(dtype=np.float64)
    return a[:, 0], a[:, 1]


def _read_ice(path: Path):
    """lon (nx), lat (ny), {name: (ny, nx) float with NaN off the ocean}."""
    import netCDF4
    with netCDF4.Dataset(str(path)) as ds:
        lon = np.asarray(ds.variables["lon"][:], dtype=np.float64)
        lat = np.asarray(ds.variables["lat"][:], dtype=np.float64)
        fields = {}
        for v in ICE_VARS.values():
            var = ds.variables[v]
            if var.ndim == 3:
                if var.shape[0] != 1:
                    sys.exit(f"ERROR: {path.name}: {v} has {var.shape[0]} "
                             f"times; expected 1")
                a = var[0]
            else:
                a = var[:]
            fields[v] = np.ma.filled(np.ma.masked_invalid(a).astype(np.float64),
                                     np.nan)
    if np.any(np.diff(lat) < 0):
        lat = lat[::-1]
        fields = {k: a[::-1] for k, a in fields.items()}

    # HYCOM leaves sih/siu/siv missing (fill) where there is no ice while
    # sic is 0 there: the ocean is where sic is defined, and a missing
    # thickness or velocity on the ocean means 0.
    ocean = np.isfinite(fields["sic"])
    for v in ("sih", "siu", "siv"):
        gap = ocean & ~np.isfinite(fields[v])
        icy = gap & (fields["sic"] > 0)
        if icy.any():
            print(f"  WARNING: {v} missing on {int(icy.sum())} HYCOM cells "
                  f"with sic > 0; set to 0 there")
        fields[v][gap] = 0.0
        fields[v][~ocean] = np.nan
    return lon, lat, fields


def _same_lon_convention(xn: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Node longitudes in the convention of the HYCOM file."""
    x = xn.copy()
    if lon.max() > 180.0:
        x[x < 0] += 360.0
    elif lon.min() < 0.0:
        x[x > 180.0] -= 360.0
    return x


def interpolate(lon, lat, fields, xn, yn):
    """{name: values at the nodes}, plus the number of nodes filled from
    the nearest ocean cell."""
    from scipy.interpolate import RegularGridInterpolator
    from scipy.spatial import cKDTree

    x = _same_lon_convention(xn, lon)
    pts = np.column_stack([yn, x])
    ocean = np.isfinite(fields["sic"])
    if not ocean.any():
        sys.exit("ERROR: the HYCOM ice file has no ocean values")
    jj, ii = np.nonzero(ocean)
    clat = np.cos(np.deg2rad(lat[jj]))
    tree = cKDTree(np.column_stack([lon[ii] * clat, lat[jj]]))

    out, nearest = {}, None
    for name, src in ICE_VARS.items():
        f = RegularGridInterpolator((lat, lon), fields[src], method="linear",
                                    bounds_error=False, fill_value=np.nan)
        v = f(pts)
        bad = ~np.isfinite(v)
        if bad.any():
            _, k = tree.query(np.column_stack(
                [x[bad] * np.cos(np.deg2rad(yn[bad])), yn[bad]]))
            v[bad] = fields[src][jj[k], ii[k]]
        out[name] = v
        if nearest is None:
            nearest = int(bad.sum())

    out["sic2"] = np.clip(out["sic2"], 0.0, 1.0)
    out["sih2"] = np.maximum(out["sih2"], 0.0)
    no_ice = out["sic2"] <= 0.0
    for name in ("sih2", "siu2", "siv2"):
        out[name][no_ice] = 0.0
    for name, v in out.items():
        if not np.all(np.isfinite(v)):
            sys.exit(f"ERROR: {name} has {int((~np.isfinite(v)).sum())} "
                     f"non-finite values at the nodes; hotstart.nc not "
                     f"changed")
    return out, nearest


def write_hotstart(hot: Path, values: dict):
    import netCDF4
    with netCDF4.Dataset(str(hot), "a") as ds:
        if "node" not in ds.dimensions:
            sys.exit(f"ERROR: {hot} has no 'node' dimension")
        n = len(ds.dimensions["node"])
        for name, v in values.items():
            if v.size != n:
                sys.exit(f"ERROR: {name} has {v.size} values but {hot.name} "
                         f"has {n} nodes")
            var = (ds.variables[name] if name in ds.variables
                   else ds.createVariable(name, "f8", ("node",)))
            var.long_name, var.units = ICE_ATTRS[name]
            var[:] = v


def run_gen_ice_hotstart(cfg: dict) -> bool:
    from workflow.downloaders.hycom import ice_file

    idir = _first_dir(cfg)
    hot  = idir / "hotstart.nc"
    done = idir / "gen_ice_hotstart.done"
    src  = ice_file(cfg)
    ll   = model_dir(cfg) / "fix" / "hgrid.ll"

    print(f"\n{'='*60}")
    print(f"  gen_ice_hotstart: {src.name} -> {hot}")
    print(f"{'='*60}")
    if done.exists():
        print("  Already done (sentinel found). Skipping.")
        return True
    for p, step in ((src, "download_hycom_ice"), (hot, "gen_hotstart"),
                    (ll, "fix/hgrid.ll")):
        if not p.exists():
            print(f"  ERROR: {p} not found ({step}).")
            return False
    if not (idir / "gen_hotstart.done").exists():
        print("  ERROR: gen_hotstart has not completed "
              "(no gen_hotstart.done).")
        return False

    xn, yn = _read_nodes(ll)
    lon, lat, fields = _read_ice(src)
    values, nearest = interpolate(lon, lat, fields, xn, yn)
    write_hotstart(hot, values)

    sic, sih = values["sic2"], values["sih2"]
    ice = sic > 0.15
    print(f"  {xn.size} nodes; {nearest} filled from the nearest HYCOM "
          f"ocean cell (next to HYCOM land or outside its box)")
    print(f"  sic2: max {sic.max():.3f}; nodes with sic2 > 0.15: "
          f"{int(ice.sum())} ({100.0 * ice.mean():.1f} %)")
    if ice.any():
        print(f"  sih2 where sic2 > 0.15: mean {sih[ice].mean():.2f} m, "
              f"max {sih.max():.2f} m; ice speed max "
              f"{np.hypot(values['siu2'], values['siv2']).max():.2f} m/s")
    else:
        print("  NOTE: no ice above 15 % concentration at the start date.")
    done.touch()
    print(f"  Added sic2, sih2, siu2, siv2 to {hot.name}; wrote {done.name}")
    return True


def submit_gen_ice_hotstart(cfg: dict, config_dir: Path,
                            after_jobid: str = "") -> str:
    """Submit the step as a SLURM job (after `after_jobid`, the gen_hotstart
    job, when given). Returns the job ID, or '' when nothing was submitted."""
    from workflow.core.environment import env_python
    from workflow.core.slurm import (
        DEFAULT_ACCOUNT, DEFAULT_MAIL_USER, DEFAULT_PARTITION, SlurmSubmitter,
    )
    idir = _first_dir(cfg)
    if (idir / "gen_ice_hotstart.done").exists():
        print("  gen_ice_hotstart already complete (sentinel found). "
              "Skipping.")
        return ""
    mdir   = model_dir(cfg)
    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    slurm  = cfg.get("slurm", {})
    subs = {
        "JOBNAME":    f"icehot_M{cfg['project_id']}",
        "WORKDIR":    str(mdir),
        "ACCOUNT":    slurm.get("account",   DEFAULT_ACCOUNT),
        "PARTITION":  slurm.get("partition", DEFAULT_PARTITION),
        "MAILUSER":   slurm.get("mail_user", DEFAULT_MAIL_USER),
        "MEM":        slurm.get("gen_ice_hotstart_mem", "32G"),
        "WALLTIME":   slurm.get("gen_ice_hotstart_walltime", "00:30:00"),
        "LOGDIR":     str(logdir),
        "PY":         env_python(cfg, "gen_ice_hotstart"),
        "CONFIG_DIR": str(config_dir),
    }
    templates = Path(__file__).resolve().parents[1] / "templates" / "slurm"
    dep = f"afterok:{after_jobid}" if after_jobid else None
    out = SlurmSubmitter(templates).render_and_submit(
        "gen_ice_hotstart.sbatch", subs, logdir / "gen_ice_hotstart.sbatch",
        dependency=dep)
    print(f"  Submitted gen_ice_hotstart"
          + (f" (after gen_hotstart job {after_jobid})" if after_jobid else "")
          + f". Log: {logdir}/gen_ice_hotstart.out")
    return SlurmSubmitter.parse_jobid(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)
    ok = run_gen_ice_hotstart(load_config(Path(args.config)))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
