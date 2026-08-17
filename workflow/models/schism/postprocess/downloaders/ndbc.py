"""
models/schism/postprocess/downloaders/ndbc.py
=============================================
Phase 5 step "download_ndbc" (DTN, internet required).

Downloads NOAA NDBC standard-meteorological (stdmet) observations for the
NDBC stations listed in the SCHISM ``fix/station.in`` file and stores them,
one CSV per station per year, under:

    M{ID}/raw/ndbc/{station_id}_{YYYY}.csv

NDBC's "weird" storage layout (all stdmet, times UTC, units metric):
  * Past complete years  -> a single gzipped ANNUAL file:
      https://www.ndbc.noaa.gov/data/historical/stdmet/{station}h{YYYY}.txt.gz
  * Current year, completed months -> gzipped MONTHLY files via the
    view_text_file service (month number WITHOUT leading zero, month-abbrev dir):
      https://www.ndbc.noaa.gov/view_text_file.php?filename={station}{M}{YYYY}.txt.gz&dir=data/stdmet/{MonAbbr}/
  * The most recent ~45 days (not yet in a monthly file) -> the realtime file:
      https://www.ndbc.noaa.gov/data/realtime2/{STATION}.txt  (newest-first)

For a requested year this module assembles a gap-free per-year CSV:
  - year < current year : the annual historical file.
  - year == current year: every completed month's monthly file, PLUS the
    realtime 45-day file to fill the tail, merged and de-duplicated on time.

Only the observation variables named in each station's station.in VARS bracket
are relevant downstream (station_skill), but NDBC serves ALL stdmet columns in
one file, so the whole stdmet table is stored and station_skill selects the
requested columns.

stdmet columns (historical):
    #YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS TIDE
Missing values (99.0/999.0/9999.0/99.00/999.00/MM) are converted to NaN.
"""

import argparse
import gzip
import io
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from workflow.core.config import load_config, model_dir
from workflow.core.environment import check_dtn, check_active_env
from workflow.core.station_parser import parse_station_in


HDR = {"User-Agent": "Mozilla/5.0 (stofs_ak_workflow)"}

HISTORICAL = "https://www.ndbc.noaa.gov/data/historical/stdmet/{sid}h{year}.txt.gz"
REALTIME   = "https://www.ndbc.noaa.gov/data/realtime2/{SID}.txt"
MONTHLY    = ("https://www.ndbc.noaa.gov/view_text_file.php?"
              "filename={sid}{m}{year}.txt.gz&dir=data/stdmet/{abbr}/")

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Sentinel missing-value tokens used across stdmet columns.
MISSING_VALUES = [99.0, 999.0, 9999.0, 99.00, 999.00, 9999.00]


def _fetch(url: str, gz: bool):
    """GET a URL; gunzip if gz. Return decoded text or None on any error."""
    try:
        req = urllib.request.Request(url, headers=HDR)
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        if gz:
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # not available (expected for very recent months)
        print(f"    HTTP {e.code}: {url}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"    fetch failed: {e}: {url}")
        return None


def _parse_stdmet_text(text: str):
    """Parse an NDBC stdmet text block into a DataFrame indexed by UTC datetime.

    Handles both historical (2-digit or 4-digit year, header '#YY'/'#YYYY') and
    realtime files. Returns a pandas DataFrame (empty if unparseable).
    """
    import numpy as np
    import pandas as pd

    lines = text.splitlines()
    if not lines:
        return pd.DataFrame()

    # Locate the header line (starts with '#YY' or 'YY' / '#YYYY').
    header_idx = None
    for i, ln in enumerate(lines[:5]):
        if ln.replace("#", "").strip().startswith(("YY", "YYYY")):
            header_idx = i
            break
    if header_idx is None:
        header_idx = 0

    # The line after the header is the UNITS line (starts with '#yr' / 'yr');
    # skip it if present.
    body_start = header_idx + 1
    if body_start < len(lines):
        nxt = lines[body_start].replace("#", "").strip().lower()
        skiprows = [1] if nxt.startswith(("yr", "mo")) else []
    else:
        skiprows = []

    try:
        df = pd.read_csv(
            io.StringIO("\n".join(lines[header_idx:])),
            sep=r"\s+", skiprows=skiprows, low_memory=False,
        )
    except Exception:  # noqa: BLE001
        return pd.DataFrame()

    df.columns = [c.replace("#", "") for c in df.columns]

    def _col(cands):
        for c in cands:
            if c in df.columns:
                return c
        return None

    yy = _col(["YY", "YYYY", "YR"])
    mm = _col(["MM", "Mo"])
    dd = _col(["DD", "Dy"])
    hh = _col(["hh", "Hr"])
    mn = _col(["mm", "Mn"])
    if not all([yy, mm, dd, hh]):
        return pd.DataFrame()

    for c in [yy, mm, dd, hh] + ([mn] if mn else []):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Normalise 2-digit years.
    df[yy] = df[yy].apply(
        lambda x: x + 1900 if x < 50 else (x + 2000 if x < 100 else x)
    )

    parts = {"year": df[yy], "month": df[mm], "day": df[dd], "hour": df[hh]}
    if mn:
        parts["minute"] = df[mn]
    df["datetime"] = pd.to_datetime(parts, errors="coerce")
    df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
    df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="first")]

    # Convert data columns to numeric and replace missing sentinels with NaN.
    # Keep the standard stdmet observation columns if present.
    keep = [c for c in ["WDIR", "WSPD", "GST", "WVHT", "DPD", "APD", "MWD",
                        "PRES", "BAR", "ATMP", "WTMP", "DEWP", "VIS", "TIDE"]
            if c in df.columns]
    out = df[keep].apply(pd.to_numeric, errors="coerce")
    out = out.replace(MISSING_VALUES, np.nan)
    # Some historical files label pressure 'BAR' instead of 'PRES'.
    if "BAR" in out.columns and "PRES" not in out.columns:
        out = out.rename(columns={"BAR": "PRES"})
    return out


