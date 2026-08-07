"""
fetch_cases.py

Sri Lanka has no machine-readable dengue API. The Epidemiology Unit's
Weekly Epidemiological Report is a scanned/rendered PDF, and the more
reliably-updated source is the Ministry of Health's NaDSys dashboard,
published daily as a PDF at dengue.health.gov.lk giving CUMULATIVE
year-to-date cases per district (not weekly new cases).

Strategy: fetch today's PDF, parse the district cumulative table, and
diff it against the last snapshot we saved (data/last_snapshot.json) to
recover that week's new cases. This only works once we have two
snapshots roughly a week apart -- the very first run just bootstraps a
snapshot with no new history row (see main()).

Safety: this script REFUSES to write a week that looks broken (negative
diff, a district missing from the parse, an implausible jump) rather
than silently corrupting history.csv. It exits non-zero in that case so
the GitHub Action fails loudly instead of committing bad data.
"""
import csv
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

from district_config import DISTRICTS, normalize_district

ROOT = Path(__file__).resolve().parent.parent
HISTORY_CSV = ROOT / "data" / "history.csv"
SNAPSHOT_JSON = ROOT / "data" / "last_snapshot.json"

# Implausible-jump guard: a single week's new cases for one district
# shouldn't plausibly exceed this. Generous on purpose -- Colombo/Gampaha
# have hit >1000 in a single week during real outbreaks (see week 26,
# 2026 in the seed data), so this only catches genuine parse failures.
MAX_PLAUSIBLE_WEEKLY_JUMP = 5000

PDF_URL_TEMPLATE = (
    "https://www.dengue.health.gov.lk/wp-content/uploads/{year}/{month:02d}/"
    "Daily-Update-{year}.-{month:02d}.-{day:02d}.pdf"
)

ROW_RE = re.compile(r"^([A-Za-z][A-Za-z /\-]*?)\s+(\d{1,6})\s+[\d.]+%\s*$")


def fetch_pdf_text(target_date: date) -> str:
    url = PDF_URL_TEMPLATE.format(
        year=target_date.year, month=target_date.month, day=target_date.day
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    import pdfplumber
    import io
    text_parts = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def parse_cumulative_table(text: str) -> dict:
    """Parse the 'District/ Unit  No of Cases  %' rows into a dict of
    canonical district -> cumulative case count. Lines that don't match
    a district row (headers, province table, etc.) are ignored."""
    out = {}
    for line in text.splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        raw_name, count = m.group(1), int(m.group(2))
        district = normalize_district(raw_name)
        if district is None:
            continue  # not a district row, or a deliberately-excluded unit
        # Accumulate rather than overwrite: aliased sub-units like CMC
        # (-> Colombo) and Kalmunai (-> Ampara) must ADD to the parent
        # district's own row, which appears separately in the table.
        out[district] = out.get(district, 0) + count
    return out


def iso_week_info(d: date):
    iso_year, iso_week, _ = d.isocalendar()
    week_start = d - timedelta(days=d.isoweekday() - 1)
    return iso_year, iso_week, week_start


def main():
    today = date.today()
    print(f"Fetching NaDSys PDF for {today.isoformat()}...")
    try:
        text = fetch_pdf_text(today)
    except Exception as e:
        print(f"FATAL: could not fetch/parse today's PDF: {e}", file=sys.stderr)
        sys.exit(1)

    current_cumulative = parse_cumulative_table(text)
    missing = set(DISTRICTS) - set(current_cumulative)
    if missing:
        print(f"FATAL: parse only recovered {len(current_cumulative)}/25 "
              f"districts. Missing: {sorted(missing)}. Refusing to write.",
              file=sys.stderr)
        sys.exit(1)

    iso_year, iso_week, week_start = iso_week_info(today)

    if not SNAPSHOT_JSON.exists():
        print("No prior snapshot found -- bootstrapping only. "
              "The real weekly number will appear on the NEXT run.")
        SNAPSHOT_JSON.write_text(json.dumps({
            "date": today.isoformat(), "cumulative": current_cumulative,
        }, indent=2))
        return

    prev = json.loads(SNAPSHOT_JSON.read_text())
    prev_cumulative = prev["cumulative"]

    new_rows = []
    for d in DISTRICTS:
        diff = current_cumulative[d] - prev_cumulative.get(d, current_cumulative[d])
        if diff < 0:
            print(f"FATAL: {d} cumulative count DROPPED ({prev_cumulative.get(d)} "
                  f"-> {current_cumulative[d]}). Source data looks broken or was "
                  f"corrected/reset. Refusing to write -- check manually.",
                  file=sys.stderr)
            sys.exit(1)
        if diff > MAX_PLAUSIBLE_WEEKLY_JUMP:
            print(f"FATAL: {d} jumped by {diff} cases, above the "
                  f"{MAX_PLAUSIBLE_WEEKLY_JUMP} sanity ceiling. Refusing to write.",
                  file=sys.stderr)
            sys.exit(1)
        new_rows.append([iso_year, iso_week, week_start.isoformat(), d, diff])

    with HISTORY_CSV.open("a", newline="") as f:
        csv.writer(f).writerows(new_rows)
    print(f"Appended week {iso_week}, {iso_year} for {len(new_rows)} districts.")

    SNAPSHOT_JSON.write_text(json.dumps({
        "date": today.isoformat(), "cumulative": current_cumulative,
    }, indent=2))


if __name__ == "__main__":
    main()
