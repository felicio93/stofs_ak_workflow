"""
models/schism/postprocess/station_skill.py
===========================================
Phase 5 step "station_skill" (interactive; runs in the swf_plot env).

Compares SCHISM station output (outputs/staout_*) against downloaded station
observations for every valid station in fix/station.in and every variable
named in its VARS bracket, then:

  * plots observed vs. modeled time series (skill metrics in the legend), one
    JPEG per station/variable, and
  * writes a single skill_metrics.csv summarising bias / RMSE / R^2 per
    station/variable.

Observation sources (selected by the station's SOURCE field in station.in):
  * CO-OPS -> obs/coops/  (download_coops)
  * NDBC   -> obs/ndbc/   (download_ndbc)

Works for all grouping modes (monthly, ndays/weekly/daily).
Group IDs are either YYYYMM (monthly) or YYYYMMDD (ndays).

staout mapping (SCHISM fixed order):
    staout_1 = elev            <- WL/ELEV
    staout_2 = air pressure    <- AIR_PRESSURE
    staout_3 = windx (u, eastward)
    staout_4 = windy (v, northward)
    staout_5 = T               <- water temperature
"""

import argparse
import re
from datetime import date
from pathlib import Path

from workflow.core.config import (
    load_config,
    model_dir,
    list_groups,
    group_date_range,
)
from workflow.core.station_parser import parse_station_in

# Variable token -> staout file number, CO-OPS product name, label, unit, color
VAR_PLAN = {
    "WL":           {"staout": 1, "product": "water_level",
                     "label": "Water Level",
                     "unit": "m",    "color": "blue"},
    "ELEV":         {"staout": 1, "product": "water_level",
                     "label": "Water Level",
                     "unit": "m",    "color": "blue"},
    "T":            {"staout": 5, "product": "water_temperature",
                     "label": "Water Temperature",
                     "unit": "degC", "color": "red"},
    "TEMP":         {"staout": 5, "product": "water_temperature",
                     "label": "Water Temperature",
                     "unit": "degC", "color": "red"},
    "AIR_PRESSURE": {"staout": 2, "product": "air_pressure",
                     "label": "Air Pressure",
                     "unit": "mbar", "color": "green"},
    "AIRPRESSURE":  {"staout": 2, "product": "air_pressure",
                     "label": "Air Pressure",
                     "unit": "mbar", "color": "green"},
    "PATM":         {"staout": 2, "product": "air_pressure",
                     "label": "Air Pressure",
                     "unit": "mbar", "color": "green"},
    "PRESSURE":     {"staout": 2, "product": "air_pressure",
                     "label": "Air Pressure",
                     "unit": "mbar", "color": "green"},
}

# Wind handled separately
WIND_TOKENS = {"WIND", "WINDX", "WINDY"}
WIND_COMPONENTS = [
    ("windx", 3, "u"),
    ("windy", 4, "v"),
]

# NDBC stdmet column -> variable token mapping
NDBC_COL_MAP = {
    "T":            "WTMP",
    "TEMP":         "WTMP",
    "AIR_PRESSURE": "PRES",
    "AIRPRESSURE":  "PRES",
    "PATM":         "PRES",
    "PRESSURE":     "PRES",
}


def _resample_rule(cfg: dict) -> str:
    """Return the pandas resample rule, normalising deprecated uppercase
    aliases. Pandas 2.2+ requires lowercase offset aliases (h, min, s).
    """
    rule = str(cfg.get("station_skill_resample", "1h"))
    rule = re.sub(r'^(\d*)H$',
                  lambda m: (m.group(1) or '1') + 'h',   rule)
    rule = re.sub(r'^(\d*)T$',
                  lambda m: (m.group(1) or '1') + 'min', rule)
    rule = re.sub(r'^(\d*)S$',
                  lambda m: (m.group(1) or '1') + 's',   rule)
    return rule


def _skill_window(cfg: dict):
    start = cfg.get("station_skill_start") or cfg["start_date"]
    end   = cfg.get("station_skill_end")   or cfg["end_date"]
    return str(start), str(end)


