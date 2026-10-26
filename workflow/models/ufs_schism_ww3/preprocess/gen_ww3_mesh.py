"""
models/ufs_schism_ww3/preprocess/gen_ww3_mesh.py
================================================
Make the mesh-dependent WW3 files from the SCHISM mesh (once per project).

  fix/hgrid.ll -> fix/mesh.msh          gmsh 2.0, same node and element
                                        numbering as the hgrid
  mesh.msh + ww3_grid.nml -> ww3_grid   fix/mod_def.ww3, fix/ST4TABUHF2.bin and
                                        scrip.nc (node-centred cells)
  scrip.nc -> ESMF_Scrip2Unstruct       fix/ww3_ESMFmesh.nc

Why the numbering matters: WW3 reads mesh.msh and numbers its nodes in file
order, and the UFS WW3 cap takes element i of ww3_ESMFmesh.nc to be WW3 node
i.  The SCHISM cap numbers its nodes as in hgrid.gr3.  The three files must
therefore keep the hgrid order.  WW3 silently drops nodes that no triangle
uses and renumbers the rest, so a mesh with such nodes is refused.

Files already in fix/ are kept (and checked), never overwritten: a project may
bring its own mesh.msh or the whole set.  Missing ones are made:

  mesh.msh missing, the others present  -> error (cannot be matched)
  mesh.msh present, others missing      -> ww3_grid + ESMF step only
  nothing present                       -> all of it

The ww3_grid and ESMF_Scrip2Unstruct steps run in one SLURM job in
M{ID}/ww3_mesh/ with the model's own build (ww3_grid is installed from the
UFS build, so its switches match the model's: mod_def.ww3 must be made by a
ww3_grid built with the same switches).

Sentinel: M{ID}/ww3_mesh/gen_ww3_mesh.done (hash of the inputs).
"""

import hashlib
import os
import sys
from pathlib import Path

from workflow.core.config import model_dir

PRODUCTS = ["mod_def.ww3", "ST4TABUHF2.bin", "ww3_ESMFmesh.nc"]
TOL = 1e-6          # degrees / metres: hgrid text vs the 10-decimal msh


# =============================================================================
# hgrid -> mesh.msh
# =============================================================================

def _read_header(f, path):
    f.readline()                                    # title
    try:
        ne, np_ = (int(v) for v in f.readline().split()[:2])
    except ValueError:
        sys.exit(f"ERROR: {path}: line 2 is not 'ne np'")
    return ne, np_


def hgrid_to_msh(hgrid: Path, msh: Path):
    """Write the gmsh 2.0 version of an hgrid (triangles only).

    Same layout as the file used for the validated M14 run: nodes
    `id  lon  lat  depth`, elements `id  2  3  0  id  0  n1  n2  n3`, no
    boundary elements, no newline after $EndElements.  Depth stays positive
    down (ww3_grid.nml has UNST%SF = -1).  Returns (np, ne).
    """
    with open(hgrid) as f, open(msh, "w") as out:
        ne, np_ = _read_header(f, hgrid)
        out.write(f"$MeshFormat\n2 0 8\n$EndMeshFormat\n$Nodes\n{np_}\n")
        for i in range(1, np_ + 1):
            p = f.readline().split()
            if int(p[0]) != i:
                sys.exit(f"ERROR: {hgrid}: node {i} has id {p[0]} "
                         f"(ids must be 1..np in order)")
            out.write(f"{i}  {float(p[1]):.10f}  {float(p[2]):.10f}  "
                      f"{float(p[3]):.10f}\n")
        out.write(f"$EndNodes\n$Elements\n{ne}\n")
        for i in range(1, ne + 1):
            p = f.readline().split()
            if int(p[0]) != i:
                sys.exit(f"ERROR: {hgrid}: element {i} has id {p[0]}")
            if int(p[1]) != 3:
                sys.exit(f"ERROR: {hgrid}: element {i} has {p[1]} nodes; "
                         f"WW3 reads triangles only")
            out.write(f"{i}  2  3  0  {i}  0  {p[2]}  {p[3]}  {p[4]}\n")
        out.write("$EndElements")
    return np_, ne


