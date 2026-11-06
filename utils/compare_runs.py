#!/usr/bin/env python3
"""
utils/compare_runs.py
=====================
Side-by-side summary of several finished SCHISM runs (new I/O, out2d) on
the same area, e.g. the 2D Norton Sound runs M31-M34.

    maps        one GIF per variable (water level, depth-averaged U and V)
                with one panel per run (2 x 2 for four runs), for the whole
                mesh and for each plot_zooms box of the first run's
                postprocess.yaml. Dry elements (dryFlagElement) are blank.
    timeseries  water level at every CO-OPS WL station of the first run's
                station.in: the observation and one line per run, with
                bias / RMSE / R^2 in the legend, plus a skill CSV.

    python utils/compare_runs.py maps --root $R --runs 31 32 33 34 --out DIR
    python utils/compare_runs.py timeseries --root $R --runs 31 32 33 34 --out DIR

Run directories are found as $R/M{id}/R{id}/R{id}_*/outputs; observations
in $R/M{id}/obs/coops of the first run that has them. The project configs
are not loaded (only postprocess.yaml is read, for the zoom boxes, padding
and cadence), so this works for projects made before modelsetup.yaml.
The frames are drawn in parallel (--workers); run "maps" on a compute node.
"""

import argparse
import sys
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow.models.schism.postprocess import plot_common as pc  # noqa: E402

MAP_VARS = {
    "elevation":        ("Water level (m)", "elev"),
    "depthAverageVelX": ("Depth-avg velocity X (m/s)", "vel"),
    "depthAverageVelY": ("Depth-avg velocity Y (m/s)", "vel"),
}


# =============================================================================
# Runs
# =============================================================================

def _mdir(root: Path, pid: str) -> Path:
    return root / f"M{pid}"


def _run_dirs(root: Path, pid: str) -> list:
    return sorted(p for p in (_mdir(root, pid) / f"R{pid}").glob(f"R{pid}_*")
                  if (p / "outputs").is_dir())


def _postprocess_yaml(root: Path, pid: str) -> dict:
    import yaml
    f = _mdir(root, pid) / "config" / "postprocess.yaml"
    return (yaml.safe_load(f.read_text()) or {}) if f.exists() else {}


def _stack_times(root: Path, pid: str) -> dict:
    """{datetime64[s]: (out2d path, time index)} over all run groups; the
    first group wins where groups overlap."""
    import numpy as np
    import xarray as xr
    index = {}
    for rdir in _run_dirs(root, pid):
        for nc in pc.list_output_stacks(rdir / "outputs", "out2d"):
            with xr.open_dataset(str(nc), drop_variables=pc.SAFE_DROP) as ds:
                times = ds["time"].values.astype("datetime64[s]")
            for i, t in enumerate(times):
                index.setdefault(np.datetime64(t, "s"), (str(nc), i))
    return index


# =============================================================================
# maps
# =============================================================================

_W = {}   # per-worker state (meshes, open files), filled by _init_worker


def _extents(runs_mesh, zooms, pad):
    """[(name, [lon0, lon1, lat0, lat1])]: the mesh (+ padding) and zooms."""
    x0 = min(float(m["x"].min()) for m in runs_mesh)
    x1 = max(float(m["x"].max()) for m in runs_mesh)
    y0 = min(float(m["y"].min()) for m in runs_mesh)
    y1 = max(float(m["y"].max()) for m in runs_mesh)
    out = [("domain", [x0 - pad[0], x1 + pad[0], y0 - pad[1], y1 + pad[1]])]
    for z in zooms or []:
        lon, lat = z["lon"], z["lat"]
        out.append((str(z["name"]), [float(min(lon)), float(max(lon)),
                                     float(min(lat)), float(max(lat))]))
    return out


def _load_run_mesh(root, pid, first_nc):
    from workflow.core.plot_style import read_mesh_boundaries
    x, y, _, tri, is_tri = pc.load_mesh(Path(first_nc))
    bnd = None
    for name in ("hgrid.ll", "hgrid.gr3"):
        f = _mdir(root, pid) / "fix" / name
        if f.exists():
            try:
                bnd = read_mesh_boundaries(f)
            except Exception:
                bnd = None
            break
    return {"x": x, "y": y, "tris": tri.triangles, "is_tri": is_tri,
            "boundaries": bnd}