def _ndbc_token_filter(cfg: dict):
    wanted = cfg.get("ndbc_variables")
    if not wanted:
        return None
    return {str(t).strip().upper() for t in wanted}


def _load_model_staout(cfg, staout_num: int, stations: list,
                       start_str, end_str):
    """Stitch staout_N across all run groups into a DataFrame indexed
    by UTC datetime.

    Uses group_date_range to get the correct start date for each group,
    which works for both monthly (YYYYMM) and ndays (YYYYMMDD) IDs.
    start_day and start_hour are still read from param.nml so that
    the first group's cold-start offset is respected.
    """
    import pandas as pd

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    idx_to_id = {s["line_index"]: s["station_id"]
                 for s in stations}

    groups = list_groups(cfg)
    frames = []

    for gid in groups:
        f = (mdir / f"R{pid}" / f"R{pid}_{gid}"
             / "outputs" / f"staout_{staout_num}")
        if not f.exists() or f.stat().st_size == 0:
            continue

        # Use group_date_range for correct year/month/day regardless
        # of whether gid is YYYYMM or YYYYMMDD.
        gstart, _ = group_date_range(cfg, gid)
        year  = gstart.year
        month = gstart.month

        # start_day and start_hour may differ from group start
        # for the first group (cold-start offset); read from param.nml.
        start_day  = gstart.day
        start_hour = 0
        param_nml = (mdir / f"I{pid}" / f"I{pid}_{gid}"
                     / "param.nml")
        if param_nml.exists():
            text = param_nml.read_text()
            m = re.search(
                r'^\s*start_day\s*=\s*([^\s!]+)',
                text, re.IGNORECASE | re.MULTILINE)
            if m:
                start_day = int(float(m.group(1)))
            m = re.search(
                r'^\s*start_hour\s*=\s*([^\s!]+)',
                text, re.IGNORECASE | re.MULTILINE)
            if m:
                start_hour = int(float(m.group(1)))

        base = pd.Timestamp(year=year, month=month,
                            day=start_day, hour=start_hour)

        df = pd.read_csv(f, sep=r"\s+", header=None)
        df = df.apply(pd.to_numeric, errors="coerce")
        tvals = df[0].dropna()
        if tvals.empty:
            continue

        df["datetime"] = base + pd.to_timedelta(df[0], unit="s")
        df = df.drop(columns=[0])
        rename = {}
        for col in df.columns:
            if col == "datetime":
                continue
            rename[col] = idx_to_id.get(col, col)
        df = df.rename(columns=rename)
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    model = pd.concat(frames, ignore_index=True)
    model = model.set_index("datetime").sort_index()
    model.index = model.index.tz_localize(None)
    model = model[~model.index.duplicated(keep="first")]
    return model.loc[start_str:end_str]


def _load_obs_series(cfg, station_id, product,
                     start_str, end_str):
    """Load + concat monthly CO-OPS CSVs for one station/product.

    CO-OPS obs are always stored one CSV per calendar month
    (YYYYMM), regardless of the project grouping.
    """
    import pandas as pd
    from dateutil.relativedelta import relativedelta

    mdir      = model_dir(cfg)
    coops_dir = mdir / "obs" / "coops"

    # Collect calendar months in the skill window
    s = date.fromisoformat(start_str[:10])
    e = date.fromisoformat(end_str[:10])
    months = []
    cur = date(s.year, s.month, 1)
    end = date(e.year, e.month, 1)
    while cur <= end:
        months.append(cur.strftime("%Y%m"))
        cur += relativedelta(months=1)

    parts = []
    for ym in months:
        f = coops_dir / f"{station_id}_{product}_{ym}.csv"
        if f.exists() and f.stat().st_size > 0:
            parts.append(pd.read_csv(f))
    if not parts:
        return None

    obs = pd.concat(parts, ignore_index=True)
    if "t" not in obs.columns:
        return None
    obs["datetime"] = pd.to_datetime(obs["t"], errors="coerce")
    obs = obs.dropna(subset=["datetime"]).set_index(
        "datetime").sort_index()
    obs.index = obs.index.tz_localize(None)
    obs = obs[~obs.index.duplicated(keep="first")]
    return obs.loc[start_str:end_str]


