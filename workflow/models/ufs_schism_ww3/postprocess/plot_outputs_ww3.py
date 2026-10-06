"""
models/ufs_schism_ww3/postprocess/plot_outputs_ww3.py
======================================================
Phase 5 step "plot_outputs_ww3" — full-run WW3 field GIFs from
*.out_grd.ww3.nc files written by WW3 in the run directory root.

WW3 field output format
-----------------------
  File pattern : YYYYMMDD.HHMMSS.out_grd.ww3.nc  (one file per output step)
  Dimensions   : nx (nodes), ny=1, time=1, ne (elements), nn=3
  lon/lat      : shape (1, nx) — flatten to (nx,)
  nconn        : shape (ne, 3), 1-based node indices
  HS           : shape (1, 1, nx) — squeeze to (nx,)
  fill value   : -999.9

Variables plotted (configured via plot_outputs_ww3_vars in postprocess.yaml):
  HS    Significant wave height (m)
  T01   Mean wave period Tm0,1 (s)
  FP0   Peak frequency (s-1)  -> plotted as peak period Tp = 1/FP0
  THM   Mean wave direction (rad) -> converted to degrees
  CX    Mean current x-component (m/s)
  CY    Mean current y-component (m/s)
  UAX   Mean wind x-component (m/s)
  UAY   Mean wind y-component (m/s)
  WLV   Water level (m)

Two-stage SLURM design:
  Stage 1 (array): one task per *.out_grd.ww3.nc file
  Stage 2 (serial, afterok): assembles per-variable GIFs

Output: P{ID}/P{ID}_plot_outputs_ww3/
"""

import argparse
import gc
import sys
from pathlib import Path

import numpy as np

from workflow.core.config import (
    load_config,
    list_groups,
    model_dir,
)


# =============================================================================
# Default variable specs
# =============================================================================

DEFAULT_WW3_VARS = [
    {"var_name": "HS",  "label": "Significant Wave Height (m)",
     "cmap": "turbo",   "vmin": 0,    "vmax": 6,    "transform": None},
    {"var_name": "T01", "label": "Mean Wave Period Tm01 (s)",
     "cmap": "viridis", "vmin": 0,    "vmax": 20,   "transform": None},
    {"var_name": "THM", "label": "Mean Wave Direction (deg)",
     "cmap": "hsv",     "vmin": 0,    "vmax": 360,  "transform": "rad2deg"},
    {"var_name": "WLV", "label": "Water Level (m)",
     "cmap": "RdBu_r",  "vmin": -2,   "vmax": 2,    "transform": None},
    {"var_name": "UAX", "label": "Wind U (m/s)",
     "cmap": "RdBu_r",  "vmin": -25,  "vmax": 25,   "transform": None},
    {"var_name": "UAY", "label": "Wind V (m/s)",
     "cmap": "RdBu_r",  "vmin": -25,  "vmax": 25,   "transform": None},
]

FILL_THRESHOLD = -900.0


# =============================================================================
# Mesh loader (cached per run directory)
# =============================================================================

_mesh_cache = {}


def _load_ww3_mesh(nc_path: Path):
    """Load lon, lat, triangulation from a WW3 field file.

    Returns (lon, lat, triangulation).
    Cached by run directory so we only parse the mesh once per task.
    nconn is 1-based — subtract 1 for matplotlib.
    """
    import matplotlib.tri as mtri
    import netCDF4 as nc4

    cache_key = str(nc_path.parent)
    if cache_key in _mesh_cache:
        return _mesh_cache[cache_key]

    ds   = nc4.Dataset(str(nc_path))
    lon  = ds.variables["lon"][:].flatten().astype("float64")
    lat  = ds.variables["lat"][:].flatten().astype("float64")
    conn = ds.variables["nconn"][:].astype(np.int64) - 1  # 1-based -> 0-based
    ds.close()

    triang = mtri.Triangulation(lon, lat, conn)
    _mesh_cache[cache_key] = (lon, lat, triang)
    return lon, lat, triang


# =============================================================================
# Timestamp helpers
# =============================================================================

def _parse_ww3_timestamp(nc_path: Path) -> str:
    """Parse human-readable timestamp from WW3 filename.

    YYYYMMDD.HHMMSS.out_grd.ww3.nc -> 'YYYY-MM-DD HH:MM'
    """
    stem   = nc_path.name
    parts  = stem.split(".")
    date_s = parts[0]
    time_s = parts[1]
    return (f"{date_s[:4]}-{date_s[4:6]}-{date_s[6:8]} "
            f"{time_s[:2]}:{time_s[2:4]}")


def _parse_ww3_sortkey(nc_path: Path) -> str:
    """Return a sortable string key from the WW3 filename."""
    stem  = nc_path.name
    parts = stem.split(".")
    return parts[0] + parts[1]   # YYYYMMDDHHMMSS


def _in_date_range(cfg: dict, nc_path: Path) -> bool:
    start = cfg.get("plot_outputs_ww3_start")
    end   = cfg.get("plot_outputs_ww3_end")
    if not start and not end:
        return True
    key = _parse_ww3_sortkey(nc_path)   # YYYYMMDDHHMMSS
    if start and key < start.replace("-", "") + "000000":
        return False
    if end and key > end.replace("-", "") + "235959":
        return False
    return True


# =============================================================================
# Output paths
# =============================================================================

def _frames_dir(cfg: dict) -> Path:
    pid = cfg["project_id"]
    return (model_dir(cfg) / f"P{pid}"
            / f"P{pid}_plot_outputs_ww3" / "frames")