def check_msh(hgrid: Path, msh: Path) -> list:
    """Compare mesh.msh with the hgrid; returns a list of problems."""
    errs = []
    with open(hgrid) as g, open(msh) as m:
        ne, np_ = _read_header(g, hgrid)
        if [m.readline().strip() for _ in range(5)][:4] != [
                "$MeshFormat", "2 0 8", "$EndMeshFormat", "$Nodes"]:
            return [f"{msh}: not a gmsh 2.0 ASCII file"]
        m.seek(0)
        for _ in range(4):
            m.readline()
        n_msh = int(m.readline())
        if n_msh != np_:
            return [f"{msh} has {n_msh} nodes, the hgrid {np_}"]
        bad = 0
        for i in range(1, np_ + 1):
            a, b = g.readline().split(), m.readline().split()
            if (int(a[0]) != i or int(b[0]) != i
                    or any(abs(float(a[k]) - float(b[k])) > TOL
                           for k in (1, 2, 3))):
                bad += 1
                if bad <= 3:
                    errs.append(f"node {i}: hgrid {a[1:4]} vs msh {b[1:4]}")
        if bad:
            errs.append(f"{bad} node(s) differ between {hgrid.name} and "
                        f"{msh.name}")
        if m.readline().strip() != "$EndNodes" or \
                m.readline().strip() != "$Elements":
            return errs + [f"{msh}: bad $EndNodes/$Elements"]
        if int(m.readline()) != ne:
            return errs + [f"{msh} has a different number of elements than "
                           f"the hgrid ({ne})"]
        used = bytearray(np_ + 1)
        bad = 0
        for i in range(1, ne + 1):
            a, b = g.readline().split(), m.readline().split()
            tri = [int(v) for v in a[2:5]]
            if (len(b) != 9 or int(b[1]) != 2 or int(a[1]) != 3
                    or [int(v) for v in b[6:9]] != tri):
                bad += 1
                if bad <= 3:
                    errs.append(f"element {i}: hgrid {a} vs msh {b}")
            for v in tri:
                used[v] = 1
        if bad:
            errs.append(f"{bad} element(s) differ (or are not triangles)")
        orphans = np_ - sum(used[1:])
        if orphans:
            errs.append(f"{orphans} node(s) are in no triangle: WW3 would "
                        f"drop and renumber them, breaking the match with "
                        f"SCHISM node numbers")
    return errs


def check_esmf_mesh(path: Path, msh: Path) -> list:
    """The ESMF mesh must have one element per mesh.msh node, centred on it."""
    import netCDF4 as nc4
    import numpy as np
    with open(msh) as m:
        for _ in range(4):
            m.readline()
        n = int(m.readline())
        xy = np.empty((n, 2))
        for i in range(n):
            p = m.readline().split()
            xy[i] = (float(p[1]), float(p[2]))
    with nc4.Dataset(str(path)) as ds:
        ne = len(ds.dimensions["elementCount"])
        if ne != n:
            return [f"{path.name}: {ne} elements, mesh.msh has {n} nodes"]
        c = np.asarray(ds.variables["centerCoords"][:])
    dx = np.abs((c[:, 0] - xy[:, 0] + 180.0) % 360.0 - 180.0)
    dy = np.abs(c[:, 1] - xy[:, 1])
    worst = float(max(dx.max(), dy.max()))
    if worst > 1e-4:
        k = int(np.argmax(np.maximum(dx, dy)))
        return [f"{path.name}: element {k + 1} is centred {worst:.3g} deg "
                f"from mesh.msh node {k + 1} (the order differs)"]
    return []


# =============================================================================
# the step
# =============================================================================

def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def input_hash(fix: Path, exe: Path) -> str:
    return hashlib.sha256("|".join(
        _sha(p) for p in (fix / "hgrid.ll", fix / "ww3_grid.nml",
                          fix / "namelists.nml", exe)).encode()).hexdigest()


def esmf_prefix_snippet() -> str:
    """Shell lines that set ESMF_S2U (the ESMF of the loaded UFS modulefile)."""
    return (
        'ESMF_S2U=$(command -v ESMF_Scrip2Unstruct || true)\n'
        'if [ -z "$ESMF_S2U" ] && [ -n "$ESMFMKFILE" ]; then\n'
        '  ESMF_S2U="$(dirname "$(dirname "$ESMFMKFILE")")/bin/'
        'ESMF_Scrip2Unstruct"\n'
        'fi\n'
        '[ -x "$ESMF_S2U" ] || { echo "ERROR: ESMF_Scrip2Unstruct not found '
        '(ESMFMKFILE=$ESMFMKFILE)"; exit 1; }\n'
        'echo "ESMF_Scrip2Unstruct: $ESMF_S2U"')


