"""
models/schism_mice/postprocess/download_seaice.py
=================================================
Phase 5 step "download_seaice" (DTN, internet required).

Daily NOAA/NSIDC Climate Data Record of Passive Microwave Sea Ice
Concentration, northern hemisphere, 25 km polar stereographic grid
(EPSG:3411, 304 x 448), anonymous HTTPS from NSIDC:

  final CDR  G02202 version 6   1978 -> about 1 week before present
      https://noaadata.apps.nsidc.org/NOAA/G02202_V6/north/daily/<YYYY>/
      sic_psn25_<YYYYMMDD>_<sensor>_v06r00.nc
  near-real-time CDR  G10016 version 4   the most recent ~3 months
      https://noaadata.apps.nsidc.org/NOAA/G10016_V4/north/daily/<YYYY>/
      sic_psn25_<YYYYMMDD>_<sensor>_icdr_v04r00.nc

For each day of compare_seaice_start/end (default start_date/end_date) the
final CDR is used when it has the day, otherwise the near-real-time one.
The file name is taken from the year's directory listing (the sensor part,
e.g. am2, changes over the record). One file per day, kept as published:

    M{ID}/obs/seaice_nsidc/sic_psn25_<YYYYMMDD>_..._v06r00.nc

Variables used by compare_seaice: cdr_seaice_conc (0-1 after scaling, fill
255), cdr_supplementary/surface_type_mask (50 ocean, 75 lake, 100 pole hole,
200 coast, 250 land), x/y (m) and crs.

Resume-safe: days already in the folder are skipped.
"""

import argparse
import re
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from workflow.core.config import load_config, model_dir

BASE = "https://noaadata.apps.nsidc.org/NOAA"
PRODUCTS = (                      # (name, directory, file suffix), in order
    ("G02202 v6 (final CDR)",  "G02202_V6", "_v06r00.nc"),
    ("G10016 v4 (NRT CDR)",    "G10016_V4", "_icdr_v04r00.nc"),
)


def obs_dir(cfg: dict) -> Path:
    return model_dir(cfg) / "obs" / "seaice_nsidc"


def date_range(cfg: dict) -> list:
    s = date.fromisoformat(str(cfg.get("compare_seaice_start")
                               or cfg["start_date"]))
    e = date.fromisoformat(str(cfg.get("compare_seaice_end")
                               or cfg["end_date"]))
    out, d = [], s
    while d <= e:
        out.append(d)
        d += timedelta(days=1)
    return out


def local_file(cfg: dict, d: date):
    """The downloaded file for day d, or None."""
    hits = sorted(obs_dir(cfg).glob(f"sic_psn25_{d:%Y%m%d}_*.nc"))
    hits = [h for h in hits if h.stat().st_size > 0]
    return hits[0] if hits else None


_LISTINGS = {}


def _listing(directory: str, year: int) -> list:
    """File names in <BASE>/<directory>/north/daily/<year>/ ([] if none)."""
    key = (directory, year)
    if key not in _LISTINGS:
        url = f"{BASE}/{directory}/north/daily/{year}/"
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                html = r.read().decode("utf-8", "replace")
            _LISTINGS[key] = sorted(set(
                re.findall(r'href="(sic_psn25_\d{8}_[^"/]+\.nc)"', html)))
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                print(f"  WARNING: {url}: HTTP {exc.code}")
            _LISTINGS[key] = []
    return _LISTINGS[key]


def _download(url: str, out: Path):
    tmp = out.with_name(out.name + ".part")
    with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    with open(tmp, "rb") as f:          # netCDF-4 files start with HDF5 magic
        if f.read(8) != b"\x89HDF\r\n\x1a\n":
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"not a netCDF-4 file: {url}")
    tmp.replace(out)


def run_download_seaice(cfg: dict) -> bool:
    from workflow.core.environment import check_active_env, check_dtn
    check_dtn("NSIDC sea-ice concentration download")
    check_active_env(cfg, "download_seaice")

    days = date_range(cfg)
    odir = obs_dir(cfg)
    odir.mkdir(parents=True, exist_ok=True)
    print(f"\n{'='*60}")
    print(f"  NOAA/NSIDC sea-ice concentration CDR (25 km, daily, north)")
    print(f"  {days[0]} -> {days[-1]}  ({len(days)} day(s))  -> {odir}")
    print(f"{'='*60}")

    counts, missing = {p[0]: 0 for p in PRODUCTS}, []
    have = 0
    for d in days:
        if local_file(cfg, d) is not None:
            have += 1
            continue
        tag = f"sic_psn25_{d:%Y%m%d}_"
        got = False
        for name, directory, suffix in PRODUCTS:
            hits = [f for f in _listing(directory, d.year)
                    if f.startswith(tag) and f.endswith(suffix)]
            if not hits:
                continue
            url = f"{BASE}/{directory}/north/daily/{d.year}/{hits[0]}"
            try:
                _download(url, odir / hits[0])
            except Exception as exc:
                print(f"  {d}: ERROR downloading {url}: {exc}")
                continue
            counts[name] += 1
            got = True
            break
        if not got:
            missing.append(d.isoformat())
    print(f"  Already present: {have}")
    for name, n in counts.items():
        print(f"  Downloaded from {name}: {n}")
    if missing:
        print(f"  Not available in either product ({len(missing)} day(s)): "
              f"{', '.join(missing)}")
    print(f"{'='*60}\n")
    return not missing


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    ok = run_download_seaice(load_config(Path(args.config)))
    sys.exit(0 if ok else 1)
