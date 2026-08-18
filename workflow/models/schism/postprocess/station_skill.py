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
  * CO-OPS -> obs/coops/  (download_coops): water_level, water_temperature,
              air_pressure, wind (speed/dir -> u/v).
  * NDBC   -> obs/ndbc/   (download_ndbc): WTMP -> T, PRES -> air_pressure,
              WSPD/WDIR -> wind (u/v). NDBC has no water level.

Only the variables listed in each station's VARS bracket are assessed.

Outputs go to:
    M{ID}/P{ID}/P{ID}_station_skill/{station_id}_{var}.jpg
    M{ID}/P{ID}/P{ID}_station_skill/skill_metrics.csv

The comparison window defaults to the run's [start_date, end_date] but can be
narrowed via station_skill_start / station_skill_end in postprocess.yaml, so
this step can be re-run for different sub-periods without re-downloading.

staout mapping (SCHISM fixed order; hard-coded):
    staout_1 = elev            <- WL
    staout_2 = air pressure    <- air_pressure
    staout_3 = windx (u, eastward)
    staout_4 = windy (v, northward)
    staout_5 = T               <- water_temperature
    (staout_6 = S, 7 = u, 8 = v, 9 = w — not compared here)

The station's value in each staout_* file is the column whose position equals
the station's 1-based line index in station.in (see core.station_parser).

Wind: CO-OPS reports speed (s, m/s) + direction (d, degrees FROM). This is
converted to eastward/northward components to match SCHISM windx/windy:
    u = -s * sin(d * pi/180)
    v = -s * cos(d * pi/180)
