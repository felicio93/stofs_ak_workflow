"""
core/vgrid.py
=============
vgrid.in for ivcor = 1 (localized sigma, VQS from Dukhovskoy) from
fix/hgrid.gr3 and the vgrid: block of modelsetup.yaml (step gen_vgrid).

Port of SCHISM's gen_vqs programs (src/Utility/Pre-Processing/gen_vqs_1.f90,
the Alaska copy of it, and STOFS-3D-Atl-Setup/.../Vgrid/gen_vqs.f90/.py),
with every value read from the config instead of the source code:

    vgrid:
      dz_bot_min: 0.1           # minimum bottom layer thickness [m]
      a_vqs0: 0.0               # bias of the shallowest class (-1..1)
      theta_b: 0.0              # S-transformation bottom parameter (default
                                # for classes without their own 4th value)
      downward_fix: [2, 3]      # classes forced to step down (optional)
      classes:                  # master grids, depth increasing
        - [1.0, 2, 0.0001]      # [max depth m, levels, theta_f(, theta_b)]
        - ...
        - {depth: 1050.0, extend: 18, z: [-400, ..., -1050]}

Optional hybrid grid: a shallow: block gives nodes shallower than
shallow.depth their own classes (and optionally their own dz_bot_min,
a_vqs0, theta_b); every other node keeps the main classes exactly, level
for level. vgrid.in stores each node's levels, so the two need not come from
one class list:

    vgrid:
      ...main classes...
      shallow:
        depth: 3.0              # nodes with dp < 3 m (land included)
        classes:
          - [1.0, 3, 0.0001]
          - [3.0, 7, 0.0001]    # the last class must reach depth

A list class is an S-coordinate master grid. A dict class copies the levels
of class `extend` (1-based) and appends the fixed depths z below them; its
level count is the sum of the two.

Each node of depth dp <= the first class depth gets that class's levels
spread as quadratic sigma (a_vqs0). A deeper node interpolates between the
two classes whose depths bracket dp, keeping levels while they stay at least
dz_bot_min above the bottom; the bottom level is at -dp.

vgrid.in is written in the layout SCHISM reads since 11 Oct 2021 (ivcor,
nvrt, the bottom level index of every node, then one line per level with
the sigma of every node, -9 below the bottom), as change_vgrid.f90 makes
from the per-node layout of the older programs.

The arithmetic is double precision throughout (as the STOFS gen_vqs.py);
the Fortran programs round some constants to single precision, which can
move the 6th decimal of a sigma value by one.
"""

import sys
from pathlib import Path

import numpy as np

_CHUNK = 100_000          # nodes per vectorised block
_BELOW = -9.0             # sigma below the bottom (as the Fortran programs)