def _init_worker(root, pids, first_ncs, extents):
    import numpy as np
    import matplotlib.tri as mtri
    _W["meshes"] = [_load_run_mesh(root, p, f)
                    for p, f in zip(pids, first_ncs)]
    _W["files"] = {}
    # Per run and extent: the triangles drawn (those with a node inside the
    # box) and their Triangulation.
    _W["sub"] = {}
    for r, m in enumerate(_W["meshes"]):
        for name, (x0, x1, y0, y1) in extents:
            inside = ((m["x"] >= x0) & (m["x"] <= x1)
                      & (m["y"] >= y0) & (m["y"] <= y1))
            sel = np.nonzero(inside[m["tris"]].any(axis=1))[0]
            _W["sub"][(r, name)] = (sel, mtri.Triangulation(
                m["x"], m["y"], m["tris"][sel]))


def _read(path, var, idx):
    import numpy as np
    import netCDF4
    ds = _W["files"].get(path)
    if ds is None:
        ds = _W["files"][path] = netCDF4.Dataset(path)
    if var not in ds.variables:
        return None
    return np.ma.filled(ds.variables[var][idx], np.nan).astype("f8")


def _draw_frame(task, labels, extents, lims, dpi):
    """One JPEG: the panels of all runs for one variable, extent, time."""
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from workflow.core.plot_style import _draw_boundaries

    var, ext_name, t, locs, out_jpg = task
    if Path(out_jpg).exists():
        return out_jpg
    x0, x1, y0, y1 = dict(extents)[ext_name]
    n = len(locs)
    ncol = 2 if n > 1 else 1
    nrow = (n + ncol - 1) // ncol
    pw = 5.0
    ph = float(np.clip(pw * (y1 - y0) / max(x1 - x0, 1e-6), 1.8, 7.0))
    fig, axes = plt.subplots(nrow, ncol, figsize=(pw * ncol + 1.2, ph * nrow + 0.8),
                             constrained_layout=True, squeeze=False)
    label, kind = MAP_VARS[var]
    vmin, vmax = lims[kind]
    pcm = None
    for r, (path, idx) in enumerate(locs):
        ax = axes.flat[r]
        m = _W["meshes"][r]
        sel, tri = _W["sub"][(r, ext_name)]
        vals = _read(path, var, idx)
        if vals is not None and len(sel):
            dry = _read(path, "dryFlagElement", idx)
            mask = (pc.expand_elem_values(dry, m["is_tri"])[sel] > 0.5
                    if dry is not None else np.zeros(len(sel), bool))
            tri.set_mask(mask)
            pcm = ax.tripcolor(tri, vals[:len(m["x"])], shading="flat",
                               cmap="RdBu_r", vmin=vmin, vmax=vmax,
                               rasterized=True)
        else:
            ax.text(0.5, 0.5, f"{var} not in output", ha="center",
                    transform=ax.transAxes)
        _draw_boundaries(ax, m["boundaries"])
        ax.set_facecolor("0.85")
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_aspect("equal")
        ax.set_title(labels[r], fontsize=11, fontweight="bold")
        ax.tick_params(labelsize=8)
    for ax in list(axes.flat)[n:]:
        ax.set_visible(False)
    if pcm is not None:
        cb = fig.colorbar(pcm, ax=axes, location="right", shrink=0.85,
                          extend="both")
        cb.set_label(label, fontsize=10)
    stamp = np.datetime_as_string(np.datetime64(t, "m")).replace("T", " ")
    fig.suptitle(f"{label} - {stamp} UTC  (grey: dry or outside mesh)",
                 fontsize=12, fontweight="bold")
    fig.savefig(out_jpg, dpi=dpi, format="jpeg",
                pil_kwargs={"quality": 90})
    plt.close(fig)
    return out_jpg


