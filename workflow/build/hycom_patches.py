"""
build/hycom_patches.py
======================
Patched sources of the SCHISM HYCOM utilities, generated from the pristine
upstream files at build time (the SCHISM checkout is never modified).

Why patches: the stock utilities expect HYCOM data packed as scaled shorts.
The workflow's HYCOM stacks are unpacked floats (ncpdq -U) with a -30000
fill value, so the scaling must go.  Every edit is an exact-text
replacement that must match exactly once, so a build fails loudly if
upstream changes the lines it patches.

Variants (per utility; `key` is the project.yaml executables key):

  noscaling            scaling removed, rjunk = -29999
  noscaling_lonshift   + HYCOM 0-360 shifted to -180..180 right after the
                       xlon axis is read (only valid when the shifted axis
                       is still increasing, i.e. the grid does not cross 180)

  ssh_max_3Dth         gen_3Dth only: |SSH| sanity limit (stock 6)

The utilities do not convert longitudes by themselves; the choice between
the two variants is made at run time (gen_hycom_utils._select_hycom_exe).
"""

from pathlib import Path

# key -> (stem, path under src/Utility, UtilLib modules in link order)
UTILS = {
    "gen_hotstart": ("gen_hot_from_hycom_0",
                     "Gen_Hotstart/gen_hot_from_hycom_0.f90",
                     ("schism_geometry", "extract_mod", "compute_zcor",
                      "pt_in_poly_test")),
    "gen_3Dth":     ("gen_3Dth_from_hycom",
                     "Gen_Hotstart/gen_3Dth_from_hycom.f90",
                     ("schism_geometry", "extract_mod", "compute_zcor",
                      "pt_in_poly_test")),
    "gen_nudge":    ("gen_nudge_from_hycom",
                     "Pre-Processing/gen_nudge_from_hycom.f90",
                     ("pt_in_poly_test", "compute_zcor")),
}
VARIANTS = ("noscaling", "noscaling_lonshift")

_SCALE5 = [
    ("        salt=salt*1.e-3+20\n", "        !salt=salt*1.e-3+20\n"),
    ("        temp=temp*1.e-3+20\n", "        !temp=temp*1.e-3+20\n"),
    ("        uvel=uvel*1.e-3\n",    "        !uvel=uvel*1.e-3\n"),
    ("        vvel=vvel*1.e-3\n",    "        !vvel=vvel*1.e-3\n"),
    ("        ssh=ssh*1.e-3\n",      "        !ssh=ssh*1.e-3\n"),
]
_RJUNK = ("        rjunk=-3.e4*1.e-3+20\n",
          "        !rjunk=-3.e4*1.e-3+20\n        rjunk = -29999.0\n")

_NOSCALING = {
    "gen_hotstart": _SCALE5 + [_RJUNK],
    "gen_3Dth":     _SCALE5 + [_RJUNK],
    "gen_nudge":    _SCALE5[:2] + [_RJUNK],       # nudge reads only T and S
}

# Where xind (the xlon axis) is read; lon(:,:) is built from it afterwards.
_LONSHIFT_ANCHOR = {
    "gen_hotstart": "        status = nf90_get_var(ncids(1), xvid, xind)\n",
    "gen_3Dth":     "           status = nf90_get_var(ncids(1), xvid, xind)\n",
    "gen_nudge":    "           status = nf90_get_var(sid, xvid, xind)\n",
}
_LONSHIFT_LINE = ("where (xind > 180.0) xind = xind - 360.0"
                  "  !HYCOM 0-360 -> mesh -180..180\n")

_SSH_STOCK = "      ssh_max=6 !max. of |SSH|\n"


def lonshift_name(exe: str) -> str:
    """gen_3Dth_from_hycom_noscaling.exe -> ..._noscaling_lonshift.exe"""
    stem, dot, ext = exe.rpartition(".")
    return f"{stem}_lonshift.{ext}" if dot else f"{exe}_lonshift"


def _lonshift(key: str) -> list:
    anchor = _LONSHIFT_ANCHOR[key]
    indent = anchor[: len(anchor) - len(anchor.lstrip())]
    return [(anchor, anchor + indent + _LONSHIFT_LINE)]


def _apply(text: str, edits: list, label: str) -> str:
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            raise ValueError(
                f"{label}: expected exactly 1 match, found {n}, for:\n{old}"
                "Upstream SCHISM changed this line; the patch must be "
                "updated in workflow/build/hycom_patches.py.")
        text = text.replace(old, new)
    return text


def variant_edits(key: str, variant: str, ssh_max_3dth=None) -> list:
    """Edit list (old, new) of one utility variant."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    edits = list(_NOSCALING[key])
    if variant == "noscaling_lonshift":
        edits += _lonshift(key)
    if key == "gen_3Dth" and ssh_max_3dth is not None:
        edits.append((_SSH_STOCK,
                      f"      ssh_max={ssh_max_3dth} !max. of |SSH|\n"))
    return edits


def write_variants(util_root: Path, out_dir: Path, ssh_max_3dth=None) -> list:
    """Write the patched sources into out_dir.

    util_root is <schism>/src/Utility. Returns a list of dicts
    {key, variant, stem, source (Path), deps (UtilLib module names)}.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for key, (stem, rel, deps) in UTILS.items():
        base = (Path(util_root) / rel).read_text()
        for variant in VARIANTS:
            label = f"{stem}_{variant}"
            text = _apply(base, variant_edits(key, variant, ssh_max_3dth),
                          label)
            path = out_dir / f"{label}.f90"
            path.write_text(text)
            made.append({"key": key, "variant": variant, "stem": stem,
                         "source": path, "deps": deps})
    return made