def _wind_uv(speed, direction):
    """Convert wind speed (m/s) + meteorological direction to u, v."""
    import numpy as np
    rad = np.deg2rad(direction)
    u = -speed * np.sin(rad)
    v = -speed * np.cos(rad)
    return u, v


def _load_ndbc_frame(cfg, station_id, start_str, end_str):
    """Load + concat per-year NDBC CSVs for one station."""
    import pandas as pd

    mdir     = model_dir(cfg)
    ndbc_dir = mdir / "obs" / "ndbc"
    start_year = int(str(start_str)[:4])
    end_year   = int(str(end_str)[:4])

    parts = []
    for year in range(start_year, end_year + 1):
        f = ndbc_dir / f"{station_id}_{year}.csv"
        if f.exists() and f.stat().st_size > 0:
            parts.append(pd.read_csv(f))
    if not parts:
        return None

    df = pd.concat(parts, ignore_index=True)
    if "datetime" not in df.columns:
        return None
    df["datetime"] = pd.to_datetime(df["datetime"],
                                    errors="coerce")
    df = df.dropna(subset=["datetime"]).set_index(
        "datetime").sort_index()
    df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="first")]
    return df.loc[start_str:end_str]


def _metrics(obs_series, mod_series, rule):
    """Resample both to rule, align, return metrics tuple."""
    import numpy as np
    import pandas as pd

    o  = obs_series.resample(rule).mean()
    m  = mod_series.resample(rule).mean()
    df = pd.concat([o, m], axis=1).dropna()
    df.columns = ["obs", "mod"]
    if len(df) < 2:
        return None
    mean_obs  = float(np.mean(df["obs"]))
    bias      = float(np.mean(df["mod"] - df["obs"]))
    rmse      = float(np.sqrt(
        np.mean((df["mod"] - df["obs"]) ** 2)))
    denom_std = df["obs"].std() * df["mod"].std()
    r2 = (float(np.corrcoef(df["obs"],
                             df["mod"])[0, 1] ** 2)
          if denom_std != 0 else float("nan"))
    return len(df), mean_obs, bias, rmse, r2


def _plot(obs_idx, obs_vals, mod_idx, mod_vals,
          title, ylabel, color, mod_label, out_jpg):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, ax = plt.subplots(figsize=(15, 3))
    ax.plot(obs_idx, obs_vals, label="Observed",
            color="k",    linewidth=1)
    ax.plot(mod_idx, mod_vals, label=mod_label,
            color=color,  linewidth=1)
    ax.set_title(title, fontsize=10)
    ax.set_ylabel(ylabel)
    ax.grid(True, linestyle="--", color="gray",
            linewidth=0.5)
    ax.legend(loc="best")
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%d-%b"))
    fig.tight_layout()
    fig.savefig(out_jpg, dpi=150)
    plt.close(fig)


def _obs_for_variable(source, tok, cfg, sid,
                      start_str, end_str):
    """Return the observed series for a station/variable."""
    import pandas as pd

    T = tok.strip().upper()

    # ---- CO-OPS ----
    if source == "CO-OPS":
        if T in WIND_TOKENS:
            obs = _load_obs_series(
                cfg, sid, "wind", start_str, end_str)
            if (obs is None or obs.empty
                    or "s" not in obs or "d" not in obs):
                return None
            spd = pd.to_numeric(obs["s"], errors="coerce")
            drc = pd.to_numeric(obs["d"], errors="coerce")
            u, v = _wind_uv(spd, drc)
            return ("wind",
                    {"u": u.dropna(), "v": v.dropna()})
        plan = VAR_PLAN.get(T)
        if plan is None:
            return None
        obs = _load_obs_series(
            cfg, sid, plan["product"], start_str, end_str)
        if obs is None or obs.empty or "v" not in obs:
            return None
        return ("single",
                pd.to_numeric(
                    obs["v"], errors="coerce").dropna())

    # ---- NDBC ----
    frame = _load_ndbc_frame(cfg, sid, start_str, end_str)
    if frame is None or frame.empty:
        return None

    if T in WIND_TOKENS:
        if ("WSPD" not in frame.columns
                or "WDIR" not in frame.columns):
            return None
        spd = pd.to_numeric(frame["WSPD"], errors="coerce")
        drc = pd.to_numeric(frame["WDIR"], errors="coerce")
        u, v = _wind_uv(spd, drc)
        return ("wind",
                {"u": u.dropna(), "v": v.dropna()})

    ndbc_col = NDBC_COL_MAP.get(T)
    if ndbc_col is None or ndbc_col not in frame.columns:
        return None
    return ("single",
            pd.to_numeric(
                frame[ndbc_col], errors="coerce").dropna())


