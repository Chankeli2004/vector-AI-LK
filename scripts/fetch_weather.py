"""
fetch_weather.py

Pulls daily rainfall + temperature for all 25 district centroids from
Open-Meteo's free archive API (no key required), aggregates to the
target ISO week, and appends to data/weather.csv.

Open-Meteo's archive endpoint typically lags 2-5 days behind real time,
so this should be run a few days after the target week closes (the
GitHub Actions schedule accounts for this -- see workflow file).
"""
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

from district_config import DISTRICTS, COORDS

ROOT = Path(__file__).resolve().parent.parent
WEATHER_CSV = ROOT / "data" / "weather.csv"

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def iso_week_bounds(iso_year: int, iso_week: int):
    start = date.fromisocalendar(iso_year, iso_week, 1)
    end = date.fromisocalendar(iso_year, iso_week, 7)
    return start, end


def fetch_week(iso_year: int, iso_week: int):
    start, end = iso_week_bounds(iso_year, iso_week)
    lats = ",".join(str(COORDS[d][0]) for d in DISTRICTS)
    lons = ",".join(str(COORDS[d][1]) for d in DISTRICTS)
    params = {
        "latitude": lats,
        "longitude": lons,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "precipitation_sum,temperature_2m_mean",
        "timezone": "Asia/Colombo",
        "format": "json",
    }
    resp = requests.get(ARCHIVE_URL, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    # Multi-location requests return a list of per-location objects
    locations = data if isinstance(data, list) else [data]
    if len(locations) != len(DISTRICTS):
        print(f"FATAL: expected {len(DISTRICTS)} locations back, got "
              f"{len(locations)}. Refusing to write.", file=sys.stderr)
        sys.exit(1)

    rows = []
    for district, loc in zip(DISTRICTS, locations):
        precip = loc["daily"]["precipitation_sum"]
        temp = loc["daily"]["temperature_2m_mean"]
        clean_precip = [v for v in precip if v is not None]
        clean_temp = [v for v in temp if v is not None]
        if not clean_precip or not clean_temp:
            print(f"FATAL: {district} has no usable weather data yet for "
                  f"week {iso_week} (archive likely hasn't caught up). "
                  f"Refusing to write partial data.", file=sys.stderr)
            sys.exit(1)
        rainfall_total = round(sum(clean_precip), 1)
        avg_temp = round(sum(clean_temp) / len(clean_temp), 2)
        rows.append([iso_year, iso_week, district, rainfall_total, avg_temp])
    return rows


def main():
    today = date.today()
    iso_year, iso_week, _ = today.isocalendar()
    # Target the week that just closed, since the current week is incomplete.
    target = today - timedelta(days=7)
    ty, tw, _ = target.isocalendar()
    print(f"Fetching weather for ISO week {tw}, {ty}...")
    rows = fetch_week(ty, tw)
    with WEATHER_CSV.open("a", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"Appended weather for {len(rows)} districts.")


if __name__ == "__main__":
    main()
