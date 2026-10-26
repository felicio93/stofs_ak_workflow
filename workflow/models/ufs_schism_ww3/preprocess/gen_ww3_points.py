"""
models/ufs_schism_ww3/preprocess/gen_ww3_points.py
==================================================
fix/ww3_points.list (WW3 point output) from fix/station.in, once per project.

station.in lists a station once per depth (z=0, z=-1, ... for the same lon and
lat); waves only have a surface value, so the list has one point per distinct
location, in order of first appearance:

    <lon>  <lat>  '<station id>'
    ...
    0.0  0.0  'STOPSTRING'

lon and lat are copied as written in station.in (same longitude convention as
the mesh).  The name is the station id of the station.in comment
(``![VARS],<id>,<SOURCE>,<name>``, see core.station_parser).  Two different
locations with the same id get a letter suffix (KU2, KU2b, ...).

A fix/ww3_points.list that already exists is kept.  Station lines whose
comment looks like a station spec but does not parse are reported: they are
ignored by the observation steps too.
"""

import string
import sys

from workflow.core.config import model_dir
from workflow.core.station_parser import parse_station_in


def read_points(station_in):
    """Return (points, warnings); points = [(lon_text, lat_text, name)]."""
    ids = {s["line_index"]: s["station_id"]
           for s in parse_station_in(station_in)}
    lines = station_in.read_text(errors="ignore").splitlines()[2:]
    where, order, warns = {}, [], []
    idx = 0
    for raw in lines:
        if not raw.strip():
            continue
        idx += 1
        data, _, comment = raw.partition("!")
        nums = data.split()
        if len(nums) < 4:
            continue
        key = (round(float(nums[1]), 6), round(float(nums[2]), 6))
        if key not in where:
            where[key] = {"lon": nums[1], "lat": nums[2], "name": None}
            order.append(key)
        if idx not in ids and "]" in comment:
            warns.append(f"station.in line {idx + 2}: comment not parsed "
                         f"({comment.strip()[:60]}...)")
        if where[key]["name"] is None and idx in ids:
            where[key]["name"] = ids[idx]

    points, used = [], set()
    for key in order:
        p = where[key]
        if p["name"] is None:
            warns.append(f"no station id for ({p['lon']}, {p['lat']}): "
                         f"skipped")
            continue
        name = p["name"]
        for suffix in [""] + list(string.ascii_lowercase[1:]):
            if name + suffix not in used:
                name += suffix
                break
        used.add(name)
        points.append((p["lon"], p["lat"], name))
    return points, warns


def write_points(points, path):
    with open(path, "w") as f:
        for lon, lat, name in points:
            f.write(f"{lon}  {lat}  '{name}'\n")
        f.write("0.0  0.0  'STOPSTRING'\n")


def run_gen_ww3_points(cfg: dict):
    fix = model_dir(cfg) / "fix"
    out = fix / "ww3_points.list"
    if out.exists():
        print("  gen_ww3_points: fix/ww3_points.list exists: kept.")
        return
    station_in = fix / "station.in"
    if not station_in.exists():
        sys.exit("ERROR: fix/ww3_points.list and fix/station.in are both "
                 "missing; provide one of them.")
    points, warns = read_points(station_in)
    for w in warns:
        print(f"  WARNING: {w}")
    write_points(points, out)
    print(f"  gen_ww3_points: wrote {out}  ({len(points)} points from "
          f"station.in, one per distinct lon/lat)")
