"""
models/schism/preprocess/replicate_fix.py
=========================================
Step replicate_fix (interactive, once per project): carry the property files
of an existing project (replicate_from, e.g. M21) over to this project's new
mesh of the same domain.

Needs this project's own fix/hgrid.gr3, fix/hgrid.ll and fix/vgrid.in
(partition.prop is needed only at setup_run). The mesh's bathymetry is
expected to be in hgrid.gr3/hgrid.ll already (interpolated from the DEM when
the mesh was built); replicate_depth: true instead interpolates the depths of
the source mesh (originals kept as hgrid.gr3.orig / hgrid.ll.orig).

  continuous node fields   linear inside the source triangle containing the
                           node, nearest source node outside the source mesh
  watertype.gr3            nearest source node (values are classes)
  tvd.prop                 nearest source element centroid (0/1 flags)
  station.in, source_*.csv linked (mesh independent)
  param.nml, run_test,     copied when this project's file is missing or
  run_comb                 still the untouched starter template; review dt
                           (and nspool/ihfskip/nspool_sta) and the cores

estuary.gr3 is made from the new depths by gen_estuary; vgrid.in and
partition.prop are the user's. Files already in fix/ are never overwritten.
Nearest-point searches use points on the unit sphere (true distances, no
0-360/180 issue). The QA of every file goes to fix/replicate_fix.txt.
"""

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from workflow.core.config import model_dir

LINEAR_FIELDS = ["albedo.gr3", "diffmin.gr3", "diffmax.gr3", "rough.gr3",
                 "TEM_nudge.gr3", "SAL_nudge.gr3", "windrot_geo2proj.gr3",
                 "shapiro.gr3"]
NEAREST_FIELDS = ["watertype.gr3"]
LINKED = ["station.in", "source_glofas.csv", "source_schism.csv"]
JOB_FILES = ["param.nml", "run_test", "run_comb"]
EARTH_KM = 6371.0


# =============================================================================
# gr3 reading / writing
# =============================================================================

def read_gr3(path: Path):
    """dict(ne, np, x, y, z, tri (ne, 4) 1-based with -1 for triangles,
    eid, lines_head) of a gr3 file; pandas for speed."""
    import pandas as pd
    with open(path) as f:
        f.readline()
        ne, np_ = (int(v) for v in f.readline().split()[:2])
    nodes = pd.read_csv(path, sep=r"\s+", skiprows=2, nrows=np_, header=None,
                        usecols=[0, 1, 2, 3], engine="c").to_numpy()
    if not np.array_equal(nodes[:, 0].astype(np.int64),
                          np.arange(1, np_ + 1)):
        sys.exit(f"ERROR: {path}: node ids are not 1..{np_} in order")
    el = pd.read_csv(path, sep=r"\s+", skiprows=2 + np_, nrows=ne,
                     header=None, names=range(6), engine="c")
    nv = el[1].to_numpy(dtype=np.int64)
    conn = el[[2, 3, 4, 5]].fillna(-1).to_numpy(dtype=np.int64)
    conn[nv == 3, 3] = -1
    return {"ne": ne, "np": np_, "x": nodes[:, 1].astype("f8"),
            "y": nodes[:, 2].astype("f8"), "z": nodes[:, 3].astype("f8"),
            "conn": conn, "eid": el[0].to_numpy(dtype=np.int64)}


def read_values(path: Path) -> np.ndarray:
    """4th column of the node block of a gr3 (property files may have no
    element block)."""
    import pandas as pd
    with open(path) as f:
        f.readline()
        np_ = int(f.readline().split()[1])
    return pd.read_csv(path, sep=r"\s+", skiprows=2, nrows=np_, header=None,
                       usecols=[3], engine="c").to_numpy(dtype="f8")[:, 0]


