"""
build_data.py

Rebuilds data.json (what the app fetches at runtime) from the
accumulated data/history.csv + data/weather.csv.

Per district, forecasting combines two signals into a small ensemble:
  1. Holt's linear-trend exponential smoothing on the case series --
     captures momentum/trend.
  2. A Random Forest regressor trained on (lagged cases, rainfall,
     temperature) across ALL districts pooled together -- captures the
     weather relationship, and pools data across districts so smaller
     districts with short/sparse series still get a sensible fit.
The final forecast is the mean of the two. Confidence bands come from
the spread across the two models plus each model's own residual
variance -- deliberately conservative (wide) rather than falsely
precise, since 4-week-ahead dengue forecasting is genuinely uncertain.

Risk is now TWO separate signals, deliberately kept apart:
  - severity: an ABSOLUTE badge (High/Medium/Low), based on where this
    district's current case count sits relative to the 33rd/67th
    percentile of ALL district-week case counts in the whole dataset.
    "High" means genuinely large, full stop -- not large-for-this-district.
  - trend: a RELATIVE arrow (Rising/Stable/Falling), based on comparing
    the forecast to this district's OWN recent baseline. A small
    district can show "Rising" on a small absolute number, which is a
    real and useful signal (an emerging local outbreak) -- it just isn't
    the same thing as being severe in absolute terms, and showing both
    avoids the confusing case where a district with fewer total cases
    than another gets a scarier-looking single badge.
"""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from district_config import DISTRICTS

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
HISTORY_CSV = ROOT / "data" / "history.csv"
WEATHER_CSV = ROOT / "data" / "weather.csv"
OUT_JSON = ROOT / "data.json"

HISTORY_WINDOW = 8   # weeks of history shown in the app's sparkline
FORECAST_HORIZON = 4  # weeks ahead


def load_merged():
    cases = pd.read_csv(HISTORY_CSV).dropna(subset=["cases"])
    weather = pd.read_csv(WEATHER_CSV)
    cases["cases"] = cases["cases"].astype(int)
    df = cases.merge(weather, on=["iso_year", "iso_week", "district"], how="left")
    df = df.sort_values(["district", "iso_year", "iso_week"]).reset_index(drop=True)
    return df


def train_rf(df: pd.DataFrame) -> RandomForestRegressor:
    """Pooled RF: predict this week's cases from last week's cases,
    2-week lag, rainfall, and temperature -- across all districts."""
    rows = []
    for d, g in df.groupby("district"):
        g = g.reset_index(drop=True)
        for i in range(2, len(g)):
            if pd.isna(g.loc[i, "rainfall_mm"]) or pd.isna(g.loc[i, "avg_temp_c"]):
                continue
            rows.append({
                "lag1": g.loc[i - 1, "cases"],
                "lag2": g.loc[i - 2, "cases"],
                "rainfall": g.loc[i, "rainfall_mm"],
                "temp": g.loc[i, "avg_temp_c"],
                "target": g.loc[i, "cases"],
            })
    train = pd.DataFrame(rows)
    X = train[["lag1", "lag2", "rainfall", "temp"]]
    y = train["target"]
    model = RandomForestRegressor(n_estimators=300, max_depth=8, random_state=42)
    model.fit(X, y)
    resid = y - model.predict(X)
    return model, float(resid.std())


def forecast_district(series: np.ndarray, rf_model, rf_resid_std,
                       last_rainfall: float, last_temp: float):
    series = np.clip(series.astype(float), 0.1, None)  # HW needs >0

    # --- Model 1: Holt's linear trend, fit in LOG space ---
    # Weekly case counts are heavy-tailed and prone to single-week
    # reporting spikes/backlogs (e.g. a holiday week under-reports, then
    # the following week catches up). Fitting the trend on raw counts
    # lets one such spike dominate the whole 4-week extrapolation, which
    # is exactly the failure mode a health dashboard can't afford. Log
    # space is the standard fix for this in case-count forecasting.
    log_series = np.log1p(series)
    try:
        hw = ExponentialSmoothing(log_series, trend="add", damped_trend=True).fit()
        hw_fc = np.clip(np.expm1(hw.forecast(FORECAST_HORIZON)), 0, None)
        hw_resid_std = float(np.std(np.expm1(hw.fittedvalues) - series))
    except Exception:
        # Degenerate/very short series: flat naive forecast
        hw_fc = np.full(FORECAST_HORIZON, series[-1])
        hw_resid_std = float(np.std(series)) if len(series) > 1 else series[-1] * 0.5

    # --- Model 2: pooled Random Forest, rolled forward autoregressively ---
    lag1, lag2 = series[-1], series[-2] if len(series) > 1 else series[-1]
    rf_fc = []
    for _ in range(FORECAST_HORIZON):
        x = pd.DataFrame([{"lag1": lag1, "lag2": lag2,
                            "rainfall": last_rainfall, "temp": last_temp}])
        pred = max(0.0, float(rf_model.predict(x)[0]))
        rf_fc.append(pred)
        lag2, lag1 = lag1, pred
    rf_fc = np.array(rf_fc)

    forecast = (hw_fc + rf_fc) / 2
    # Widen with horizon since 4-weeks-out is less certain than 1-week-out
    horizon_mult = np.array([1.0, 1.3, 1.6, 1.9])[:FORECAST_HORIZON]
    spread = (np.abs(hw_fc - rf_fc) / 2 + (hw_resid_std + rf_resid_std) / 2) * horizon_mult
    conf_low = np.clip(forecast - spread, 0, None)
    conf_high = forecast + spread

    return forecast, conf_low, conf_high, hw_resid_std, rf_resid_std


