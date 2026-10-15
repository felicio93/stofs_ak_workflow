"""
models/schism_wwm/postprocess/wave_skill.py
===========================================
Phase 5 step "wave_skill" (interactive; runs in swf_plot env).

Compares NDBC wave observations against WWM station output
(wwm_sta_*.nc files) for stations that have HS, TM01, or DM
tokens in their station.in VARS bracket.

NDBC column mapping
-------------------
  HS   <- WVHT  (significant wave height, m)
  TM01 <- APD   (average wave period, s)
         Note: NDBC APD = average period ≈ TM01 (spectral moment 0,1)
         not TM02 (spectral moment 0,2). WWM outputs TM01 directly.
  DM   <- MWD   (mean wave direction, degrees)

WWM model output
----------------
  wwm_sta_000N.nc files in each R{ID}_{group_id}/ run directory.
  Each file contains one day of half-hourly records (DELTC=1800s).
  Station index in the NetCDF matches the position in the NOUTS
  list in wwminput.nml (confirmed by lon/lat comparison).

Station index mapping
---------------------
  wwminput.nml NOUTS list is parsed to build a name->index map.
  The wwminput.nml is read from the first available run directory
  (all groups share the same station list).

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
#   (NDBC column, WWM netcdf varname, label, unit, color)
#
# Note: NDBC APD = Average Period ≈ TM01 (spectral moment 0,1).
# WWM writes TM01 directly in wwm_sta_*.nc.
WAVE_VAR_PLAN = {
    "HS":   ("WVHT", "HS",   "Significant Wave Height", "m",   "steelblue"),
    "TM01": ("APD",  "TM01", "Average Wave Period (TM01)", "s", "darkorange"),
    "DM":   ("MWD",  "DM",   "Mean Wave Direction",    "deg",  "seagreen"),
}

# Keep TM02 as alias for TM01 for backward compatibility with
# station.in files that have [TM02] tokens.
WAVE_VAR_PLAN["TM02"] = WAVE_VAR_PLAN["TM01"]

WAVE_TOKENS = set(WAVE_VAR_PLAN.keys())

# NDBC missing-value sentinel per wave column (99 is a VALID direction in
# degrees, so sentinels must be applied per column, not globally).
NDBC_WAVE_MISSING = {
    "WVHT": 99.0,
    "APD":  99.0,
    "DPD":  99.0,
    "MWD":  999.0,
}


# ---------------------------------------------------------------------------
# wwminput.nml NOUTS parser
# ---------------------------------------------------------------------------

def _parse_nouts(wwminput_path: Path) -> list:
    """Parse the NOUTS list from wwminput.nml."""
    text = wwminput_path.read_text(errors="ignore")
    m = re.search(
        r"NOUTS\s*=\s*(.*?)(?=\n\s*[A-Z]|\n\s*\/)",
        text, re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    block = m.group(1)
    names = re.findall(r"'([^']+)'", block)
    return [n.strip() for n in names]


def _build_station_index_map(cfg: dict) -> dict:
    """Return {station_name: 0-based column index} from the first
    available wwminput.nml in the run directories."""
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    for group_id in list_groups(cfg):
        wwminput = (mdir / f"R{pid}" / f"R{pid}_{group_id}"
                    / "wwminput.nml")
        if wwminput.exists():
            nouts = _parse_nouts(wwminput)
            if nouts:
                return {name: i for i, name in enumerate(nouts)}
    return {}


# ---------------------------------------------------------------------------
# WWM model data loader
# ---------------------------------------------------------------------------

def _mjd_to_datetime64(mjd_array: np.ndarray) -> np.ndarray:
    """Convert Modified Julian Date to numpy datetime64[ns]."""
    epoch = np.datetime64("1858-11-17T00:00:00", "s")
    return epoch + (mjd_array * 1e9).astype("timedelta64[ns]")


def _load_wwm_station(cfg: dict, station_name: str,
                      col_idx: int, var_name: str,
                      start_str: str, end_str: str) -> pd.Series:
    """Load a single variable for one station from all wwm_sta_*.nc files."""
    import netCDF4 as nc4

    pid  = cfg["project_id"]
    mdir = model_dir(cfg)

    records = []

    for group_id in list_groups(cfg):
        rdir = mdir / f"R{pid}" / f"R{pid}_{group_id}"
        if not rdir.is_dir():
            continue

        wwm_files = sorted(
            rdir.glob("wwm_sta_*.nc"),
            key=lambda p: int(
                re.search(r"(\d+)\.nc$", p.name).group(1))
        )

        for nc_path in wwm_files:
            ds = None
            try:
                ds = nc4.Dataset(str(nc_path))

                if var_name not in ds.variables:
                    ds.close()
                    ds = None
                    continue

                times_mjd = np.asarray(
                    ds.variables["ocean_time"][:])
                vals = np.asarray(
                    ds.variables[var_name][:, col_idx],
                    dtype="float64")

                ds.close()
                ds = None

                dt_arr = _mjd_to_datetime64(times_mjd)

                vals = np.where(vals < -900, np.nan, vals)
                vals = np.where(~np.isfinite(vals), np.nan, vals)

                for dt, v in zip(dt_arr, vals):
                    records.append((dt, v))

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

    idx  = pd.DatetimeIndex(
        [pd.Timestamp(r[0]) for r in records])
    vals = np.array([r[1] for r in records], dtype="float64")

    s = pd.Series(vals, index=idx).sort_index()
    s.index = s.index.tz_localize(None)
    s = s[~s.index.duplicated(keep="first")]

    return s.loc[start_str:end_str]


# ---------------------------------------------------------------------------
# NDBC wave observation loader
# ---------------------------------------------------------------------------

def _load_ndbc_wave_var(cfg: dict, station_id: str,
                        ndbc_col: str,
                        start_str: str,
                        end_str: str) -> pd.Series:
    """Load one NDBC wave column for a station."""
    pid  = cfg["project_id"]
    mdir = model_dir(cfg)
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

    mv = NDBC_WAVE_MISSING.get(ndbc_col)
    if mv is not None:
        s = s.where(s != mv, other=np.nan)
    s = s.where(s < 900, other=np.nan)

    return s.loc[start_str:end_str].dropna()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _circular_mean_deg(x: pd.Series) -> float:
    """Circular (vector) mean of angles in degrees, in [0, 360)."""
    rad = np.deg2rad(x.dropna().values)
    if rad.size == 0:
        return float("nan")
    return float(np.rad2deg(np.arctan2(np.sin(rad).mean(),
                                       np.cos(rad).mean())) % 360.0)


def _wrap180(d):
    """Wrap angle differences (deg) to [-180, 180)."""
    return (d + 180.0) % 360.0 - 180.0


def _metrics(obs: pd.Series, mod: pd.Series,
             resample_rule: str, circular: bool = False):
    """Compute bias, RMSE, R² after resampling.

    circular=True is for directions in degrees: resampling uses the
    circular mean, differences are wrapped to [-180, 180) and R² is the
    squared circular-circular correlation coefficient.
    """
    if circular:
        o = obs.resample(resample_rule).apply(_circular_mean_deg)
        m = mod.resample(resample_rule).apply(_circular_mean_deg)
    else:
        o = obs.resample(resample_rule).mean()
        m = mod.resample(resample_rule).mean()
    df = pd.concat([o, m], axis=1).dropna()
    df.columns = ["obs", "mod"]

    if len(df) < 2:
        return None

    if circular:
        diff = _wrap180(df["mod"] - df["obs"])
        bias = float(np.mean(diff))
        rmse = float(np.sqrt(np.mean(diff ** 2)))
        ao = np.deg2rad(df["obs"].values)
        am = np.deg2rad(df["mod"].values)
        so = np.sin(ao - np.arctan2(np.sin(ao).mean(), np.cos(ao).mean()))
        sm = np.sin(am - np.arctan2(np.sin(am).mean(), np.cos(am).mean()))
        denom = np.sqrt((so ** 2).sum() * (sm ** 2).sum())
        r2 = (float(((so * sm).sum() / denom) ** 2)
              if denom > 1e-8 else float("nan"))
        mean_obs = _circular_mean_deg(df["obs"])
        return len(df), mean_obs, bias, rmse, r2

    bias  = float(np.mean(df["mod"] - df["obs"]))
    rmse  = float(np.sqrt(np.mean((df["mod"] - df["obs"]) ** 2)))
    denom = df["obs"].std() * df["mod"].std()
    r2    = (float(np.corrcoef(df["obs"], df["mod"])[0, 1] ** 2)
             if denom > 1e-8 else float("nan"))

    return len(df), float(np.mean(df["obs"])), bias, rmse, r2


# ---------------------------------------------------------------------------
# Plot
# ---------------------------------------------------------------------------

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
    """Run wave skill assessment for all NDBC stations with wave
    tokens in station.in."""
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

    # ---- Build station index map from wwminput.nml ----
    station_index_map = _build_station_index_map(cfg)
    if not station_index_map:
        print("ERROR: could not parse NOUTS from any "
              "wwminput.nml in the run directories.")
        return

    print(f"\n{'='*60}")
    print(f"  Wave skill assessment (SCHISM+WWM)")
    print(f"  Window   : {start_str} -> {end_str}")
    print(f"  Resample : {resample}")
    print(f"  Stations : {len(station_index_map)} WWM "
          f"stations indexed from wwminput.nml")
    print(f"  Variables: HS (WVHT), TM01 (APD), DM (MWD)")
    print(f"  Output   : {out_dir}")
    print(f"{'='*60}\n")

    # ---- Parse station.in — NDBC stations with wave tokens ----
    all_stations = parse_station_in(station_in)
    wave_stations = [
        s for s in all_stations
        if s["source"] == "NDBC"
        and any(t.upper() in WAVE_TOKENS for t in s["vars"])
        and s["depth"] == 0.0
    ]

    print(f"  {len(wave_stations)} NDBC station(s) with "
          f"wave tokens found in station.in.\n")

    rows = []

    for st in wave_stations:
        sid  = st["station_id"]
        name = re.sub(r"\s+z=[-\d.]+m$", "",
                      st["name"]).strip()

        col_idx = station_index_map.get(sid)
        if col_idx is None:
            print(f"  {sid}: not found in NOUTS list, skipping.")
            continue

        print(f"  Station {sid} ({name})  "
              f"[WWM column {col_idx}]")

        for tok in st["vars"]:
            T = tok.strip().upper()
            if T not in WAVE_TOKENS:
                continue

            # Resolve to canonical token (TM02 -> TM01)
            plan_entry = WAVE_VAR_PLAN[T]
            ndbc_col, wwm_var, label, unit, color = plan_entry
            canonical_tok = "TM01" if T == "TM02" else T

            # ---- Load observations ----
            obs = _load_ndbc_wave_var(
                cfg, sid, ndbc_col, start_str, end_str)
            if obs.empty:
                print(f"    {T}: no NDBC obs "
                      f"(column {ndbc_col}), skipping.")
                continue

            # ---- Load model ----
            mod = _load_wwm_station(
                cfg, sid, col_idx, wwm_var,
                start_str, end_str)
            if mod.empty:
                print(f"    {T}: no model data for "
                      f"{wwm_var}, skipping.")
                continue

            # ---- Compute metrics ----
            res = _metrics(obs, mod, resample,
                           circular=(unit == "deg"))
            if res is None:
                print(f"    {T}: fewer than 2 overlapping "
                      f"points, skipping.")
                continue

            n, mean_obs, bias, rmse, r2 = res

            mod_label = (
                f"Model  "
                f"[R\u00b2: {r2:.2f}; "
                f"RMSE: {rmse:.3f} {unit}; "
                f"Bias: {bias:.3f} {unit}]")

            out_jpg = out_dir / f"{sid}_{canonical_tok.lower()}.jpg"
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

            print(f"    {T} ({wwm_var}): n={n}  "
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
    print(f"  Wave skill assessment complete. "
          f"Plots + CSV in {out_dir}")
    print(f"{'='*60}\n")