def _write_gr3(path: Path, title: str, mesh: dict, values: np.ndarray,
               elem_lines_from: Path):
    """Write a property gr3 on `mesh` (node block from mesh x/y + values,
    element block copied verbatim from the mesh's hgrid)."""
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w") as out, open(elem_lines_from) as src:
        out.write(f"{title}\n{mesh['ne']} {mesh['np']}\n")
        for i in range(mesh["np"]):
            out.write(f"{i + 1} {mesh['x'][i]:.10f} {mesh['y'][i]:.10f} "
                      f"{values[i]:.10g}\n")
        for _ in range(2 + mesh["np"]):
            src.readline()
        for _ in range(mesh["ne"]):
            out.write(src.readline())
    os.replace(tmp, path)


def _rewrite_depth(path: Path, depth: np.ndarray):
    """Replace the 4th column of the node block, keep every other line."""
    tmp = path.parent / (path.name + ".tmp")
    with open(path) as src, open(tmp, "w") as out:
        out.write(src.readline())
        head = src.readline()
        out.write(head)
        np_ = int(head.split()[1])
        for i in range(np_):
            p = src.readline().split()
            out.write(f"{p[0]} {p[1]} {p[2]} {depth[i]:.10g}\n")
        shutil.copyfileobj(src, out)
    os.replace(tmp, path)


# =============================================================================
# geometry
# =============================================================================

def _xyz(lon, lat):
    lo, la = np.radians(lon), np.radians(lat)
    return np.column_stack((np.cos(la) * np.cos(lo),
                            np.cos(la) * np.sin(lo), np.sin(la)))


def _triangles(conn: np.ndarray) -> np.ndarray:
    """0-based triangles; quads split along 1-3."""
    tri = conn[:, :3] - 1
    quad = conn[:, 3] > 0
    if quad.any():
        q = conn[quad] - 1
        tri = np.vstack((tri, q[:, [0, 2, 3]]))
    return tri


def _centroids(mesh: dict):
    c = mesh["conn"]
    n = np.where(c[:, 3] > 0, 4, 3)
    idx = np.where(c > 0, c - 1, 0)
    x = np.where(c > 0, mesh["x"][idx], 0).sum(axis=1) / n
    y = np.where(c > 0, mesh["y"][idx], 0).sum(axis=1) / n
    return x, y