def _fail(msg: str):
    print(f"  ERROR (gen_vgrid): {msg}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Master grids
# ---------------------------------------------------------------------------

def _s_column(nv: int, depth: float, h1: float, theta_f: float,
              theta_b: float) -> np.ndarray:
    """z of the nv levels of an S-coordinate master grid (etal = 0)."""
    sigma = (np.arange(1, nv + 1) - 1.0) / (1.0 - nv)
    cs = ((1 - theta_b) * np.sinh(theta_f * sigma) / np.sinh(theta_f)
          + theta_b * (np.tanh(theta_f * (sigma + 0.5))
                       - np.tanh(theta_f * 0.5)) / 2 / np.tanh(theta_f * 0.5))
    return h1 * sigma + (depth - h1) * cs


def master_grid(vcfg: dict):
    """(hsm, nv, z_mas) from the vgrid: block; z_mas[k, m] with k = 0 the
    surface and rows beyond nv[m] unused."""
    classes = vcfg.get("classes")
    if not classes or len(classes) < 2:
        _fail("vgrid.classes needs at least 2 master grids")
    theta_b = float(vcfg.get("theta_b", 0.0))

    # S classes first; extended classes copy their source after
    # downward_fix (the order of the STOFS program).
    hsm, cols, extend = [], [], {}
    for i, c in enumerate(classes, start=1):
        if isinstance(c, dict):
            src = c.get("extend")
            if not (isinstance(src, int) and 1 <= src < i):
                _fail(f"class {i}: extend must be the number of an earlier "
                      f"class, got {src!r}")
            if isinstance(classes[src - 1], dict):
                _fail(f"class {i}: extend must name an S class, not class "
                      f"{src}, which extends another")
            z = np.asarray(c.get("z") or [], dtype=float)
            if z.size == 0:
                _fail(f"class {i}: extend needs a non-empty z list")
            hsm.append(float(c["depth"]))
            extend[i - 1] = (src - 1, z)
            cols.append(None)
        else:
            if not (isinstance(c, (list, tuple)) and len(c) in (3, 4)):
                _fail(f"class {i}: expected [max depth, levels, theta_f] "
                      f"(optionally a 4th value theta_b) or "
                      f"{{depth, extend, z}}, got {c!r}")
            depth, levels, theta_f = float(c[0]), int(c[1]), float(c[2])
            cb = float(c[3]) if len(c) == 4 else theta_b
            if not 0.0 <= cb <= 1.0:
                _fail(f"class {i}: theta_b must be in 0..1, got {cb}")
            if levels < 2:
                _fail(f"class {i}: levels must be >= 2, got {levels}")
            if theta_f <= 0:
                _fail(f"class {i}: theta_f must be > 0, got {theta_f}")
            h1 = depth if i == 1 else hsm[0]
            hsm.append(depth)
            cols.append(_s_column(levels, depth, h1, theta_f, cb))
    nv = [len(col) if col is not None else 0 for col in cols]
    for j, (src, z) in extend.items():
        nv[j] = nv[src] + z.size

    hsm = np.array(hsm)
    nv = np.array(nv)
    if hsm[0] < 0:
        _fail(f"the first class depth must be >= 0, got {hsm[0]}")
    bad = np.flatnonzero(np.diff(hsm) <= 0)
    if bad.size:
        _fail(f"class depths must increase: class {bad[0] + 2} "
              f"({hsm[bad[0] + 1]}) <= class {bad[0] + 1} ({hsm[bad[0]]})")

    z_mas = np.full((nv.max(), len(hsm)), -1.0e5)
    for m, col in enumerate(cols):
        if col is not None:
            z_mas[:len(col), m] = col

    for m in vcfg.get("downward_fix") or []:
        if not (isinstance(m, int) and 2 <= m <= len(hsm)):
            _fail(f"downward_fix: {m!r} is not a class number from 2 to "
                  f"{len(hsm)}")
        j, p = m - 1, m - 2
        if j in extend or p in extend:
            _fail(f"downward_fix: class {m} and class {m - 1} must both be "
                  f"S classes")
        if nv[j] <= nv[p]:
            _fail(f"downward_fix: class {m} needs more levels than class "
                  f"{m - 1} ({nv[j]} <= {nv[p]})")
        z_mas[:nv[p], j] = np.minimum(z_mas[:nv[p], j], z_mas[:nv[p], p])
        step = (z_mas[nv[j] - 1, j] - z_mas[nv[p] - 1, j]) / (nv[j] - nv[p])
        for k in range(nv[p], nv[j]):
            z_mas[k, j] = z_mas[k - 1, j] + step

    for j, (src, z) in extend.items():
        z_mas[:nv[src], j] = z_mas[:nv[src], src]
        z_mas[nv[src]:nv[j], j] = z

    for m in range(len(hsm)):
        col = z_mas[:nv[m], m]
        if np.any(np.diff(col) >= 0):
            k = int(np.flatnonzero(np.diff(col) >= 0)[0]) + 2
            _fail(f"class {m + 1}: levels must decrease from the surface; "
                  f"level {k} ({col[k - 1]:.4f}) is not below level {k - 1} "
                  f"({col[k - 2]:.4f})")
    return hsm, nv, z_mas


# ---------------------------------------------------------------------------
# Per-node levels
# ---------------------------------------------------------------------------

def node_sigma(dp: np.ndarray, vcfg: dict, hsm, nv, z_mas):
    """(kbp, sigma): number of levels of each node and sigma[k, i] with k = 0
    the surface (0) and k = kbp-1 the bottom (-1), _BELOW further down."""
    dz_bot_min = float(vcfg.get("dz_bot_min", 0.5))
    a_vqs0 = float(vcfg.get("a_vqs0", 0.0))
    if dp.max() > hsm[-1]:
        i = int(np.argmax(dp))
        _fail(f"the deepest node ({i + 1}, {dp[i]:.2f} m) is deeper than the "
              f"last class ({hsm[-1]} m); add a deeper class")

    nvrt_m = int(nv.max())
    # Column of class m with rows past its bottom repeating the bottom
    # (the Fortran z_mas(min(k, nv(m-1)), m-1)).
    zpad = z_mas.copy()
    for m in range(len(hsm)):
        zpad[nv[m]:, m] = z_mas[nv[m] - 1, m]
    kidx = np.arange(nvrt_m)

    kbp = np.empty(dp.size, dtype=np.int64)
    sigma = np.full((nvrt_m, dp.size), _BELOW)

    # Shallow nodes: the first class as quadratic sigma.
    shallow = dp <= hsm[0]
    n0 = int(nv[0])
    s = (np.arange(1, n0 + 1) - 1.0) / (1.0 - n0)
    kbp[shallow] = n0
    sigma[:n0, shallow] = (a_vqs0 * s * s + (1 + a_vqs0) * s + 0.0)[:, None]

    deep_ids = np.flatnonzero(~shallow)
    for a in range(0, deep_ids.size, _CHUNK):
        ids = deep_ids[a:a + _CHUNK]
        d = dp[ids]
        m = np.searchsorted(hsm, d, side="left")      # hsm[m-1] < d <= hsm[m]
        zrat = (d - hsm[m - 1]) / (hsm[m] - hsm[m - 1])
        z1 = zpad[:, m - 1]
        z2 = z_mas[:, m]
        z3 = z1 + (z2 - z1) * zrat
        valid = kidx[:, None] < nv[m][None, :]
        keep = (z3 >= -d + dz_bot_min) & valid
        stop = ~keep & valid
        has = stop.any(axis=0)
        if not has.all():
            i = int(ids[np.flatnonzero(~has)[0]])
            _fail(f"no bottom found for node {i + 1} (depth {dp[i]:.2f} m): "
                  f"every level of its classes is more than dz_bot_min above "
                  f"the bottom")
        kb = np.argmax(stop, axis=0) + 1               # 1-based bottom level
        rows = kidx[:, None]
        z = np.where(rows < (kb - 1)[None, :], z3, np.nan)
        z[kb - 1, np.arange(ids.size)] = -d
        dz = np.diff(z, axis=0)
        inside = rows[1:] < kb[None, :]
        if np.any((dz >= 0) & inside):
            j = int(np.flatnonzero(((dz >= 0) & inside).any(axis=0))[0])
            _fail(f"inverted levels at node {ids[j] + 1} (depth "
                  f"{d[j]:.2f} m, class {m[j] + 1}); check the classes and "
                  f"dz_bot_min")
        sg = np.where(rows < kb[None, :], z / d[None, :], _BELOW)
        sg[0, :] = 0.0
        sg[kb - 1, np.arange(ids.size)] = -1.0
        kbp[ids] = kb
        sigma[:, ids] = sg

    if kbp.min() < 2:
        i = int(np.argmin(kbp))
        _fail(f"node {i + 1} (depth {dp[i]:.2f} m) has fewer than 2 levels; "
              f"check dz_bot_min and the shallow classes")
    return kbp, sigma


def _sub_cfg(vcfg: dict) -> dict:
    """The shallow: block as a stand-alone vgrid config (inherits the main
    dz_bot_min, a_vqs0 and theta_b unless it sets its own)."""
    sh = vcfg["shallow"]
    if not isinstance(sh, dict) or "depth" not in sh or "classes" not in sh:
        _fail("vgrid.shallow needs depth and classes")
    sub = {k: sh.get(k, vcfg.get(k)) for k in ("dz_bot_min", "a_vqs0",
                                              "theta_b")}
    sub = {k: v for k, v in sub.items() if v is not None}
    sub["classes"] = sh["classes"]
    sub["downward_fix"] = sh.get("downward_fix", [])
    return sub


def compute_grid(dp: np.ndarray, vcfg: dict):
    """(kbp, sigma, info) for every node, with the shallow: block applied
    when present. info = dict(hsm, nv, z_mas, n_shallow, shallow_depth)."""
    hsm, nv, z_mas = master_grid(vcfg)
    info = dict(hsm=hsm, nv=nv, z_mas=z_mas, n_shallow=0, shallow_depth=None)
    sh = vcfg.get("shallow")
    if not sh:
        kbp, sigma = node_sigma(dp, vcfg, hsm, nv, z_mas)
        return kbp, sigma, info
    sub = _sub_cfg(vcfg)
    depth = float(sh["depth"])
    shs, snv, sz = master_grid(sub)
    if shs[-1] < depth:
        _fail(f"vgrid.shallow: the last shallow class ({shs[-1]:g} m) must "
              f"reach shallow.depth ({depth:g} m)")
    shallow = dp < depth
    deep = ~shallow
    nrow = int(max(nv.max(), snv.max()))
    kbp = np.empty(dp.size, dtype=np.int64)
    sigma = np.full((nrow, dp.size), _BELOW)
    if deep.any():
        k1, s1 = node_sigma(dp[deep], vcfg, hsm, nv, z_mas)
        kbp[deep] = k1
        sigma[:s1.shape[0], deep] = s1
    if shallow.any():
        k2, s2 = node_sigma(dp[shallow], sub, shs, snv, sz)
        kbp[shallow] = k2
        sigma[:s2.shape[0], shallow] = s2
    info.update(n_shallow=int(shallow.sum()), shallow_depth=depth)
    return kbp, sigma, info


_BANDS = [(-1e9, 0.0, "land (dp<=0)"), (0.0, 1.0, "0-1 m"), (1.0, 3.0, "1-3 m"),
          (3.0, 10.0, "3-10 m"), (10.0, 50.0, "10-50 m"),
          (50.0, 200.0, "50-200 m"), (200.0, 1000.0, "200-1000 m"),
          (1000.0, 1e9, ">1000 m")]


def grid_diagnostics(dp, kbp, sigma, conn, chunk=400_000) -> dict:
    """Quality and cost numbers of a vertical grid on a mesh.

    prisms           sum over elements of their level count (as gen_vqs)
    jump             largest difference in layers between the nodes of one
                     element (max, 99th percentile, elements with > 5)
    rx1              hydrostatic consistency (Haney): for each side and each
                     pair of levels present at both its nodes,
                     |dz_k + dz_k+1| / (layer thickness at node a + at b),
                     dz = level depth difference between the two nodes;
                     the max over levels per side, then max / 99.9 / 99th
                     percentile over the sides (only wet sides, dp > 0)
    thinnest         thinnest layer of any node at least 1 m deep (and the
                     1st percentile)
    shelf_top/bottom median surface / bottom layer thickness, 20-200 m
    bands            node count and mean layers per depth band
    """
    layers = kbp - 1
    tri = conn[:, :3] - 1
    quad = conn[:, 3] > 0
    k = np.where(conn > 0, kbp[np.maximum(conn, 1) - 1], 0)
    prisms = int(k.max(axis=1).sum())
    kmin = np.where(conn > 0, k, 10**9).min(axis=1)
    jump = k.max(axis=1) - kmin

    # sides (unique node pairs)
    e = [tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]
    if quad.any():
        q = conn[quad] - 1
        e = [tri[~quad][:, [0, 1]], tri[~quad][:, [1, 2]], tri[~quad][:, [2, 0]],
             q[:, [0, 1]], q[:, [1, 2]], q[:, [2, 3]], q[:, [3, 0]]]
    sides = np.unique(np.sort(np.concatenate(e), axis=1), axis=0)
    wet = (dp[sides[:, 0]] > 0) & (dp[sides[:, 1]] > 0)
    sides = sides[wet]
    nrow = sigma.shape[0]
    rows = np.arange(nrow - 1)[:, None]
    rx = np.zeros(len(sides))
    for a0 in range(0, len(sides), chunk):
        sa, sb = sides[a0:a0 + chunk, 0], sides[a0:a0 + chunk, 1]
        za = sigma[:, sa] * dp[sa]
        zb = sigma[:, sb] * dp[sb]
        ok = (rows + 1 < np.minimum(kbp[sa], kbp[sb])[None, :])
        num = np.abs((za[:-1] - zb[:-1]) + (za[1:] - zb[1:]))
        den = (za[:-1] - za[1:]) + (zb[:-1] - zb[1:])
        r = np.where(ok & (den > 0), num / np.where(den > 0, den, 1.0), 0.0)
        rx[a0:a0 + chunk] = r.max(axis=0)

    wetn = np.flatnonzero(dp >= 1.0)    # thin layers of nodes < 1 m deep are
                                        # unavoidable (sigma) and not counted
    thin = np.full(dp.size, np.inf)
    top = np.full(dp.size, np.nan)
    bot = np.full(dp.size, np.nan)
    for a0 in range(0, wetn.size, chunk):
        ids = wetn[a0:a0 + chunk]
        z = sigma[:, ids] * dp[ids]
        valid = np.arange(nrow)[:, None] < kbp[ids][None, :]
        dz = np.where(valid[1:], z[:-1] - z[1:], np.inf)
        thin[ids] = dz.min(axis=0)
        top[ids] = dz[0]
        bot[ids] = (z[kbp[ids] - 2, np.arange(ids.size)]
                    - z[kbp[ids] - 1, np.arange(ids.size)])
    shelf = (dp >= 20) & (dp <= 200)
    bands = []
    for lo, hi, name in _BANDS:
        m = (dp > lo) & (dp <= hi)
        bands.append((name, int(m.sum()),
                      float(layers[m].mean()) if m.any() else float("nan")))
    th = thin[np.isfinite(thin)]
    return dict(
        nvrt=int(kbp.max()), prisms=prisms, avg_levels=prisms / len(conn),
        jump_max=int(jump.max()), jump_p99=float(np.percentile(jump, 99)),
        jump_gt5=int(np.sum(jump > 5)),
        rx1_max=float(rx.max()) if rx.size else 0.0,
        rx1_p999=float(np.percentile(rx, 99.9)) if rx.size else 0.0,
        rx1_p99=float(np.percentile(rx, 99)) if rx.size else 0.0,
        thinnest=float(th.min()) if th.size else float("nan"),
        thin_p01=float(np.percentile(th, 1)) if th.size else float("nan"),
        shelf_top=float(np.nanmedian(top[shelf])) if shelf.any() else float("nan"),
        shelf_bottom=float(np.nanmedian(bot[shelf])) if shelf.any() else float("nan"),
        bands=bands)


def format_diagnostics(d: dict, ref_prisms=None) -> str:
    cost = (f"  ({d['prisms'] / ref_prisms:.2f} x reference)"
            if ref_prisms else "")
    lines = [
        f"nvrt = {d['nvrt']}   # of prisms = {d['prisms']}{cost}   "
        f"average # of levels = {d['avg_levels']:.2f}",
        f"layer jump within an element: max {d['jump_max']}, 99th pct "
        f"{d['jump_p99']:.0f}, elements with > 5: {d['jump_gt5']}",
        f"hydrostatic consistency rx1 (wet sides): max {d['rx1_max']:.2f}, "
        f"99.9th pct {d['rx1_p999']:.2f}, 99th pct {d['rx1_p99']:.2f}",
        f"thinnest layer (nodes >= 1 m): {d['thinnest']:.3f} m (1st pct "
        f"{d['thin_p01']:.3f} m)"
        f";  shelf 20-200 m median top {d['shelf_top']:.2f} m, bottom "
        f"{d['shelf_bottom']:.2f} m",
        "layers per depth band:  " + "   ".join(
            f"{n}: {c} nodes, {m:.1f}" for n, c, m in d["bands"] if c)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def read_hgrid(path: Path):
    """(x, y, dp, i34, conn) of hgrid.gr3; conn is (ne, 4) 1-based with 0
    in column 4 for triangles."""
    import pandas as pd
    with open(path) as f:
        f.readline()
        ne, np_ = (int(v) for v in f.readline().split()[:2])
    nodes = pd.read_csv(path, sep=r"\s+", skiprows=2, nrows=np_, header=None,
                        usecols=[1, 2, 3], engine="c").to_numpy(dtype="f8")
    el = pd.read_csv(path, sep=r"\s+", skiprows=2 + np_, nrows=ne,
                     header=None, names=range(6), engine="c")
    i34 = el[1].to_numpy(dtype=np.int64)
    conn = el[[2, 3, 4, 5]].fillna(0).to_numpy(dtype=np.int64)
    conn[i34 == 3, 3] = 0
    return nodes[:, 0], nodes[:, 1], nodes[:, 2], i34, conn


def write_vgrid(path: Path, kbp: np.ndarray, sigma: np.ndarray):
    """vgrid.in in the current SCHISM layout (Fortran formats of
    gen_vqs_1.f90 / change_vgrid.f90)."""
    nvrt = int(kbp.max())
    np_ = kbp.size
    with open(path, "w") as f:
        f.write(f"{1:12d}\n{nvrt:12d}\n")
        f.write("".join(f" {v:10d}" for v in (nvrt + 1 - kbp).tolist()))
        f.write("\n")
        fmt = " %14.6f" * np_
        for k in range(1, nvrt + 1):
            row = sigma[nvrt - k, :] + 0.0        # no "-0.000000"
            f.write(f"{k:10d}")
            f.write(fmt % tuple(row.tolist()))
            f.write("\n")
    return nvrt


def write_nlev(path: Path, x, y, kbp, i34, conn):
    """nlev.gr3: number of levels at each node (for plotting)."""
    with open(path, "w") as f:
        f.write("# of levels at each node\n")
        f.write(f"{len(i34)} {kbp.size}\n")
        for i in range(kbp.size):
            f.write(f"{i + 1} {x[i]!r} {y[i]!r} {int(kbp[i])}\n")
        for e in range(len(i34)):
            n = int(i34[e])
            f.write(f"{e + 1} {n} " + " ".join(map(str, conn[e, :n])) + "\n")


def write_master(path: Path, hsm, nv, z_mas):
    """vgrid_master.out: the master grids (for plot_VQS)."""
    with open(path, "w") as f:
        for m in range(len(hsm)):
            f.write(f" {m + 1:5d} {int(nv[m]):5d} {hsm[m]:12.4f}"
                    + "".join(f" {v:12.4f}" for v in z_mas[:, m]) + "\n")


def element_levels(kbp: np.ndarray, conn: np.ndarray) -> np.ndarray:
    """Number of levels of each element (max over its nodes), as the
    prism count of the Fortran programs."""
    k = np.where(conn > 0, kbp[np.maximum(conn, 1) - 1], 0)
    return k.max(axis=1)


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------

def run_gen_vgrid(cfg: dict):
    from workflow.core.config import model_dir
    mdir = model_dir(cfg)
    fix = mdir / "fix"
    out = fix / "vgrid.in"
    work = mdir / "vgrid_work"

    print(f"\n{'='*60}")
    print(f"  gen_vgrid for M{cfg['project_id']}")
    print(f"{'='*60}")

    if out.exists():
        print(f"  {out} exists: kept (move it aside to make a new one).")
        return
    vcfg = cfg.get("vgrid")
    if not isinstance(vcfg, dict):
        _fail("no vgrid: block in modelsetup.yaml (uncomment one of the "
              "examples there)")
    hgrid = fix / "hgrid.gr3"
    if not hgrid.exists():
        _fail(f"{hgrid} not found")

    hsm, nv, z_mas = master_grid(vcfg)
    print(f"  {len(hsm)} classes, up to {nv.max()} levels, deepest "
          f"{hsm[-1]:g} m")
    if vcfg.get("shallow"):
        print(f"  hybrid: nodes shallower than {float(vcfg['shallow']['depth']):g}"
              f" m use the {len(vcfg['shallow']['classes'])} shallow classes")
    print(f"  Reading {hgrid} ...")
    x, y, dp, i34, conn = read_hgrid(hgrid)
    ne = len(i34)
    print(f"  {dp.size} nodes, {ne} elements, depth {dp.min():.2f} to "
          f"{dp.max():.2f} m")
    kbp, sigma, info = compute_grid(dp, vcfg)
    if info["n_shallow"]:
        print(f"  {info['n_shallow']} node(s) on the shallow classes")
    nprism = int(element_levels(kbp, conn).sum())

    work.mkdir(parents=True, exist_ok=True)
    tmp = fix / "vgrid.in.tmp"
    nvrt = write_vgrid(tmp, kbp, sigma)
    tmp.rename(out)
    write_nlev(work / "nlev.gr3", x, y, kbp, i34, conn)
    write_master(work / "vgrid_master.out", hsm, nv, z_mas)
    summary = (f"nvrt = {nvrt}\n# of prisms = {nprism}\n"
               f"Average # of layers = {nprism / ne:.6f}\n")
    print("  " + summary.strip().replace("\n", "\n  "))
    print("  Diagnostics ...")
    diag = format_diagnostics(grid_diagnostics(dp, kbp, sigma, conn))
    (work / "gen_vgrid.log").write_text(summary + diag + "\n")
    print("  " + diag.replace("\n", "\n  "))
    print(f"  Wrote {out}")
    print(f"  Wrote {work}/nlev.gr3, vgrid_master.out, gen_vgrid.log")