def run_gen_ww3_mesh(cfg: dict, config_dir: Path):
    from workflow.core.slurm import (DEFAULT_ACCOUNT, DEFAULT_MAIL_USER,
                                     DEFAULT_PARTITION, SlurmSubmitter,
                                     wait_for_slurm_jobs)
    from workflow.core.templating import ufs_module_name

    mdir, pid = model_dir(cfg), cfg["project_id"]
    fix, work = mdir / "fix", mdir / "ww3_mesh"
    sentinel  = work / "gen_ww3_mesh.done"
    msh       = fix / "mesh.msh"
    print("  gen_ww3_mesh: the WW3 mesh files from fix/hgrid.ll")

    for name in ("hgrid.ll", "ww3_grid.nml", "namelists.nml"):
        if not (fix / name).exists():
            sys.exit(f"ERROR: fix/{name} is missing (hgrid.ll is the mesh; "
                     f"the other two are in templates/ufs_schism_ww3/fix/).")

    present = {n: (fix / n).exists() for n in PRODUCTS}
    if not msh.exists():
        if any(present.values()):
            sys.exit("ERROR: fix/ has " + ", ".join(
                n for n in PRODUCTS if present[n]) + " but no mesh.msh: "
                "they cannot be checked against the mesh. Add the mesh.msh "
                "they were made from, or remove them to make all again.")
        print("  fix/mesh.msh not found: converting hgrid.ll ...")
        np_, ne = hgrid_to_msh(fix / "hgrid.ll", msh)
        print(f"  Wrote {msh}  (nodes={np_}, elements={ne})")
    else:
        print("  fix/mesh.msh exists: kept.")
    errs = check_msh(fix / "hgrid.ll", msh)
    if errs:
        print("  ERROR: mesh.msh does not match hgrid.ll:")
        for e in errs:
            print(f"    - {e}")
        sys.exit(1)
    print("  mesh.msh matches hgrid.ll (same numbering, triangles only, "
          "every node used).")

    if all(present.values()):
        errs = check_esmf_mesh(fix / "ww3_ESMFmesh.nc", msh)
        if errs:
            sys.exit("ERROR: " + "; ".join(errs))
        print("  mod_def.ww3, ST4TABUHF2.bin and ww3_ESMFmesh.nc exist: kept "
              "(ESMF mesh matches mesh.msh).")
        return

    exe = mdir / "bin" / (cfg.get("executables") or {}).get("ww3_grid",
                                                            "ww3_grid")
    if not exe.exists():
        sys.exit(f"ERROR: {exe} not found. It is installed by the UFS build "
                 f"(build.yaml install_tools: [ww3_ounp, ww3_grid]); run "
                 f"--phase build.")
    new_hash = input_hash(fix, exe)
    if sentinel.exists() and sentinel.read_text().strip() == new_hash \
            and all((fix / n).exists() for n in PRODUCTS):
        print("  gen_ww3_mesh: complete for these inputs. Skipping.")
        return

    work.mkdir(parents=True, exist_ok=True)
    for stale in PRODUCTS + ["scrip.nc"]:
        (work / stale).unlink(missing_ok=True)
    sentinel.unlink(missing_ok=True)
    for name in ("mesh.msh", "namelists.nml"):      # inputs of ww3_grid
        link = work / name
        link.unlink(missing_ok=True)
        link.symlink_to(fix / name)
    (work / "ww3_grid.nml").write_text((fix / "ww3_grid.nml").read_text())

    logdir = mdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    slurm = cfg.get("slurm", {})
    subs = {
        "ACCOUNT":    slurm.get("account", DEFAULT_ACCOUNT),
        "PARTITION":  slurm.get("partition", DEFAULT_PARTITION),
        "MAILUSER":   slurm.get("mail_user", DEFAULT_MAIL_USER),
        "WORKDIR":    str(work),
        "JOBNAME":    f"ww3mesh_M{pid}",
        "MEM":        slurm.get("gen_ww3_mesh_mem", "64G"),
        "WALLTIME":   slurm.get("gen_ww3_mesh_walltime", "01:00:00"),
        "LOGDIR":     str(logdir),
        "BIN_DIR":    str(mdir / "bin"),
        "UFS_MODULE": ufs_module_name(cfg),
        "GRID_EXE":   str(exe),
        "ESMF_LINES": esmf_prefix_snippet(),
    }
    submitter = SlurmSubmitter(Path(__file__).resolve().parent.parent
                               / "templates" / "slurm")
    out = submitter.render_and_submit("gen_ww3_mesh.sbatch", subs,
                                      logdir / "gen_ww3_mesh.sbatch")
    jobid = SlurmSubmitter.parse_jobid(out)
    print(f"  Log: {logdir}/gen_ww3_mesh.out")
    wait_for_slurm_jobs([jobid], label="gen_ww3_mesh job",
                        tag="[gen_ww3_mesh]", next_step="the checks")

    for n in PRODUCTS + ["scrip.nc"]:
        if not (work / n).exists() or (work / n).stat().st_size == 0:
            sys.exit(f"ERROR: the job did not make {work / n}; see "
                     f"{logdir}/gen_ww3_mesh.out and {work}/ww3_grid.out")
    errs = check_esmf_mesh(work / "ww3_ESMFmesh.nc", msh)
    if errs:
        sys.exit("ERROR: " + "; ".join(errs))
    for n in PRODUCTS:
        os.replace(work / n, fix / n)
    sentinel.write_text(new_hash + "\n")
    print("  gen_ww3_mesh complete: " + ", ".join(
        f"fix/{n}" for n in PRODUCTS) + "  (scrip.nc stays in ww3_mesh/)")