def _gif_dir(cfg: dict) -> Path:
    pid = cfg["project_id"]
    return model_dir(cfg) / f"P{pid}" / f"P{pid}_plot_outputs_ww3"


# =============================================================================
# Single-file frame renderer (called by each array task)
# =============================================================================

def frames_for_file(cfg: dict, nc_path: Path):
    """Render all configured variable frames for one WW3 field file."""
    import netCDF4 as nc4
    import matplotlib
    matplotlib.use("Agg")
    from workflow.core.plot_style import (
        make_frame_tripcolor,
        read_mesh_boundaries,
    )

    if not _in_date_range(cfg, nc_path):
        print(f"  [{nc_path.name}] outside date range, skipping.")
        return

    fdir = _frames_dir(cfg)
    fdir.mkdir(parents=True, exist_ok=True)

    mdir = model_dir(cfg)

    # Load mesh boundaries once (optional overlay)
    boundaries = None
    for hp in (mdir / "fix" / "hgrid.ll",
               mdir / "fix" / "hgrid.gr3"):
        if hp.exists():
            try:
                boundaries = read_mesh_boundaries(hp)
            except Exception:
                boundaries = None
            break

    lon, lat, triang = _load_ww3_mesh(nc_path)

    # Variable specs
    var_cfgs = cfg.get("plot_outputs_ww3_vars") or DEFAULT_WW3_VARS
    dpi      = int(cfg.get("plot_outputs_ww3_dpi", 150))

    # Timestamp string for title and filename
    ts_label = _parse_ww3_timestamp(nc_path)
    ts_file  = _parse_ww3_sortkey(nc_path)   # YYYYMMDDHHMMSS

    ds = nc4.Dataset(str(nc_path))

    for vc in var_cfgs:
        varname = vc["var_name"]
        if varname not in ds.variables:
            continue

        fp = fdir / f"{varname}__{ts_file}.jpg"
        if fp.exists():
            continue   # resume-safe: skip already-rendered frames

        raw = np.array(ds.variables[varname]).squeeze()
        # Replace fill values with NaN
        raw = np.where(raw < FILL_THRESHOLD, np.nan, raw)

        # Optional transform
        transform = vc.get("transform")
        if transform == "rad2deg":
            raw = np.rad2deg(raw)
        elif transform == "inv":
            with np.errstate(divide="ignore", invalid="ignore"):
                raw = np.where(raw > 0, 1.0 / raw, np.nan)

        vmin = vc.get("vmin")
        vmax = vc.get("vmax")
        if vmin is None:
            finite = raw[np.isfinite(raw)]
            vmin   = float(np.percentile(finite, 2))  if finite.size > 0 else 0.0
        if vmax is None:
            finite = raw[np.isfinite(raw)]
            vmax   = float(np.percentile(finite, 98)) if finite.size > 0 else 1.0

        label = vc.get("label", varname)
        cmap  = vc.get("cmap", "viridis")
        title = f"{label} — {ts_label}"

        make_frame_tripcolor(
            triang, raw,
            title=title,
            out_path=fp,
            cbar_label=label,
            cmap=cmap,
            vmin=vmin, vmax=vmax,
            dpi=dpi,
            quality=90,
            boundaries=boundaries,
            depth=None,
            isobaths=None,
        )

    ds.close()
    gc.collect()
    print(f"  [{nc_path.name}] frames written.")
    sys.stdout.flush()


# =============================================================================
# Stage 2 — GIF assembly
# =============================================================================

def assemble(cfg: dict):
    """Assemble per-variable GIFs from rendered frames."""
    from workflow.models.schism.postprocess.plot_common import assemble_gif

    fdir = _frames_dir(cfg)
    gdir = _gif_dir(cfg)
    gdir.mkdir(parents=True, exist_ok=True)

    fps         = int(cfg.get("plot_outputs_ww3_fps", 4))
    keep_frames = bool(cfg.get("keep_frames", True))

    var_cfgs = cfg.get("plot_outputs_ww3_vars") or DEFAULT_WW3_VARS
    made = 0
    for vc in var_cfgs:
        name   = vc["var_name"]
        frames = sorted(fdir.glob(f"{name}__*.jpg"))
        if not frames:
            print(f"  {name}: no frames found, skipping GIF.")
            continue
        gif = gdir / f"ww3_{name}.gif"
        assemble_gif(frames, gif, fps=fps,
                     keep_frames=keep_frames)
        made += 1

    if not keep_frames:
        try:
            fdir.rmdir()
        except OSError:
            pass

    # Touch sentinel so Stage 2 is not resubmitted
    (gdir / ".ww3_frames_done").touch()
    (gdir / "plot_outputs_ww3.done").touch()
    print(f"  plot_outputs_ww3 assembly complete: "
          f"{made} GIF(s) in {gdir}")


# =============================================================================
# CLI
# =============================================================================

def main():
    ap  = argparse.ArgumentParser(
        description="UFS-SCHISM+WW3 field output GIFs")
    sub = ap.add_subparsers(dest="stage", required=True)

    # Stage 1: single file (called by array task)
    pf = sub.add_parser("frames",
                        help="render frames for one WW3 file")
    pf.add_argument("--file", required=True,
                    help="Full path to *.out_grd.ww3.nc file")

    # Stage 2: assemble GIFs
    sub.add_parser("assemble",
                   help="assemble frames into GIFs")

    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg  = load_config(Path(args.config))

    if args.stage == "frames":
        frames_for_file(cfg, Path(args.file))
    elif args.stage == "assemble":
        assemble(cfg)


if __name__ == "__main__":
    main()