and obs u/v are compared to staout_3 / staout_4 respectively.
"""

import argparse
from datetime import date
from pathlib import Path

from workflow.core.config import load_config, model_dir, list_months
from workflow.core.station_parser import parse_station_in


# variable-token -> (staout file number, obs product, label, unit, plot color)
# Wind is handled specially (two components).
VAR_PLAN = {
    "WL":            {"staout": 1, "product": "water_level",       "label": "Water Level", "unit": "m",  "color": "blue"},
    "ELEV":          {"staout": 1, "product": "water_level",       "label": "Water Level", "unit": "m",  "color": "blue"},
    "T":             {"staout": 5, "product": "water_temperature", "label": "Water Temperature", "unit": "degC", "color": "red"},
    "TEMP":          {"staout": 5, "product": "water_temperature", "label": "Water Temperature", "unit": "degC", "color": "red"},
    "AIR_PRESSURE":  {"staout": 2, "product": "air_pressure",      "label": "Air Pressure", "unit": "mbar", "color": "green"},
    "AIRPRESSURE":   {"staout": 2, "product": "air_pressure",      "label": "Air Pressure", "unit": "mbar", "color": "green"},
    "PATM":          {"staout": 2, "product": "air_pressure",      "label": "Air Pressure", "unit": "mbar", "color": "green"},
    "PRESSURE":      {"staout": 2, "product": "air_pressure",      "label": "Air Pressure", "unit": "mbar", "color": "green"},
}

# Wind maps to two staout files / two components.
WIND_TOKENS = {"WIND", "WINDX", "WINDY"}
WIND_COMPONENTS = [
    # (component label, staout file, obs derivation key)
    ("windx", 3, "u"),
    ("windy", 4, "v"),
]


def _resample_rule(cfg: dict) -> str:
    """Return the pandas resample rule, normalising deprecated uppercase aliases.

    Pandas 2.2+ requires lowercase offset aliases (h, min, s, ...).
    Older configs may use uppercase (H, T, S). Map the common ones so
    station_skill works on both pandas <2.2 and >=2.2.
    """
    import re
    rule = str(cfg.get("station_skill_resample", "1h"))
    # H -> h  (hours)
    rule = re.sub(r'^(\d*)H$', lambda m: (m.group(1) or '1') + 'h', rule)
    # T -> min (minutes)
    rule = re.sub(r'^(\d*)T$', lambda m: (m.group(1) or '1') + 'min', rule)
    # S -> s  (seconds)
    rule = re.sub(r'^(\d*)S$', lambda m: (m.group(1) or '1') + 's', rule)
    return rule


def _skill_window(cfg: dict):
    start = cfg.get("station_skill_start") or cfg["start_date"]
    end   = cfg.get("station_skill_end")   or cfg["end_date"]
    return str(start), str(end)


def _load_model_staout(cfg, staout_num: int, stations: list, start_str, end_str):
    """Stitch a given staout_N across all run months into a DataFrame indexed
    by UTC datetime, with columns renamed to each station's id (by line index).

    Returns a pandas DataFrame (may be empty if no files found).
    """
    import pandas as pd

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    start_dt = pd.to_datetime(start_str)
    end_dt   = pd.to_datetime(end_str)

    # Column (line_index) -> station_id map for renaming.
    idx_to_id = {s["line_index"]: s["station_id"] for s in stations}

    # Load every month that overlaps [start, end]; staout column 0 is time (s).
    months = list_months(cfg)
    frames = []
    for ym in months:
        f = mdir / f"R{pid}" / f"R{pid}_{ym}" / "outputs" / f"staout_{staout_num}"
        if not f.exists() or f.stat().st_size == 0:
            continue
        month_dt = pd.to_datetime(ym + "01")
        df = pd.read_csv(f, sep=r"\s+", header=None)
        df = df.apply(pd.to_numeric, errors="coerce")
        # Column 0 = seconds since the month's start (SCHISM restarts each month
        # from t=0 under ihot=1 with per-month base date).
        tvals = df[0].dropna()
        if tvals.empty:
            continue
        base = month_dt
        df["datetime"] = base + pd.to_timedelta(df[0], unit="s")
        df = df.drop(columns=[0])
        # Rename data columns (1-based) to station ids.
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


def _load_obs_series(cfg, station_id, product, start_str, end_str):
    """Load + concat monthly CO-OPS CSVs for one station/product into a
    DataFrame indexed by UTC datetime. Returns None if nothing found.

    For single-value products the returned frame has a 'v' column; for wind it
    has 's' and 'd' columns.
    """
    import pandas as pd

    mdir = model_dir(cfg)
    coops_dir = mdir / "obs" / "coops"
    months = list_months(cfg)

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
    obs = obs.dropna(subset=["datetime"]).set_index("datetime").sort_index()
    obs.index = obs.index.tz_localize(None)
    obs = obs[~obs.index.duplicated(keep="first")]
    return obs.loc[start_str:end_str]


def _wind_uv(speed, direction):
    """Convert wind speed (m/s) + meteorological direction (deg FROM) to
    eastward (u) and northward (v) components, matching SCHISM windx/windy."""
    import numpy as np
    rad = np.deg2rad(direction)
    u = -speed * np.sin(rad)
    v = -speed * np.cos(rad)
    return u, v


# NDBC stdmet column mapping: which stdmet column supplies each variable.
#   T (water temperature)  -> WTMP
#   air_pressure           -> PRES
#   wind speed / direction -> WSPD / WDIR  (converted to u/v)
# NDBC does not provide water level.
def _load_ndbc_frame(cfg, station_id, start_str, end_str):
    """Load + concat per-year NDBC CSVs for one station into a DataFrame
    indexed by UTC datetime with the stdmet columns. Returns None if none found.
    """
    import pandas as pd

    mdir = model_dir(cfg)
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
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
    df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="first")]
    return df.loc[start_str:end_str]


def _metrics(obs_series, mod_series, rule):
    """Resample both to `rule`, align, and return (n, mean_obs, bias, rmse, r2).
    Returns None if fewer than 2 overlapping points."""
    import numpy as np
    import pandas as pd

    o = obs_series.resample(rule).mean()
    m = mod_series.resample(rule).mean()
    df = pd.concat([o, m], axis=1).dropna()
    df.columns = ["obs", "mod"]
    if len(df) < 2:
        return None
    mean_obs = float(np.mean(df["obs"]))
    bias = float(np.mean(df["mod"] - df["obs"]))
    rmse = float(np.sqrt(np.mean((df["mod"] - df["obs"]) ** 2)))
    denom_std = df["obs"].std() * df["mod"].std()
    if denom_std == 0:
        r2 = float("nan")
    else:
        r2 = float(np.corrcoef(df["obs"], df["mod"])[0, 1] ** 2)
    return len(df), mean_obs, bias, rmse, r2


def _plot(obs_idx, obs_vals, mod_idx, mod_vals, title, ylabel, color,
          mod_label, out_jpg):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, ax = plt.subplots(figsize=(15, 3))
    ax.plot(obs_idx, obs_vals, label="Observed", color="k", linewidth=1)
    ax.plot(mod_idx, mod_vals, label=mod_label, color=color, linewidth=1)
    ax.set_title(title, fontsize=10)
    ax.set_ylabel(ylabel)
    ax.grid(True, linestyle="--", color="gray", linewidth=0.5)
    ax.legend(loc="best")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d-%b"))
    fig.tight_layout()
    fig.savefig(out_jpg, dpi=150)
    plt.close(fig)


def _obs_for_variable(source, tok, cfg, sid, start_str, end_str):
    """Return the observed series (or dict for wind) for a station/variable.

    For CO-OPS: single-value products read the 'v' column; wind reads 's','d'.
    For NDBC: reads the stdmet per-year frame; WTMP->T, PRES->air_pressure,
    WSPD/WDIR->wind.

    Returns one of:
      ("single", pandas.Series)                        for scalar variables
      ("wind",   {"u": Series, "v": Series})           for wind
      None                                             if no obs
    """
    import pandas as pd

    T = tok.strip().upper()

    if source == "CO-OPS":
        if T in WIND_TOKENS:
            obs = _load_obs_series(cfg, sid, "wind", start_str, end_str)
            if obs is None or obs.empty or "s" not in obs or "d" not in obs:
                return None
            spd = pd.to_numeric(obs["s"], errors="coerce")
            drc = pd.to_numeric(obs["d"], errors="coerce")
            u, v = _wind_uv(spd, drc)
            return ("wind", {"u": u.dropna(), "v": v.dropna()})
        plan = VAR_PLAN.get(T)
        if plan is None:
            return None
        obs = _load_obs_series(cfg, sid, plan["product"], start_str, end_str)
        if obs is None or obs.empty or "v" not in obs:
            return None
        return ("single", pd.to_numeric(obs["v"], errors="coerce").dropna())

    # --- NDBC ---
    frame = _load_ndbc_frame(cfg, sid, start_str, end_str)
    if frame is None or frame.empty:
        return None
    if T in WIND_TOKENS:
        if "WSPD" not in frame or "WDIR" not in frame:
            return None
        spd = pd.to_numeric(frame["WSPD"], errors="coerce")
        drc = pd.to_numeric(frame["WDIR"], errors="coerce")
        u, v = _wind_uv(spd, drc)
        return ("wind", {"u": u.dropna(), "v": v.dropna()})
    # scalar: map token -> NDBC stdmet column
    ndbc_col = {"T": "WTMP", "TEMP": "WTMP",
                "AIR_PRESSURE": "PRES", "AIRPRESSURE": "PRES",
                "PATM": "PRES", "PRESSURE": "PRES"}.get(T)
    if ndbc_col is None or ndbc_col not in frame:
        return None
    return ("single", pd.to_numeric(frame[ndbc_col], errors="coerce").dropna())


def _assess_station(st, source, cfg, model_frame, rule,
                    start_str, end_str, out_dir, rows):
    """Assess one station (all its VARS) against the model and append rows."""
    sid  = st["station_id"]
    name = st["name"]
    src_tag = source

    for tok in st["vars"]:
        T = tok.strip().upper()
        obs = _obs_for_variable(source, tok, cfg, sid, start_str, end_str)
        if obs is None:
            # WL is not available from NDBC; unsupported tokens (CU/S) skipped.
            if not (source == "NDBC" and T in ("WL", "ELEV")):
                print(f"  {src_tag} {sid} {T}: no obs, skipping.")
            continue
        kind, payload = obs

        if kind == "wind":
            for comp_label, staout_num, key in WIND_COMPONENTS:
                mdl = model_frame(staout_num)
                if mdl.empty or sid not in mdl.columns:
                    print(f"  {src_tag} {sid} {comp_label}: no model column, skipping.")
                    continue
                o = payload[key]
                m = mdl[sid]
                res = _metrics(o, m, rule)
                if res is None:
                    print(f"  {src_tag} {sid} {comp_label}: <2 overlapping points, skipping.")
                    continue
                n, mean_obs, bias, rmse, r2 = res
                mod_label = (f"Model [R\u00b2: {r2:.2f}; RMSE: {rmse:.2f} m/s; "
                             f"Bias: {bias:.2f} m/s]")
                _plot(o.index, o.values, m.index, m.values,
                      f"{src_tag} ({sid}): {name} \u2014 Wind {comp_label} (m/s)",
                      f"{comp_label} (m/s)", "purple", mod_label,
                      out_dir / f"{sid}_{comp_label}.jpg")
                rows.append(dict(station_id=sid, name=name, source=src_tag,
                                 variable=comp_label, n_points=n,
                                 mean_obs=mean_obs, bias=bias, rmse=rmse,
                                 r2=r2, start=start_str, end=end_str))
                print(f"  {src_tag} {sid} {comp_label}: n={n} bias={bias:.3f} "
                      f"rmse={rmse:.3f} r2={r2:.3f}")
            continue

        # --- single-value variable ---
        plan = VAR_PLAN.get(T)
        if plan is None:
            continue
        o = payload
        mdl = model_frame(plan["staout"])
        if mdl.empty or sid not in mdl.columns:
            print(f"  {src_tag} {sid} {T}: no model column "
                  f"(staout_{plan['staout']}), skipping.")
            continue
        m = mdl[sid]
        if m.std() < 1e-3:
            print(f"  WARNING: {src_tag} {sid} {T} model series is flat (dry node?).")
        res = _metrics(o, m, rule)
        if res is None:
            print(f"  {src_tag} {sid} {T}: <2 overlapping points, skipping.")
            continue
        n, mean_obs, bias, rmse, r2 = res
        unit = plan["unit"]
        mod_label = (f"Model [R\u00b2: {r2:.2f}; RMSE: {rmse:.2f} {unit}; "
                     f"Bias: {bias:.2f} {unit}]")
        _plot(o.index, o.values, m.index, m.values,
              f"{src_tag} ({sid}): {name} \u2014 {plan['label']}",
              f"{plan['label']} ({unit})", plan["color"], mod_label,
              out_dir / f"{sid}_{plan['product']}.jpg")
        rows.append(dict(station_id=sid, name=name, source=src_tag,
                         variable=plan["product"], n_points=n,
                         mean_obs=mean_obs, bias=bias, rmse=rmse, r2=r2,
                         start=start_str, end=end_str))
        print(f"  {src_tag} {sid} {T}: n={n} bias={bias:.3f} "
              f"rmse={rmse:.3f} r2={r2:.3f}")


def run_station_skill(cfg: dict, config_dir=None):
    import pandas as pd

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
    station_in = mdir / "fix" / "station.in"
    if not station_in.exists():
        print(f"ERROR: station.in not found: {station_in}")
        return

    start_str, end_str = _skill_window(cfg)
    rule = _resample_rule(cfg)

    stations = parse_station_in(station_in)
    coops = [s for s in stations if s["source"] == "CO-OPS"]
    ndbc  = [s for s in stations if s["source"] == "NDBC"]

    out_dir = mdir / f"P{pid}" / f"P{pid}_station_skill"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Station skill assessment")
    print(f"  Window: {start_str} -> {end_str}   resample: {rule}")
    print(f"  {len(coops)} CO-OPS + {len(ndbc)} NDBC station(s)   Output: {out_dir}")
    print(f"{'='*60}\n")

    # Cache model staout frames by staout number (loaded on demand).
    model_cache = {}

    def model_frame(staout_num):
        if staout_num not in model_cache:
            model_cache[staout_num] = _load_model_staout(
                cfg, staout_num, stations, start_str, end_str)
        return model_cache[staout_num]

    rows = []   # skill_metrics.csv rows

    # --- CO-OPS stations ---
    for st in coops:
        _assess_station(st, "CO-OPS", cfg, model_frame, rule,
                        start_str, end_str, out_dir, rows)

    # --- NDBC stations ---
    for st in ndbc:
        _assess_station(st, "NDBC", cfg, model_frame, rule,
                        start_str, end_str, out_dir, rows)

    # --- write skill_metrics.csv ---
    if rows:
        df = pd.DataFrame(rows, columns=[
            "station_id", "name", "source", "variable", "n_points",
            "mean_obs", "bias", "rmse", "r2", "start", "end"])
        csv_path = out_dir / "skill_metrics.csv"
        df.to_csv(csv_path, index=False)
        print(f"\n  Wrote {len(rows)} skill row(s) -> {csv_path}")
    else:
        print("\n  No station/variable pairs produced metrics "
              "(check download_coops/download_ndbc ran and the window overlaps "
              "the data).")

    print(f"\n{'='*60}")
    print(f"  Station skill assessment complete. Plots + CSV in {out_dir}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CO-OPS station skill assessment")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    run_station_skill(cfg, args.config)