def _assess_station(st, source, cfg, model_frame, rule,
                    start_str, end_str, out_dir, rows,
                    ndbc_token_filter=None):
    """Assess one station against the model."""
    sid     = st["station_id"]
    name    = st["name"]
    src_tag = source

    for tok in st["vars"]:
        T = tok.strip().upper()

        if source == "NDBC" and ndbc_token_filter is not None:
            if T not in ndbc_token_filter:
                continue

        if source == "NDBC" and T in ("WL", "ELEV"):
            continue

        obs = _obs_for_variable(
            source, tok, cfg, sid, start_str, end_str)
        if obs is None:
            print(f"  {src_tag} {sid} {T}: no obs, skipping.")
            continue
        kind, payload = obs

        # ---- Wind ----
        if kind == "wind":
            for comp_label, staout_num, key \
                    in WIND_COMPONENTS:
                mdl = model_frame(staout_num)
                if mdl.empty or sid not in mdl.columns:
                    print(f"  {src_tag} {sid} {comp_label}: "
                          f"no model column, skipping.")
                    continue
                o   = payload[key]
                m   = mdl[sid]
                res = _metrics(o, m, rule)
                if res is None:
                    print(f"  {src_tag} {sid} {comp_label}: "
                          f"<2 overlapping points, skipping.")
                    continue
                n, mean_obs, bias, rmse, r2 = res
                mod_label = (
                    f"Model [R\u00b2: {r2:.2f}; "
                    f"RMSE: {rmse:.2f} m/s; "
                    f"Bias: {bias:.2f} m/s]")
                _plot(o.index, o.values,
                      m.index, m.values,
                      f"{src_tag} ({sid}): {name} \u2014 "
                      f"Wind {comp_label} (m/s)",
                      f"{comp_label} (m/s)",
                      "purple", mod_label,
                      out_dir / f"{sid}_{comp_label}.jpg")
                rows.append(dict(
                    station_id=sid, name=name,
                    source=src_tag,
                    variable=comp_label, n_points=n,
                    mean_obs=mean_obs, bias=bias,
                    rmse=rmse, r2=r2,
                    start=start_str, end=end_str))
                print(f"  {src_tag} {sid} {comp_label}: "
                      f"n={n} bias={bias:.3f} "
                      f"rmse={rmse:.3f} r2={r2:.3f}")
            continue

        # ---- Scalar ----
        plan = VAR_PLAN.get(T)
        if plan is None:
            continue
        o   = payload
        mdl = model_frame(plan["staout"])
        if mdl.empty or sid not in mdl.columns:
            print(f"  {src_tag} {sid} {T}: no model column "
                  f"(staout_{plan['staout']}), skipping.")
            continue
        if mdl[sid].std() < 1e-3:
            print(f"  WARNING: {src_tag} {sid} {T} model "
                  f"series is flat (dry node?).")
        m   = mdl[sid]
        res = _metrics(o, m, rule)
        if res is None:
            print(f"  {src_tag} {sid} {T}: <2 overlapping "
                  f"points, skipping.")
            continue
        n, mean_obs, bias, rmse, r2 = res
        unit      = plan["unit"]
        mod_label = (
            f"Model [R\u00b2: {r2:.2f}; "
            f"RMSE: {rmse:.2f} {unit}; "
            f"Bias: {bias:.2f} {unit}]")
        _plot(o.index, o.values,
              m.index, m.values,
              f"{src_tag} ({sid}): {name} \u2014 "
              f"{plan['label']}",
              f"{plan['label']} ({unit})",
              plan["color"], mod_label,
              out_dir / f"{sid}_{plan['product']}.jpg")
        rows.append(dict(
            station_id=sid, name=name, source=src_tag,
            variable=plan["product"], n_points=n,
            mean_obs=mean_obs, bias=bias, rmse=rmse, r2=r2,
            start=start_str, end=end_str))
        print(f"  {src_tag} {sid} {T}: n={n} "
              f"bias={bias:.3f} rmse={rmse:.3f} "
              f"r2={r2:.3f}")