def classify_trend(forecast_mean: float, baseline_mean: float):
    """Direction relative to the district's OWN recent baseline. This
    answers 'is it getting worse here', independent of how big the
    absolute numbers are."""
    if baseline_mean <= 0:
        return "Rising" if forecast_mean >= 1 else "Stable"
    ratio = forecast_mean / baseline_mean
    if ratio > 1.3:
        return "Rising"
    if ratio < 0.7:
        return "Falling"
    return "Stable"


def compute_severity_thresholds(df: pd.DataFrame):
    """Data-driven absolute severity bands: the 33rd/67th percentile of
    ALL district-week case counts in the whole dataset (not just this
    district, not just this week). This answers 'how big is this
    compared to what's typical for any district in any week' -- an
    absolute yardstick, deliberately separate from classify_trend()
    above, which only looks at each district relative to itself."""
    all_cases = df["cases"].to_numpy()
    low_cut = float(np.percentile(all_cases, 33))
    high_cut = float(np.percentile(all_cases, 67))
    return low_cut, high_cut


def classify_severity(last_cases: int, low_cut: float, high_cut: float):
    if last_cases >= high_cut:
        return "High"
    if last_cases <= low_cut:
        return "Low"
    return "Medium"


def main():
    df = load_merged()
    missing_districts = set(DISTRICTS) - set(df["district"].unique())
    if missing_districts:
        print(f"FATAL: no history at all for {missing_districts}. Refusing.",
              file=sys.stderr)
        sys.exit(1)

    rf_model, rf_resid_std = train_rf(df)
    severity_low_cut, severity_high_cut = compute_severity_thresholds(df)

    out = {}
    for d in DISTRICTS:
        g = df[df["district"] == d].sort_values(["iso_year", "iso_week"])
        if len(g) < HISTORY_WINDOW:
            print(f"FATAL: {d} has only {len(g)} weeks of history, need "
                  f"{HISTORY_WINDOW}. Refusing to write partial output.",
                  file=sys.stderr)
            sys.exit(1)

        full_series = g["cases"].to_numpy()
        window = full_series[-HISTORY_WINDOW:]
        last_row = g.iloc[-1]
        last_rainfall = float(last_row["rainfall_mm"]) if not pd.isna(last_row["rainfall_mm"]) else float(g["rainfall_mm"].mean())
        last_temp = float(last_row["avg_temp_c"]) if not pd.isna(last_row["avg_temp_c"]) else float(g["avg_temp_c"].mean())

        forecast, conf_low, conf_high, hw_std, rf_std = forecast_district(
            full_series, rf_model, rf_resid_std, last_rainfall, last_temp)

        baseline_mean = float(window.mean())
        forecast_mean = float(forecast.mean())
        trend = classify_trend(forecast_mean, baseline_mean)
        severity = classify_severity(int(full_series[-1]), severity_low_cut, severity_high_cut)

        # Confidence score: how tight the band is relative to the forecast
        # (narrower band + models agreeing => higher confidence), clipped to [0,1]
        rel_spread = float(np.mean((conf_high - conf_low)) / max(forecast_mean, 1))
        confidence = round(float(np.clip(1 - rel_spread / 3, 0.3, 0.95)), 2)

        out[d] = {
            "history": [int(v) for v in window],
            "forecast": [round(float(v)) for v in forecast],
            "conf_low": [round(float(v)) for v in conf_low],
            "conf_high": [round(float(v)) for v in conf_high],
            "severity": severity,
            "trend": trend,
            "confidence": confidence,
            "last_cases": int(full_series[-1]),
            "last_rainfall": round(last_rainfall, 1),
            "last_temp": round(last_temp, 1),
        }

    meta = {
        "iso_year": int(df["iso_year"].max()),
        "iso_week": int(df[df["iso_year"] == df["iso_year"].max()]["iso_week"].max()),
        "generated_at": pd.Timestamp.utcnow().isoformat(),
    }

    OUT_JSON.write_text(json.dumps({"meta": meta, "districts": out}, indent=1))
    print(f"Wrote {OUT_JSON} for week {meta['iso_week']}, {meta['iso_year']}.")


if __name__ == "__main__":
    main()