class Interpolator:
    """Linear interpolation on the source triangles, nearest-node fallback.

    For every target node the triangles around its nearest source nodes are
    tested (barycentric weights in lon/lat, local to the triangle); a node in
    no tested triangle is outside the source mesh and takes the nearest
    source node's value.
    """

    def __init__(self, src: dict, dst_x, dst_y, k: int = 4,
                 chunk: int = 50000):
        from scipy.spatial import cKDTree
        tri = _triangles(src["conn"])
        n = src["np"]
        # node -> triangles (CSR)
        flat = tri.ravel()
        order = np.argsort(flat, kind="stable")
        tri_of = (order // 3)
        start = np.searchsorted(flat[order], np.arange(n + 1))
        deg = np.diff(start)
        maxdeg = int(deg.max())
        adj = np.full((n, maxdeg), -1, dtype=np.int64)
        col = np.arange(len(flat)) - np.repeat(start[:-1], deg)
        adj[flat[order], col] = tri_of

        tree = cKDTree(_xyz(src["x"], src["y"]))
        dist, nn = tree.query(_xyz(dst_x, dst_y), k=k, workers=-1)
        self.dist_km = dist[:, 0] * EARTH_KM
        self.nearest = nn[:, 0]
        m = len(dst_x)
        self.tri = np.full(m, -1, dtype=np.int64)
        self.w = np.zeros((m, 3))
        sx, sy = src["x"], src["y"]
        for a in range(0, m, chunk):
            b = min(a + chunk, m)
            cand = adj[nn[a:b]].reshape(b - a, -1)          # (c, k*maxdeg)
            ok = cand >= 0
            t = np.where(ok, cand, 0)
            v = tri[t]                                        # (c, K, 3)
            x0, y0 = sx[v[..., 0]], sy[v[..., 0]]
            x1, y1 = sx[v[..., 1]], sy[v[..., 1]]
            x2, y2 = sx[v[..., 2]], sy[v[..., 2]]
            px = np.asarray(dst_x[a:b])[:, None]
            py = np.asarray(dst_y[a:b])[:, None]
            det = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            det = np.where(det == 0, np.nan, det)
            l0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / det
            l1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / det
            l2 = 1.0 - l0 - l1
            eps = -1e-9
            inside = ok & (l0 >= eps) & (l1 >= eps) & (l2 >= eps)
            hit = inside.any(axis=1)
            j = np.argmax(inside, axis=1)
            r = np.arange(b - a)
            self.tri[a:b] = np.where(hit, t[r, j], -1)
            self.w[a:b] = np.column_stack((l0[r, j], l1[r, j], l2[r, j]))
        self.trinodes = tri
        self.outside = int((self.tri < 0).sum())

    def linear(self, values: np.ndarray) -> np.ndarray:
        out = values[self.nearest].astype("f8")
        inn = self.tri >= 0
        v = self.trinodes[self.tri[inn]]
        out[inn] = (self.w[inn] * values[v]).sum(axis=1)
        return out

    def nearest_value(self, values: np.ndarray) -> np.ndarray:
        return values[self.nearest].astype("f8")


# =============================================================================
# the step
# =============================================================================

def _source_fix(cfg: dict) -> Path:
    src = cfg.get("replicate_from")
    if not src:
        sys.exit("ERROR: replicate_fix needs replicate_from in project.yaml "
                 "(the project to copy from, e.g. /work2/.../M21)")
    p = Path(str(src))
    fix = p / "fix" if (p / "fix").is_dir() else p
    if not (fix / "hgrid.ll").exists():
        sys.exit(f"ERROR: replicate_from: no hgrid.ll in {fix}")
    return fix


def _untouched_template(cfg: dict, name: str, path: Path) -> bool:
    from workflow.core.scaffold import _norm, render_template, template_file
    t = template_file(str(cfg.get("model_type", "schism")).lower(), name)
    if t is None:
        return False
    return _norm(path.read_text()) == _norm(render_template(
        t.read_text(), cfg.get("build")))


def _stats(a: np.ndarray) -> str:
    return f"min {np.nanmin(a):.6g}  mean {np.nanmean(a):.6g}  max {np.nanmax(a):.6g}"


def run_replicate_fix(cfg: dict):
    fix = model_dir(cfg) / "fix"
    src = _source_fix(cfg)
    if src.resolve() == fix.resolve():
        sys.exit("ERROR: replicate_from points to this project itself")
    for name in ("hgrid.gr3", "hgrid.ll", "vgrid.in"):
        if not (fix / name).exists():
            sys.exit(f"ERROR: fix/{name} is missing: put the new mesh "
                     f"(with its bathymetry) in {fix} first.")

    log = [f"replicate_fix {datetime.now():%Y-%m-%d %H:%M}  "
           f"source {src}  ->  {fix}"]

    def say(msg):
        print("  " + msg)
        log.append(msg)

    print(f"  replicate_fix: {src} -> {fix}")
    print("  Reading meshes ...")
    s_mesh = read_gr3(src / "hgrid.ll")
    d_mesh = read_gr3(fix / "hgrid.ll")
    say(f"source mesh: np={s_mesh['np']} ne={s_mesh['ne']}   "
        f"new mesh: np={d_mesh['np']} ne={d_mesh['ne']}")
    print("  Locating new nodes in the source mesh ...")
    itp = Interpolator(s_mesh, d_mesh["x"], d_mesh["y"])
    say(f"nodes: nearest source node {np.median(itp.dist_km):.3f} km median, "
        f"{itp.dist_km.max():.3f} km max; {itp.outside} node(s) outside the "
        f"source mesh (nearest value used)")
    if itp.dist_km.max() > 10:
        say("WARNING: some new nodes are > 10 km from any source node: "
            "are the two meshes on the same domain and lon convention?")

    # --- optional depth replication -----------------------------------------
    if cfg.get("replicate_depth", False):
        if (fix / "hgrid.gr3.orig").exists():
            say("hgrid depths: hgrid.gr3.orig exists, depths already "
                "replicated: kept")
        else:
            depth = itp.linear(s_mesh["z"])
            for name in ("hgrid.gr3", "hgrid.ll"):
                shutil.copy2(fix / name, fix / (name + ".orig"))
                _rewrite_depth(fix / name, depth)
            d = depth - d_mesh["z"]
            say(f"hgrid depths replaced by the source mesh's (linear); "
                f"new - original: {_stats(d)} m; originals kept as "
                f"hgrid.gr3.orig / hgrid.ll.orig")
            d_mesh["z"] = depth

    # --- node property files ---------------------------------------------------
    for name in LINEAR_FIELDS + NEAREST_FIELDS:
        out, srcf = fix / name, src / name
        if out.exists():
            say(f"{name}: exists, kept")
            continue
        if not srcf.exists():
            say(f"{name}: not in the source, skipped")
            continue
        vals = read_values(srcf)
        if len(vals) != s_mesh["np"]:
            say(f"WARNING {name}: {len(vals)} nodes, the source hgrid has "
                f"{s_mesh['np']}: skipped")
            continue
        new = (itp.nearest_value(vals) if name in NEAREST_FIELDS
               else itp.linear(vals))
        _write_gr3(out, f"{name} replicated from {src}", d_mesh, new,
                   fix / "hgrid.ll")
        how = "nearest" if name in NEAREST_FIELDS else "linear"
        say(f"{name}: {how}; source {_stats(vals)}; new {_stats(new)}")

    # --- tvd.prop --------------------------------------------------------------
    out, srcf = fix / "tvd.prop", src / "tvd.prop"
    if out.exists():
        say("tvd.prop: exists, kept")
    elif not srcf.exists():
        say("tvd.prop: not in the source, skipped")
    else:
        import pandas as pd
        from scipy.spatial import cKDTree
        t = pd.read_csv(srcf, sep=r"\s+", header=None, usecols=[0, 1],
                        engine="c").to_numpy(dtype=np.int64)
        if len(t) != s_mesh["ne"]:
            say(f"WARNING tvd.prop: {len(t)} lines, the source mesh has "
                f"{s_mesh['ne']} elements: skipped")
        else:
            flag = np.zeros(s_mesh["ne"], dtype=np.int64)
            flag[t[:, 0] - 1] = t[:, 1]
            sx, sy = _centroids(s_mesh)
            dx, dy = _centroids(d_mesh)
            dist, j = cKDTree(_xyz(sx, sy)).query(_xyz(dx, dy), workers=-1)
            new = flag[j]
            tmp = fix / "tvd.prop.tmp"
            with open(tmp, "w") as f:
                for e, v in zip(d_mesh["eid"], new):
                    f.write(f"{e} {v}\n")
            os.replace(tmp, out)
            say(f"tvd.prop: nearest element centroid "
                f"({np.median(dist) * EARTH_KM:.3f} km median, "
                f"{dist.max() * EARTH_KM:.3f} km max); TVD elements "
                f"{100 * flag.mean():.1f} % in the source, "
                f"{100 * new.mean():.1f} % in the new mesh")

    # --- mesh-independent files --------------------------------------------------
    for name in LINKED:
        out, srcf = fix / name, src / name
        if out.exists() or out.is_symlink():
            say(f"{name}: exists, kept")
        elif srcf.exists():
            out.symlink_to(srcf.resolve())
            say(f"{name}: linked to {srcf.resolve()}")
        else:
            say(f"{name}: not in the source, skipped")

    for name in JOB_FILES:
        out, srcf = fix / name, src / name
        if not srcf.exists():
            continue
        if out.exists() and not _untouched_template(cfg, name, out):
            say(f"{name}: edited in this project, kept")
            continue
        shutil.copy2(srcf, out)
        say(f"{name}: copied from the source (review it)")
        if name == "param.nml" and "\n  ihot = 2" in out.read_text():
            say("WARNING param.nml: ihot = 2 (the first group would get no "
                "station output); ihot = 1 is recommended")
    say("REVIEW: param.nml dt (and nspool/ihfskip/nspool_sta with it) for the "
        "new mesh, and run_test -N/-n against the new partition.prop.")

    with open(fix / "replicate_fix.txt", "a") as f:
        f.write("\n".join(log) + "\n\n")
    print(f"  replicate_fix complete; record in {fix / 'replicate_fix.txt'}")