def run_station_skill(cfg: dict, config_dir=None):
    import pandas as pd

    pid        = cfg["project_id"]
    mdir       = model_dir(cfg)
    station_in = mdir / "fix" / "station.in"
    if not station_in.exists():
        print(f"ERROR: station.in not found: {station_in}")
        return

    start_str, end_str   = _skill_window(cfg)
    rule                 = _resample_rule(cfg)
    ndbc_token_filter    = _ndbc_token_filter(cfg)

    stations = parse_station_in(station_in)
    coops    = [s for s in stations
                if s["source"] == "CO-OPS"]
    ndbc     = [s for s in stations
                if s["source"] == "NDBC"]

    out_dir = mdir / f"P{pid}" / f"P{pid}_station_skill"
    out_dir.mkdir(parents=True, exist_ok=True)

    grouping = cfg.get("grouping", "monthly")
    groups   = list_groups(cfg)

    print(f"\n{'='*60}")
    print(f"  Station skill assessment")
    print(f"  Grouping  : {grouping}"
          + (f"  (group_ndays={cfg['group_ndays']})"
             if grouping == "ndays" else ""))
    print(f"  Groups    : {groups[0]} -> {groups[-1]}")
    print(f"  Window    : {start_str} -> {end_str}")
    print(f"  Resample  : {rule}")
    print(f"  CO-OPS stations: {len(coops)}")
    print(f"  NDBC stations  : {len(ndbc)}")
    if ndbc_token_filter:
        print(f"  NDBC variable filter: "
              f"{sorted(ndbc_token_filter)}")
    else:
        print(f"  NDBC variable filter: none "
              f"(use each station's VARS bracket)")
    print(f"  Output: {out_dir}")
    print(f"{'='*60}\n")

    # Cache model staout frames — loaded once per staout number
    model_cache = {}

    def model_frame(staout_num):
        if staout_num not in model_cache:
            model_cache[staout_num] = _load_model_staout(
                cfg, staout_num, stations,
                start_str, end_str)
        return model_cache[staout_num]

    rows = []

    for st in coops:
        _assess_station(
            st, "CO-OPS", cfg, model_frame, rule,
            start_str, end_str, out_dir, rows,
            ndbc_token_filter=None)

    for st in ndbc:
        _assess_station(
            st, "NDBC", cfg, model_frame, rule,
            start_str, end_str, out_dir, rows,
            ndbc_token_filter=ndbc_token_filter)

    if rows:
        df = pd.DataFrame(rows, columns=[
            "station_id", "name", "source", "variable",
            "n_points", "mean_obs", "bias", "rmse", "r2",
            "start", "end"])
        csv_path = out_dir / "skill_metrics.csv"
        df.to_csv(csv_path, index=False)
        print(f"\n  Wrote {len(rows)} skill row(s) "
              f"-> {csv_path}")
    else:
        print("\n  No station/variable pairs produced metrics.")
        print("  Check that:")
        print("    1. download_coops / download_ndbc have run")
        print("    2. station.in uses ![VARS] format "
              "(no space after !)")
        print("    3. The skill window overlaps the downloaded "
              "data")

    (out_dir / "station_skill.done").touch()
    print(f"\n{'='*60}")
    print(f"  Station skill assessment complete. "
          f"Plots + CSV in {out_dir}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="CO-OPS/NDBC station skill assessment")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    run_station_skill(cfg, args.config)