def run_maps(a):
    import numpy as np
    from multiprocessing import get_context

    root, pids = Path(a.root), [str(p) for p in a.runs]
    labels = a.labels or [f"M{p}" for p in pids]
    pp = _postprocess_yaml(root, pids[0])
    every = a.every_hours or int(pp.get("plot_outputs_every_hours", 6) or 0)
    pad = pp.get("plot_padding_deg", [0.1, 0.1])
    pad = (float(pad), float(pad)) if isinstance(pad, (int, float)) \
        else (float(pad[0]), float(pad[1]))
    zooms = [] if a.no_zoom else pp.get("plot_zooms") or []

    print(f"Indexing out2d stacks of {', '.join('M' + p for p in pids)}...")
    index = [_stack_times(root, p) for p in pids]
    for p, ix in zip(pids, index):
        if not ix:
            sys.exit(f"ERROR: no out2d_*.nc under {_mdir(root, p)}/R{p}")
        print(f"  M{p}: {len(ix)} output times, "
              f"{min(ix)} .. {max(ix)}")
    times = sorted(set.intersection(*(set(ix) for ix in index)))
    if a.start:
        times = [t for t in times if t >= np.datetime64(a.start)]
    if a.end:
        times = [t for t in times
                 if t < np.datetime64(a.end) + np.timedelta64(1, "D")]
    if every > 0:
        times = [t for t in times
                 if (t - t.astype("datetime64[D]")) % np.timedelta64(every, "h")
                 == np.timedelta64(0, "s")]
    if not times:
        sys.exit("ERROR: the runs have no common output time in the window")
    print(f"  {len(times)} common times every {every} h: {times[0]} .. {times[-1]}")

    first_ncs = [index[r][times[0]][0] for r in range(len(pids))]
    meshes = [pc.load_mesh(Path(f)) for f in first_ncs]
    extents = _extents([{"x": m[0], "y": m[1]} for m in meshes], zooms, pad)
    lims = {"elev": (-a.elev_lim, a.elev_lim), "vel": (-a.vel_lim, a.vel_lim)}

    out = Path(a.out)
    tasks = []
    for var in MAP_VARS:
        for ext_name, _ in extents:
            fdir = out / "frames" / f"{var}_{ext_name}"
            fdir.mkdir(parents=True, exist_ok=True)
            for t in times:
                stamp = str(t).replace("-", "").replace(":", "").replace("T", "_")
                tasks.append((var, ext_name, t,
                              [ix[t] for ix in index],
                              str(fdir / f"{stamp}.jpg")))
    print(f"Drawing {len(tasks)} frames on {a.workers} worker(s) "
          f"({len(MAP_VARS)} variables x {len(extents)} extents: "
          f"{', '.join(e for e, _ in extents)})...")
    draw = partial(_draw_frame, labels=labels, extents=extents, lims=lims,
                   dpi=a.dpi)
    with get_context("fork").Pool(
            a.workers, initializer=_init_worker,
            initargs=(root, pids, first_ncs, extents)) as pool:
        for k, _ in enumerate(pool.imap_unordered(draw, tasks, chunksize=4), 1):
            if k % 100 == 0 or k == len(tasks):
                print(f"  {k}/{len(tasks)} frames", flush=True)

    for var in MAP_VARS:
        for ext_name, _ in extents:
            fdir = out / "frames" / f"{var}_{ext_name}"
            pc.assemble_gif(sorted(fdir.glob("*.jpg")),
                            out / f"{var}_{ext_name}.gif", fps=a.fps)


# =============================================================================
# timeseries
# =============================================================================

def _param_start(param_nml: Path):
    import re
    import pandas as pd
    txt = param_nml.read_text()
    get = {}
    for k in ("start_year", "start_month", "start_day", "start_hour"):
        m = re.search(rf"(?mi)^\s*{k}\s*=\s*([-\d.]+)", txt)
        get[k] = float(m.group(1)) if m else 0.0
    return (pd.Timestamp(int(get["start_year"]), int(get["start_month"]),
                         int(get["start_day"]))
            + pd.to_timedelta(get["start_hour"], unit="h"))


def _model_wl(root: Path, pid: str, column: int):
    """Water level (staout 1) of station.in line `column`, all groups."""
    import numpy as np
    import pandas as pd
    from workflow.models.schism.postprocess.station_skill import _read_staout
    parts = []
    for rdir in _run_dirs(root, pid):
        got = _read_staout(rdir / "outputs", 1)
        pn = rdir / "param.nml"
        if not pn.exists():
            gid = rdir.name.split("_", 1)[1]
            pn = _mdir(root, pid) / f"I{pid}" / f"I{pid}_{gid}" / "param.nml"
        if got is None or not pn.exists():
            continue
        secs, vals = got
        ok = ~np.isnan(secs)
        base = _param_start(pn)
        parts.append(pd.Series(vals[ok, column - 1],
                               index=base + pd.to_timedelta(secs[ok], unit="s")))
    if not parts:
        return None
    s = pd.concat(parts).sort_index()
    return s[~s.index.duplicated(keep="first")]


def _obs_wl(root: Path, pids: list, sid: str):
    import pandas as pd
    for pid in pids:
        files = sorted((_mdir(root, pid) / "obs" / "coops").glob(
            f"{sid}_water_level_*.csv"))
        parts = [pd.read_csv(f) for f in files if f.stat().st_size > 0]
        if not parts:
            continue
        df = pd.concat(parts, ignore_index=True)
        if "t" not in df.columns or "v" not in df.columns:
            continue
        s = pd.Series(pd.to_numeric(df["v"], errors="coerce").to_numpy(),
                      index=pd.to_datetime(df["t"], errors="coerce"))
        s = s[s.index.notna()].dropna().sort_index()
        s.index = s.index.tz_localize(None)
        return s[~s.index.duplicated(keep="first")], pid
    return None, None


