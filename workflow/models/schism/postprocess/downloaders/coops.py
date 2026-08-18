"""
models/schism/postprocess/downloaders/coops.py
==============================================
Phase 5 step "download_coops" (DTN, internet required).

Downloads NOAA CO-OPS observations for the stations listed in the SCHISM
``fix/station.in`` file and stores them, one CSV per station / product / month,
under:

    M{ID}/obs/coops/{station_id}_{product}_{YYYYMM}.csv

CO-OPS 6-minute data is limited to one month per API request, so the natural
storage granularity is monthly. Months already present are skipped
(resume-safe).

Supported variables (this pass): water level (WL/elev), water temperature (T),
air pressure, and wind. Currents and salinity are parsed but deferred.

Only stations whose station.in comment is a valid CO-OPS spec
(``[VARS],<id>,CO-OPS,<name>``) and whose VARS intersect the supported set are
downloaded. NDBC stations are skipped here (handled by a future NDBC step).

API reference: https://api.tidesandcurrents.noaa.gov/api/prod/
  * time_zone=gmt  -> timestamps in UTC (matches SCHISM model time)
  * units=metric   -> m, degC, m/s (NOTE: currents would be cm/s; not used here)
  * format=json    -> {"data": [{"t": "...", "v": "..."} ...]} or
                      {"error": {"message": "..."}}
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from calendar import monthrange
from datetime import date
from pathlib import Path

from workflow.core.config import load_config, model_dir, list_months
from workflow.core.environment import check_dtn, check_active_env
from workflow.core.station_parser import parse_station_in


API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
APP = "stofs_ak_workflow"

# Map a station.in variable token (upper-cased) to a CO-OPS product name.
# WL/elev -> water_level ; T -> water_temperature ; air pressure -> air_pressure
# wind -> wind. Currents/salinity intentionally omitted in this pass.
VAR_TOKEN_TO_PRODUCT = {
    "WL":            "water_level",
    "ELEV":          "water_level",
    "T":             "water_temperature",
    "TEMP":          "water_temperature",
    "AIR_PRESSURE":  "air_pressure",
    "AIRPRESSURE":   "air_pressure",
    "PATM":          "air_pressure",
    "PRESSURE":      "air_pressure",
    "WIND":          "wind",
    "WINDX":         "wind",
    "WINDY":         "wind",
}

# The products this pass actually downloads.
SUPPORTED_PRODUCTS = {"water_level", "water_temperature", "air_pressure", "wind"}

# Per-product response field(s) we keep from the JSON "data" records. The 't'
# (time) field is always kept. For single-value products we keep 'v'; wind
# returns speed 's', direction 'd', gust 'g'.
PRODUCT_FIELDS = {
    "water_level":       ["t", "v"],
    "water_temperature": ["t", "v"],
    "air_pressure":      ["t", "v"],
    "wind":              ["t", "s", "d", "g"],
}


def _products_for_station(station: dict) -> set:
    """Return the set of CO-OPS products to download for a parsed station."""
    prods = set()
    for tok in station["vars"]:
        p = VAR_TOKEN_TO_PRODUCT.get(tok.strip().upper())
        if p and p in SUPPORTED_PRODUCTS:
            prods.add(p)
    return prods


def _month_bounds(ym: str):
    """Return (begin_YYYYMMDD, end_YYYYMMDD) covering the whole month ym."""
    year, month = int(ym[:4]), int(ym[4:])
    ndays = monthrange(year, month)[1]
    return f"{ym}01", f"{ym}{ndays:02d}"


def _build_url(station_id, product, begin, end, datum) -> str:
    params = [
        f"begin_date={begin}",
        f"end_date={end}",
        f"station={station_id}",
        f"product={product}",
        "time_zone=gmt",
        "units=metric",
        "format=json",
        f"application={APP}",
    ]
    if product == "water_level":
        params.append(f"datum={datum}")
    return API + "?" + "&".join(params)


def _fetch_json(url: str):
    """GET a CO-OPS API URL, return the parsed JSON dict (or None on HTTP error)."""
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"    HTTP error {e.code}: {url}")
        return None
    except urllib.error.URLError as e:
        print(f"    URL error: {e.reason}: {url}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"    request failed: {e}: {url}")
        return None


def _records_to_csv(records: list, fields: list, out_path: Path):
    """Write CO-OPS 'data' records to a CSV with the given field columns."""
    import csv
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for rec in records:
            w.writerow([rec.get(k, "") for k in fields])
    tmp.replace(out_path)


def _download_station_product_month(station_id, product, ym, datum,
                                     coops_dir: Path) -> str:
    """Download one station/product/month. Returns 'ok', 'skip', or 'fail'."""
    out = coops_dir / f"{station_id}_{product}_{ym}.csv"
    if out.exists() and out.stat().st_size > 0:
        print(f"    {station_id} {product} {ym}: already present, skipping.")
        return "skip"

    begin, end = _month_bounds(ym)
    url = _build_url(station_id, product, begin, end, datum)
    data = _fetch_json(url)
    if data is None:
        return "fail"
    if "error" in data:
        msg = data["error"].get("message", "unknown error").strip()
        print(f"    {station_id} {product} {ym}: CO-OPS says: {msg}")
        return "fail"
    records = data.get("data")
    if not records:
        print(f"    {station_id} {product} {ym}: no data returned.")
        return "fail"

    fields = PRODUCT_FIELDS[product]
    _records_to_csv(records, fields, out)
    print(f"    {station_id} {product} {ym}: {len(records)} records -> {out.name}")
    return "ok"


def run_download_coops(cfg: dict):
    check_dtn("CO-OPS observation download")
    check_active_env(cfg, "download_coops")

    mdir  = model_dir(cfg)
    fix   = mdir / "fix"
    station_in = fix / "station.in"
    if not station_in.exists():
        print(f"ERROR: station.in not found: {station_in}")
        sys.exit(1)

    datum  = str(cfg.get("coops_datum", "MSL"))
    months = list_months(cfg)

    # Optional restriction of which variables to download.
    wanted = cfg.get("coops_variables")
    wanted_products = None
    if wanted:
        wanted_products = set()
        for tok in wanted:
            p = VAR_TOKEN_TO_PRODUCT.get(str(tok).strip().upper())
            if p:
                wanted_products.add(p)

    stations = parse_station_in(station_in)
    coops_stations = [s for s in stations if s["source"] == "CO-OPS"]

    coops_dir = mdir / "obs" / "coops"
    coops_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  CO-OPS observation download")
    print(f"  station.in: {station_in}")
    print(f"  {len(coops_stations)} valid CO-OPS station(s); "
          f"{len(months)} month(s): {months[0]} -> {months[-1]}")
    print(f"  Water-level datum: {datum}")
    print(f"  Output: {coops_dir}")
    print(f"{'='*60}\n")

    n_ok = n_skip = n_fail = 0
    failures = []

    for st in coops_stations:
        sid   = st["station_id"]
        prods = _products_for_station(st)
        if wanted_products is not None:
            prods &= wanted_products
        if not prods:
            continue
        print(f"  Station {sid} ({st['name']}): products {sorted(prods)}")
        for product in sorted(prods):
            for ym in months:
                status = _download_station_product_month(
                    sid, product, ym, datum, coops_dir)
                if status == "ok":
                    n_ok += 1
                elif status == "skip":
                    n_skip += 1
                else:
                    n_fail += 1
                    failures.append(f"{sid} {product} {ym}")
                # Be gentle with the API (throttling best practice).
                if status == "ok":
                    time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  CO-OPS download complete.")
    print(f"  downloaded={n_ok}  skipped={n_skip}  failed={n_fail}")
    if failures:
        print(f"  Failed/empty (some stations simply lack a product/month):")
        for f in failures[:40]:
            print(f"    {f}")
        if len(failures) > 40:
            print(f"    ... and {len(failures) - 40} more")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Download CO-OPS station observations")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    run_download_coops(cfg)
