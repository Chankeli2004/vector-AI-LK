# DengueWatch LK — live data pipeline

`index.html` now fetches `data.json` at runtime instead of shipping a frozen
snapshot. `data.json` is rebuilt weekly by three scripts, wired together
with a GitHub Actions workflow.

## What's real vs what I couldn't do from here

I don't have a network connection to Sri Lankan government sites or
Open-Meteo from the environment I built this in — only to package
registries (pip/npm), which is what let me install and test the code
itself. So:

- **I tested the actual parser** against a real PDF I fetched separately
  (`scripts/tests/sample_daily_update.txt`, taken from a live NaDSys
  daily update). It correctly recovers all 25 districts, and the sum
  reconciles to within ~1% of the reported national total — the gap is
  the NIHS row, which is deliberately excluded (see `district_config.py`
  docstring for why).
- **I ran the forecasting rebuild end-to-end** against your real seed
  data (weeks 1–26, 2026) and it produces sane, bounded output. I did
  *not* try to reverse-engineer whatever produced the numbers already
  embedded in your app — I don't know that model's exact spec, so I
  built a different, fully documented one instead (see "Forecasting
  method" below). Don't expect the rebuilt numbers to match the old
  static ones exactly.
- **I have not run the scripts against the live internet**, because I
  can't from here. The first real run will happen wherever you deploy
  this (see Setup).

## Setup

1. Push this whole folder to a GitHub repo (public, so GitHub Pages can
   serve it for free — or use any static host you like).
2. If using GitHub Pages: repo Settings → Pages → deploy from the
   default branch, root folder.
3. Go to the Actions tab and manually run "Weekly DengueWatch data
   update" once (`workflow_dispatch`), to bootstrap `data/last_snapshot.json`.
4. **Important:** the first run only bootstraps a snapshot — it can't
   compute a "new cases this week" number from a single cumulative
   reading. Run the workflow a second time (or wait for next Wednesday's
   scheduled run) before the case-count history actually advances.
5. After that it runs automatically every Wednesday and commits
   `data.json` back to the repo.

## Files

- `scripts/district_config.py` — the 25 official districts, their
  coordinates, and the alias/exclusion rules for folding CMC → Colombo,
  Kalmunai → Ampara, and excluding NIHS.
- `scripts/fetch_cases.py` — downloads the day's NaDSys PDF, parses the
  district table, diffs against the last saved cumulative snapshot to
  get that week's new cases. Refuses to write anything that looks
  broken (negative diff, missing district, implausible jump) — it exits
  non-zero instead, so a bad week fails the Action loudly rather than
  quietly corrupting `history.csv`.
- `scripts/fetch_weather.py` — pulls rainfall/temperature from
  Open-Meteo's free archive API for the week that just closed.
- `scripts/build_data.py` — rebuilds `data.json` from
  `data/history.csv` + `data/weather.csv`.
- `.github/workflows/update.yml` — runs the three scripts weekly and
  commits the result.
- `data/history.csv`, `data/weather.csv` — seeded from your workbook
  (2,075 rows each, weeks 1–26 2026 have real case data; weather is
  already populated through week 31 since Open-Meteo is historical
  reanalysis and doesn't depend on case reporting).

## Forecasting method

Per district: a damped trend model (Holt's linear trend, fit in
log-space to avoid one spiky week — e.g. a holiday under-report followed
by a catch-up week — from dominating a 4-week extrapolation) averaged
with a Random Forest regressor trained on lagged case counts + weather,
pooled across all 25 districts so smaller districts benefit from the
larger ones' data. Confidence bands widen with the forecast horizon and
reflect how much the two models disagree. Risk labels (High/Medium/Low)
compare the forecast to each district's *own* recent baseline rather
than a fixed case-count cutoff, since "high" means something different
in Colombo than in Mullaitivu.

This is a reasonable, defensible method — but it's not a peer-reviewed
epidemiological model, and dengue forecasting 4 weeks out is genuinely
uncertain. Treat it as a screening signal, not a clinical or policy
input, without review by someone with public-health domain expertise.

## Judgment calls worth a sanity check before you present this

- **CMC → Colombo, Kalmunai → Ampara**: these aren't official districts,
  so their case counts are added into the parent district. This matches
  the instruction already in your data-collection workbook.
- **NIHS excluded**: it's a hospital referral catchment, not a district,
  and its patients are presumably already counted under their home
  districts elsewhere — folding it in anywhere would double-count. This
  means the rebuilt district-level total sits ~1% under the country
  total on the source PDF. Expected, not a bug — but worth flagging if
  someone asks why the numbers don't add up to the headline figure
  exactly.
- **Source PDF layout could change.** Government PDF exports are not a
  stable API. If the Ministry changes the report's format, the regex in
  `fetch_cases.py` will likely fail to find 25 districts and the script
  will refuse to write (rather than write garbage) — but someone will
  need to notice the Action failed and update the parser.
