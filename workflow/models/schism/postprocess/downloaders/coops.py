"""
models/schism/postprocess/downloaders/coops.py
==============================================
Phase 5 step "download_coops" (DTN, internet required).

Downloads NOAA CO-OPS observations for the stations listed in the SCHISM
fix/station.in file and stores them, one CSV per station / product / month,
under:

    M{ID}/obs/coops/{station_id}_{product}_{YYYYMM}.csv

CO-OPS 6-minute data is limited to one month per API request.
Months already present are skipped (resume-safe).

Works for all grouping modes (monthly, ndays/weekly/daily). The CO-OPS
download always operates on calendar months regardless of the project
grouping — it collects the unique calendar months that span the project
date range and downloads one CSV per station/product/month.
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

from dateutil.relativedelta import relativedelta

from workflow.core.config import (
    load_config,
    model_dir,
)
from workflow.core.environment import check_dtn, check_active_env
from workflow.core.station_parser import parse_station_in


API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
APP = "stofs_ak_workflow"

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

SUPPORTED_PRODUCTS = {
    "water_level", "water_temperature",
    "air_pressure", "wind",
}

PRODUCT_FIELDS = {
    "water_level":       ["t", "v"],
    "water_temperature": ["t", "v"],
    "air_pressure":      ["t", "v"],
    "wind":              ["t", "s", "d", "g"],
}


# =============================================================================
# Calendar month helpers
# =============================================================================

def _calendar_months(cfg: dict) -> list:
    """Return sorted list of 'YYYYMM' strings covering the project
    date range.

    Always uses calendar months regardless of the project grouping —
    CO-OPS data is stored one CSV per calendar month.
    """
    start = date.fromisoformat(cfg["start_date"])
    end   = date.fromisoformat(cfg["end_date"])

    months  = []
    current = date(start.year, start.month, 1)
    last    = date(end.year,   end.month,   1)
    while current <= last:
        months.append(current.strftime("%Y%m"))
        current += relativedelta(months=1)
    return months


# =============================================================================
# Helpers
# =============================================================================

def _products_for_station(station: dict) -> set:
    prods = set()
    for tok in station["vars"]:
        p = VAR_TOKEN_TO_PRODUCT.get(tok.strip().upper())
        if p and p in SUPPORTED_PRODUCTS:
            prods.add(p)
    return prods


def _month_bounds(ym: str):
    """Return (begin_YYYYMMDD, end_YYYYMMDD) for calendar month ym.

    ym must be a 6-character 'YYYYMM' string — never a group ID.
    """
    year  = int(ym[:4])
    month = int(ym[4:6])
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
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"    HTTP error {e.code}: {url}")
        return None
    except urllib.error.URLError as e:
        print(f"    URL error: {e.reason}: {url}")
        return None
    except Exception as e:
        print(f"    request failed: {e}: {url}")
        return None


def _records_to_csv(records: list, fields: list,
                    out_path: Path):
    import csv
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for rec in records:
            w.writerow([rec.get(k, "") for k in fields])
    tmp.replace(out_path)


def _download_station_product_month(
        station_id, product, ym, datum,
        coops_dir: Path) -> str:
    """Download one station/product/calendar-month.

    ym must be a 6-character 'YYYYMM' calendar month string.
    Returns 'ok', 'skip', or 'fail'.
    """
    out = coops_dir / f"{station_id}_{product}_{ym}.csv"
    if out.exists() and out.stat().st_size > 0:
        print(f"    {station_id} {product} {ym}: "
              f"already present, skipping.")
        return "skip"

    begin, end = _month_bounds(ym)
    url  = _build_url(station_id, product,
                      begin, end, datum)
    data = _fetch_json(url)
    if data is None:
        return "fail"
    if "error" in data:
        msg = data["error"].get(
            "message", "unknown error").strip()
        print(f"    {station_id} {product} {ym}: "
              f"CO-OPS says: {msg}")
        return "fail"
    records = data.get("data")
    if not records:
        print(f"    {station_id} {product} {ym}: "
              f"no data returned.")
        return "fail"

    fields = PRODUCT_FIELDS[product]
    _records_to_csv(records, fields, out)
    print(f"    {station_id} {product} {ym}: "
          f"{len(records)} records -> {out.name}")
    return "ok"


# =============================================================================
# Main
# =============================================================================

def run_download_coops(cfg: dict):
    check_dtn("CO-OPS observation download")
    check_active_env(cfg, "download_coops")

    mdir       = model_dir(cfg)
    fix        = mdir / "fix"
    station_in = fix / "station.in"
    if not station_in.exists():
        print(f"ERROR: station.in not found: {station_in}")
        sys.exit(1)

    datum  = str(cfg.get("coops_datum", "MSL"))

    # Always use calendar months — never group IDs
    months   = _calendar_months(cfg)
    grouping = cfg.get("grouping", "monthly")

    wanted = cfg.get("coops_variables")
    wanted_products = None
    if wanted:
        wanted_products = set()
        for tok in wanted:
            p = VAR_TOKEN_TO_PRODUCT.get(
                str(tok).strip().upper())
            if p:
                wanted_products.add(p)

    stations       = parse_station_in(station_in)
    coops_stations = [s for s in stations
                      if s["source"] == "CO-OPS"]

    coops_dir = mdir / "obs" / "coops"
    coops_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  CO-OPS observation download")
    print(f"  Grouping  : {grouping} "
          f"(obs always stored by calendar month)")
    print(f"  station.in: {station_in}")
    print(f"  {len(coops_stations)} valid CO-OPS station(s); "
          f"{len(months)} calendar month(s): "
          f"{months[0]} -> {months[-1]}")
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
        print(f"  Station {sid} ({st['name']}): "
              f"products {sorted(prods)}")
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
                    failures.append(
                        f"{sid} {product} {ym}")
                if status == "ok":
                    time.sleep(0.5)

    print(f"\n{'='*60}")
    print(f"  CO-OPS download complete.")
    print(f"  downloaded={n_ok}  skipped={n_skip}  "
          f"failed={n_fail}")
    if failures:
        print(f"  Failed/empty "
              f"(some stations lack a product/month):")
        for f in failures[:40]:
            print(f"    {f}")
        if len(failures) > 40:
            print(f"    ... and {len(failures) - 40} more")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download CO-OPS station observations")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    run_download_coops(cfg)