def _slice_year(df, year: int):
    """Return only the rows of df that fall in the given calendar year."""
    if df is None or df.empty:
        return df
    return df[(df.index >= f"{year}-01-01") & (df.index < f"{year + 1}-01-01")]


def _build_year(sid: str, year: int, today: date):
    """Assemble a gap-free stdmet DataFrame for one station/year.

    Returns a DataFrame (possibly empty).
    """
    import pandas as pd

    if year < today.year:
        # Complete past year: single annual historical file.
        txt = _fetch(HISTORICAL.format(sid=sid.lower(), year=year), gz=True)
        if txt is None:
            return pd.DataFrame()
        return _slice_year(_parse_stdmet_text(txt), year)

    # Current year: completed monthly files + realtime tail.
    frames = []
    last_month = today.month  # attempt through the current month
    for m in range(1, last_month + 1):
        abbr = MONTH_ABBR[m - 1]
        url = MONTHLY.format(sid=sid.lower(), m=m, year=year, abbr=abbr)
        # NOTE: the view_text_file service decompresses server-side and returns
        # PLAIN text despite the .txt.gz filename, so gz=False here.
        txt = _fetch(url, gz=False)
        if txt is not None:
            df = _parse_stdmet_text(txt)
            if not df.empty:
                frames.append(df)
            time.sleep(0.3)
    # Realtime 45-day file (fills the most recent weeks not yet in monthly).
    rt = _fetch(REALTIME.format(SID=sid.upper()), gz=False)
    if rt is not None:
        df = _parse_stdmet_text(rt)
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    return _slice_year(combined, year)


def _years_in_range(start: date, end: date):
    return list(range(start.year, end.year + 1))


def run_download_ndbc(cfg: dict):
    import pandas as pd

    check_dtn("NDBC observation download")
    check_active_env(cfg, "download_ndbc")

    mdir = model_dir(cfg)
    station_in = mdir / "fix" / "station.in"
    if not station_in.exists():
        print(f"ERROR: station.in not found: {station_in}")
        sys.exit(1)

    start = date.fromisoformat(cfg["start_date"])
    end   = date.fromisoformat(cfg["end_date"])
    today = date.today()
    years = _years_in_range(start, end)

    stations = parse_station_in(station_in)
    ndbc = [s for s in stations if s["source"] == "NDBC"]

    ndbc_dir = mdir / "raw" / "ndbc"
    ndbc_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  NDBC observation download")
    print(f"  station.in: {station_in}")
    print(f"  {len(ndbc)} NDBC station(s); years: {years}")
    print(f"  Output: {ndbc_dir}")
    print(f"{'='*60}\n")

    # Unique station ids (station.in may list a station more than once).
    seen = set()
    n_ok = n_skip = n_fail = 0
    for st in ndbc:
        sid = st["station_id"]
        for year in years:
            key = (sid, year)
            if key in seen:
                continue
            seen.add(key)

            out = ndbc_dir / f"{sid}_{year}.csv"
            # For PAST years the annual file is immutable -> skip if present.
            # For the CURRENT year always refresh (new months/realtime accrue).
            if out.exists() and out.stat().st_size > 0 and year < today.year:
                print(f"  {sid} {year}: already present, skipping.")
                n_skip += 1
                continue

            print(f"  {sid} {year}: assembling "
                  f"({'annual' if year < today.year else 'monthly + realtime'}) ...")
            df = _build_year(sid, year, today)
            if df is None or df.empty:
                print(f"  {sid} {year}: no data available.")
                n_fail += 1
                continue
            tmp = out.with_suffix(".csv.tmp")
            df.to_csv(tmp, index_label="datetime")
            tmp.replace(out)
            print(f"  {sid} {year}: {len(df)} records -> {out.name}")
            n_ok += 1

    print(f"\n{'='*60}")
    print(f"  NDBC download complete.  written={n_ok} skipped={n_skip} empty={n_fail}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Download NDBC station observations")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    run_download_ndbc(cfg)
