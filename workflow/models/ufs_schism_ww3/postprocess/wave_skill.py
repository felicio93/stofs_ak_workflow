"""
models/ufs_schism_ww3/postprocess/wave_skill.py
=================================================
Phase 5 step "wave_skill" (interactive; runs in swf_plot env).

Compares NDBC wave observations against WW3 station output
(ww3.{YYYYMM}_tab.nc files produced by ww3_ounp) for stations
that have HS, TM01, or DM tokens in their station.in VARS bracket.

WW3 station NetCDF source
--------------------------
  ww3_ounp converts out_pnt.ww3 -> ww3.{YYYYMM}_tab.nc
  (ITYPE=2, OTYPE=2 — mean wave parameters).
  One file per group, written to the run directory by auto_hotstart.py
  after each group completes.

Variable mapping
-----------------
  WW3 var   NDBC col  Description
  --------  --------  -----------
  hs        WVHT      Significant wave height (m)
  tr        APD       Average period = TM01 (s)
  th1m      MWD       Mean wave direction (deg)

Note: NDBC APD = average period ≈ TM01 (spectral moment 0,1).
WW3 ww3_ounp OTYPE=2 writes 'tr' for TM01.
TM02 token in station.in is treated as TM01 alias.

Output
------
  P{ID}/P{ID}_station_skill/{station_id}_{variable}.jpg
  P{ID}/P{ID}_station_skill/wave_skill_metrics.csv
  P{ID}/P{ID}_station_skill/wave_skill.done
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from workflow.core.config import (
    model_dir,
    list_groups,
)
from workflow.core.station_parser import parse_station_in


# ---------------------------------------------------------------------------
# Variable plan
# ---------------------------------------------------------------------------

# Maps station.in VARS token ->
#   (NDBC column, WW3 netcdf varname, label, unit, color)
#
# WW3 ww3_ounp OTYPE=2 variable names:
#   hs    = significant wave height
#   tr    = mean wave period (TM01, from spectral moments 0,1)
#   th1m  = mean wave direction
#   fp    = peak frequency
#   th1p  = peak direction
#   sth1m = directional spreading
#
# NDBC APD = Average Period ≈ TM01
WAVE_VAR_PLAN = {
    "HS":   ("WVHT", "hs",   "Significant Wave Height", "m",   "steelblue"),
    "TM01": ("APD",  "tr",   "Average Wave Period (TM01)", "s", "darkorange"),
    "DM":   ("MWD",  "th1m", "Mean Wave Direction",    "deg",  "seagreen"),
}

# TM02 token treated as TM01 alias (NDBC APD ≈ TM01)
WAVE_VAR_PLAN["TM02"] = WAVE_VAR_PLAN["TM01"]

WAVE_TOKENS = set(WAVE_VAR_PLAN.keys())

# NDBC missing value sentinels
NDBC_WAVE_MISSING = {99.0, 999.0, 9999.0, 99.00, 999.00}


# ---------------------------------------------------------------------------
# WW3 station data loader
# ---------------------------------------------------------------------------

def _ww3_tab_files(cfg: dict) -> list:
    """Return sorted list of ww3.{YYYYMM}_tab.nc files across all groups."""
    pid   = cfg["project_id"]
    mdir  = model_dir(cfg)
    files = []
    for group_id in list_groups(cfg):
        rdir = mdir / f"R{pid}" / f"R{pid}_{group_id}"
        tab_files = sorted(rdir.glob("ww3.*_tab.nc"))
        files.extend(tab_files)
    return files


def _load_ww3_station(cfg: dict,
                      station_name: str,
                      var_name: str,
                      start_str: str,
                      end_str: str) -> pd.Series:
    """Load a WW3 variable for one station from all ww3.*_tab.nc files.

    The ww3_tab NetCDF has dimensions (time, station) with station
    identified by name in the station_name variable (char array).
    Time is in days since 1990-01-01.

    Parameters
    ----------
    cfg          : workflow config dict
    station_name : station name string matching ww3_points.list
    var_name     : WW3 variable name (e.g. 'hs', 'tr', 'th1m')
    start_str    : start date string YYYY-MM-DD
    end_str      : end date string YYYY-MM-DD

    Returns
    -------
    pd.Series with datetime index, or empty Series if not found.
    """
    import netCDF4 as nc4

    tab_files = _ww3_tab_files(cfg)
    if not tab_files:
        return pd.Series(dtype="float64")

    records = []

    for nc_path in tab_files:
        ds = None
        try:
            ds = nc4.Dataset(str(nc_path))

            if var_name not in ds.variables:
                ds.close()
                ds = None
                continue

            # Read station names (char array: station x string40)
            sta_names_raw = ds.variables["station_name"][:]
            sta_names = []
            for i in range(sta_names_raw.shape[0]):
                row = sta_names_raw[i]
                if hasattr(row, 'compressed'):
                    s = "".join(
                        c.decode("utf-8", errors="ignore")
                        if isinstance(c, bytes) else str(c)
                        for c in row.compressed()
                    ).strip()
                else:
                    s = "".join(
                        c.decode("utf-8", errors="ignore")
                        if isinstance(c, bytes) else str(c)
                        for c in row
                    ).strip()
                sta_names.append(s)

            # Find station index
            sta_idx = None
            for i, name in enumerate(sta_names):
                if name.strip() == station_name.strip():
                    sta_idx = i
                    break

            if sta_idx is None:
                ds.close()
                ds = None
                continue

            # Read time (days since 1990-01-01)
            time_var = ds.variables["time"]
            times_days = np.asarray(time_var[:])

            # Convert to datetime
            epoch = pd.Timestamp("1990-01-01")
            timestamps = [
                epoch + pd.Timedelta(days=float(t))
                for t in times_days
            ]

            # Read variable values for this station
            # Shape is (time, station) with ORDER=T
            raw = ds.variables[var_name][:, sta_idx]

            # Ensure 1D — squeeze out any extra dimensions
            vals = np.asarray(raw, dtype="float64").squeeze()
            if vals.ndim == 0:
                vals = vals.reshape(1)
            elif vals.ndim > 1:
                # Take first column along extra dimension
                vals = vals[:, 0]

            ds.close()
            ds = None

            # Replace fill values with NaN
            fill = 9.96921e+36
            vals = np.where(np.abs(vals) > fill * 0.9,
                            np.nan, vals)
            vals = np.where(~np.isfinite(vals), np.nan, vals)

            # Append records as scalar floats
            for t, v in zip(timestamps, vals.ravel()):
                records.append((t, float(v)))

        except Exception as exc:
            print(f"  WARNING: error reading "
                  f"{nc_path.name}: {exc}")
            if ds is not None:
                try:
                    ds.close()
                except Exception:
                    pass
                ds = None
            continue

    if not records:
        return pd.Series(dtype="float64")

    idx  = pd.DatetimeIndex([r[0] for r in records])
    vals = np.array([float(r[1]) for r in records],
                    dtype="float64")

    s = pd.Series(vals, index=idx).sort_index()
    s.index = s.index.tz_localize(None)
    s = s[~s.index.duplicated(keep="first")]
    return s.loc[start_str:end_str].dropna()


# ---------------------------------------------------------------------------
# NDBC observation loader
# ---------------------------------------------------------------------------

def _load_ndbc_wave_var(cfg: dict, station_id: str,
                        ndbc_col: str,
                        start_str: str,
                        end_str: str) -> pd.Series:
    """Load one NDBC wave column for a station from obs/ndbc/ CSVs."""
    mdir     = model_dir(cfg)
    ndbc_dir = mdir / "obs" / "ndbc"

    start_year = int(str(start_str)[:4])
    end_year   = int(str(end_str)[:4])

    parts = []
    for year in range(start_year, end_year + 1):
        f = ndbc_dir / f"{station_id}_{year}.csv"
        if f.exists() and f.stat().st_size > 0:
            try:
                parts.append(pd.read_csv(f))
            except Exception as exc:
                print(f"  WARNING: could not read "
                      f"{f.name}: {exc}")

    if not parts:
        return pd.Series(dtype="float64")

    df = pd.concat(parts, ignore_index=True)

    if "datetime" not in df.columns:
        return pd.Series(dtype="float64")
    if ndbc_col not in df.columns:
        return pd.Series(dtype="float64")

    df["datetime"] = pd.to_datetime(
        df["datetime"], errors="coerce")
    df = (df.dropna(subset=["datetime"])
            .set_index("datetime")
            .sort_index())
    df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated(keep="first")]

    s = pd.to_numeric(df[ndbc_col], errors="coerce")

    for mv in NDBC_WAVE_MISSING:
        s = s.where(s != mv, other=np.nan)
    s = s.where(s < 900, other=np.nan)

    return s.loc[start_str:end_str].dropna()


# ---------------------------------------------------------------------------
# Metrics and plotting
# ---------------------------------------------------------------------------

def _metrics(obs: pd.Series, mod: pd.Series,
             resample_rule: str):
    """Compute bias, RMSE, R² after resampling."""
    o  = obs.resample(resample_rule).mean()
    m  = mod.resample(resample_rule).mean()
    df = pd.concat([o, m], axis=1).dropna()
    df.columns = ["obs", "mod"]

    if len(df) < 2:
        return None

    bias  = float(np.mean(df["mod"] - df["obs"]))
    rmse  = float(np.sqrt(np.mean((df["mod"] - df["obs"]) ** 2)))
    denom = df["obs"].std() * df["mod"].std()
    r2    = (float(np.corrcoef(df["obs"], df["mod"])[0, 1] ** 2)
             if denom > 1e-8 else float("nan"))

    return len(df), float(np.mean(df["obs"])), bias, rmse, r2


def _plot_wave(obs: pd.Series, mod: pd.Series,
               title: str, ylabel: str,
               color: str, mod_label: str,
               out_path: Path):
    """Save a time-series comparison plot."""
    fig, ax = plt.subplots(figsize=(15, 3))
    ax.plot(obs.index, obs.values,
            color="k",    lw=1, label="Observed")
    ax.plot(mod.index, mod.values,
            color=color,  lw=1, label=mod_label)
    ax.set_title(title, fontsize=10)
    ax.set_ylabel(ylabel)
    ax.grid(True, linestyle="--",
            color="gray", linewidth=0.5)
    ax.legend(loc="best")
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter("%d-%b"))
    fig.tight_layout()
    fig.savefig(str(out_path), dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_wave_skill(cfg: dict, config_dir=None):
    """Run wave skill assessment using WW3 station NetCDF output."""
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    station_in = mdir / "fix" / "station.in"
    if not station_in.exists():
        print(f"ERROR: station.in not found: {station_in}")
        return

    out_dir = mdir / f"P{pid}" / f"P{pid}_station_skill"
    out_dir.mkdir(parents=True, exist_ok=True)

    sentinel = out_dir / "wave_skill.done"
    if sentinel.exists():
        print("  wave_skill: already complete "
              "(sentinel found). "
              "Delete wave_skill.done to re-run.")
        return

    # ---- Config ----
    start_str = str(
        cfg.get("station_skill_start") or cfg["start_date"])
    end_str   = str(
        cfg.get("station_skill_end")   or cfg["end_date"])
    resample  = str(
        cfg.get("station_skill_resample", "1h"))
    resample = re.sub(r"^(\d*)H$",
        lambda m: (m.group(1) or "1") + "h", resample)
    resample = re.sub(r"^(\d*)T$",
        lambda m: (m.group(1) or "1") + "min", resample)

    # ---- Check WW3 tab files exist ----
    tab_files = _ww3_tab_files(cfg)
    if not tab_files:
        print("ERROR: no ww3.*_tab.nc files found in run "
              "directories.")
        print("  Run convert_ww3_stations (via setup_run + "
              "submit_run) first.")
        return

    print(f"\n{'='*60}")
    print(f"  Wave skill assessment (UFS-SCHISM+WW3)")
    print(f"  Window   : {start_str} -> {end_str}")
    print(f"  Resample : {resample}")
    print(f"  WW3 tab files: {len(tab_files)} file(s)")
    print(f"    e.g. {tab_files[0].name}")
    print(f"  Variables: HS (WVHT->hs), "
          f"TM01 (APD->tr), DM (MWD->th1m)")
    print(f"  Output   : {out_dir}")
    print(f"{'='*60}\n")

    # ---- Parse station.in ----
    all_stations = parse_station_in(station_in)
    wave_stations = [
        s for s in all_stations
        if s["source"] == "NDBC"
        and any(t.upper() in WAVE_TOKENS
                for t in s["vars"])
        and s["depth"] == 0.0
    ]

    print(f"  {len(wave_stations)} NDBC station(s) with "
          f"wave tokens found in station.in.\n")

    rows = []

    for st in wave_stations:
        sid  = st["station_id"]
        name = re.sub(r"\s+z=[-\d.]+m$", "",
                      st["name"]).strip()

        print(f"  Station {sid} ({name})")

        for tok in st["vars"]:
            T = tok.strip().upper()
            if T not in WAVE_TOKENS:
                continue

            plan_entry = WAVE_VAR_PLAN[T]
            ndbc_col, ww3_var, label, unit, color = plan_entry
            # Use TM01 as canonical name for TM02 alias
            canonical_tok = "TM01" if T == "TM02" else T

            # ---- Load observations ----
            obs = _load_ndbc_wave_var(
                cfg, sid, ndbc_col, start_str, end_str)
            if obs.empty:
                print(f"    {T}: no NDBC obs "
                      f"(column {ndbc_col}), skipping.")
                continue

            # ---- Load WW3 model ----
            mod = _load_ww3_station(
                cfg, sid, ww3_var, start_str, end_str)
            if mod.empty:
                print(f"    {T}: no WW3 data for "
                      f"var={ww3_var} station={sid}, "
                      f"skipping.")
                continue

            # ---- Metrics ----
            res = _metrics(obs, mod, resample)
            if res is None:
                print(f"    {T}: fewer than 2 overlapping "
                      f"points, skipping.")
                continue

            n, mean_obs, bias, rmse, r2 = res

            mod_label = (
                f"WW3  "
                f"[R\u00b2: {r2:.2f}; "
                f"RMSE: {rmse:.3f} {unit}; "
                f"Bias: {bias:.3f} {unit}]")

            out_jpg = (out_dir
                       / f"{sid}_{canonical_tok.lower()}.jpg")
            _plot_wave(
                obs, mod,
                title=(f"NDBC ({sid}): {name} "
                       f"\u2014 {label}"),
                ylabel=f"{label} ({unit})",
                color=color,
                mod_label=mod_label,
                out_path=out_jpg,
            )

            rows.append(dict(
                station_id=sid,
                name=name,
                source="NDBC",
                variable=canonical_tok,
                n_points=n,
                mean_obs=mean_obs,
                bias=bias,
                rmse=rmse,
                r2=r2,
                start=start_str,
                end=end_str,
            ))

            print(f"    {T} (ww3={ww3_var}): n={n}  "
                  f"bias={bias:.3f}  "
                  f"rmse={rmse:.3f}  "
                  f"r2={r2:.3f}  "
                  f"-> {out_jpg.name}")

    # ---- Write skill CSV ----
    if rows:
        df = pd.DataFrame(rows, columns=[
            "station_id", "name", "source",
            "variable", "n_points", "mean_obs",
            "bias", "rmse", "r2",
            "start", "end",
        ])
        csv_path = out_dir / "wave_skill_metrics.csv"
        df.to_csv(csv_path, index=False)
        print(f"\n  Wrote {len(rows)} wave skill row(s) "
              f"-> {csv_path}")
    else:
        print("\n  No station/variable pairs produced "
              "wave metrics.")

    sentinel.touch()
    print(f"\n{'='*60}")
    print(f"  Wave skill assessment (WW3) complete. "
          f"Plots + CSV in {out_dir}")
    print(f"{'='*60}\n")