def run_timeseries(a):
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from workflow.core.station_parser import parse_station_in
    from workflow.models.schism.postprocess.station_skill import _metrics

    root, pids = Path(a.root), [str(p) for p in a.runs]
    labels = a.labels or [f"M{p}" for p in pids]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sta = parse_station_in(_mdir(root, pids[0]) / "fix" / "station.in")
    wl = [s for s in sta if s["source"] == "CO-OPS" and s["depth"] == 0.0
          and {"WL", "ELEV"} & {v.upper() for v in s["vars"]}]
    if not wl:
        sys.exit("ERROR: no CO-OPS WL station (z=0) in the first run's "
                 "fix/station.in")
    colors = plt.get_cmap("tab10").colors
    rows = []
    for s in wl:
        obs, obs_pid = _obs_wl(root, pids, s["station_id"])
        mods = [_model_wl(root, p, s["line_index"]) for p in pids]
        if all(m is None for m in mods):
            print(f"  {s['station_id']} {s['name']}: no model output, skipped")
            continue
        t0 = pd.Timestamp(a.start) if a.start else min(
            m.index[0] for m in mods if m is not None)
        t1 = (pd.Timestamp(a.end) + pd.Timedelta(days=1)) if a.end else max(
            m.index[-1] for m in mods if m is not None)

        fig, ax = plt.subplots(figsize=(15, 4.5), constrained_layout=True)
        if obs is not None:
            o = obs.loc[t0:t1]
            ax.plot(o.index, o.values, color="k", lw=1.2, label="Observed")
        else:
            print(f"  {s['station_id']}: no CO-OPS water_level CSVs in any "
                  f"M*/obs/coops; model lines only")
        for k, (p, lab, m) in enumerate(zip(pids, labels, mods)):
            if m is None:
                continue
            m = m.loc[t0:t1]
            txt = lab
            if obs is not None:
                got = _metrics(obs.loc[t0:t1], m, a.resample)
                if got:
                    n, _, bias, rmse, r2 = got
                    txt += f"  (bias {bias:+.3f}, RMSE {rmse:.3f} m, R$^2$ {r2:.2f})"
                    rows.append({"station_id": s["station_id"],
                                 "name": s["name"], "run": f"M{p}",
                                 "label": lab, "n": n, "bias_m": bias,
                                 "rmse_m": rmse, "r2": r2})
            ax.plot(m.index, m.values, color=colors[k % 10], lw=0.9,
                    alpha=0.9, label=txt)
        ax.set_xlim(t0, t1)
        ax.set_ylabel("Water level (m)")
        ax.set_title(f"{s['name']} ({s['station_id']}): water level, "
                     f"observed vs models"
                     + (f"  [obs from M{obs_pid}]" if obs_pid else ""),
                     fontsize=11, fontweight="bold")
        ax.grid(True, ls="--", color="gray", lw=0.5)
        ax.legend(loc="upper left", fontsize=9, ncol=1)
        loc = mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(loc)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
        f = out / f"wl_{s['station_id']}.jpg"
        fig.savefig(f, dpi=150)
        plt.close(fig)
        print(f"  Written: {f}")
    if rows:
        f = out / "wl_skill.csv"
        pd.DataFrame(rows).to_csv(f, index=False, float_format="%.4f")
        print(f"  Written: {f}")


# =============================================================================
# CLI
# =============================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("maps", "timeseries"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True,
                       help="folder holding the M{id} projects")
        p.add_argument("--runs", nargs="+", required=True,
                       help="project ids, panel order (row by row)")
        p.add_argument("--labels", nargs="+",
                       help="one label per run (default M{id})")
        p.add_argument("--out", required=True, help="output folder")
        p.add_argument("--start", help="YYYY-MM-DD (default: whole run)")
        p.add_argument("--end", help="YYYY-MM-DD, inclusive")
    m = sub.choices["maps"]
    m.add_argument("--every-hours", type=int, default=None,
                   help="frame cadence (default plot_outputs_every_hours)")
    m.add_argument("--elev-lim", type=float, default=1.5,
                   help="water level colour range +- (m)")
    m.add_argument("--vel-lim", type=float, default=0.5,
                   help="velocity colour range +- (m/s)")
    m.add_argument("--no-zoom", action="store_true",
                   help="only the whole mesh, not the plot_zooms boxes")
    m.add_argument("--workers", type=int, default=8)
    m.add_argument("--dpi", type=int, default=110)
    m.add_argument("--fps", type=int, default=4)
    sub.choices["timeseries"].add_argument(
        "--resample", default="1h", help="interval for the skill metrics")
    a = ap.parse_args(argv)
    if a.labels and len(a.labels) != len(a.runs):
        ap.error("--labels needs one label per run")
    run_maps(a) if a.cmd == "maps" else run_timeseries(a)


if __name__ == "__main__":
    main()
