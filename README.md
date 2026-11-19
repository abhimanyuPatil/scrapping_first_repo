# Web Scraping Practice

A collection of Python web scrapers demonstrating different scraping techniques and patterns. All scrapers target practice sites from [scrapethissite.com](https://www.scrapethissite.com) and one production site ([wearcomet.com](https://www.wearcomet.com)).

---

## 📚 What's inside

| Scraper | Technique | Description |
|---------|-----------|-------------|
| `scrape_hockey_teams.py` | Static HTML + pagination | NHL team stats with dynamic page count discovery |
| `scrape_hockey_teams_senior.py` | Production patterns | Senior-level implementation with validation, deduplication, QA checks |
| `scrape_movies.py` | AJAX / JSON API | Oscar-winning films from a JavaScript-rendered page |
| `scrape_turtles.py` | Nested iframes | Turtle families from content loaded in frames |
| `scrape_comet.py` | Browser automation | Men's sneakers from a Shopify store using Playwright |

All scrapers (except `scrape_comet.py`) use the `common/` module for shared utilities (HTTP retry, logging, atomic saves).

---

## 🚀 Quick start

This project uses [uv](https://docs.astral.sh/uv/) for dependency management. All commands use `uv run` — no virtual environment setup needed.

### Run a scraper

```bash
# Hockey teams (static HTML, pagination)
uv run scrape_hockey_teams.py

# Movies (AJAX/JSON endpoint)
uv run scrape_movies.py

# Turtles (nested iframes)
uv run scrape_turtles.py

# Senior-level hockey scraper (all production patterns)
uv run scrape_hockey_teams_senior.py

# Comet sneakers (browser automation with Playwright)
# One-time setup: install Chromium for Playwright
uv run --with playwright==1.64.1 playwright install chromium
# Then run the scraper
uv run scrape_comet.py
uv run scrape_comet.py --headed              # watch the browser work
uv run scrape_comet.py --collection women-sneakers --out output/women_sneakers.csv
```

### Output

All scrapers save results to the `output/` directory as CSV files.

---

## 📖 Learning progression

The scrapers are ordered by complexity:

1. **`scrape_hockey_teams.py`** — Start here. Static HTML with `requests` + `BeautifulSoup`. Covers pagination, retry logic, and basic parsing.

2. **`scrape_movies.py`** — AJAX scraping. Covers finding hidden API endpoints, handling JSON responses, and dealing with missing fields.

3. **`scrape_turtles.py`** — Iframe scraping. Covers nested frames and detail page navigation.

4. **`scrape_hockey_teams_senior.py`** — Production patterns. Covers schema validation, deduplication, cross-field checks, quality assertions, and atomic writes.

5. **`scrape_comet.py`** — Browser automation. Uses Playwright when JavaScript is required. Covers waiting for dynamic content, infinite scroll handling, and production Shopify scraping.

---

## 🛠️ Common utilities (`common/`)

Shared modules extracted from the scrapers to avoid duplication:

- **`config.py`** — Constants (headers, retries, delays)
- **`logging_utils.py`** — Structured JSON logging
- **`http.py`** — HTTP with exponential backoff retry
- **`storage.py`** — Atomic CSV/JSON writes

See [`common/README.md`](common/README.md) for full documentation and examples.

---

## 📋 Requirements

- Python 3.9+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Dependencies are auto-managed by `uv run` or listed in `requirements.txt`

### Install uv (if not already installed)

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with pip
pip install uv
```

---

## 🎯 Senior-level patterns demonstrated

`scrape_hockey_teams_senior.py` shows production-ready patterns:

- **Dynamic pagination** — reads page count from HTML instead of hardcoding
- **Schema validation** — enforces field types and cross-field checks
- **Deduplication** — composite key `(name, year)` prevents duplicate rows
- **Quality checks** — dataset-level assertions before writing
- **Atomic writes** — write-then-rename prevents corruption on crash
- **Structured logging** — JSON logs queryable by tools like `jq`, CloudWatch
- **Exponential backoff** — polite retry with increasing delays

---

## 🗂️ Output files

All scrapers write to `output/`:

| File | Columns | Rows (approx) |
|------|---------|---------------|
| `hockey_teams.csv` | name, year, wins, losses, ot_losses, win_pct, gf, ga, diff | ~582 |
| `movies.csv` | title, year, awards, nominations, best_picture | ~87 |
| `turtles.csv` | title, common_name, discovered_year, discovered_by | 14 |
| `men_sneakers.csv` | title, subtitle, price, compare_at_price, currency, badge, sold_out, image, url, page | varies |

---

## 🧪 Running tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=common --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_parse_schema.py
```

---

## 📝 Notes

- All scrapers respect `robots.txt`
- Be considerate: scrapers include delays between requests (`DELAY_SECONDS = 1.0`)
- `scrape_comet.py` targets a real production site — use sparingly
- Practice sites are intentionally designed for scraping practice
- JSON logs go to `stderr`, structured output to `stdout` or files

---

## 🤝 Contributing

This is a learning project. Feel free to:
- Add new scrapers for different techniques
- Improve error handling or parsing logic
- Extract more patterns into `common/`
- Add tests for parsing functions

---

## 📚 Resources

- [Scrape This Site](https://www.scrapethissite.com) — practice scraping sandbox
- [Beautiful Soup docs](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)
- [Requests docs](https://requests.readthedocs.io/)
- [Playwright Python docs](https://playwright.dev/python/)
- [uv docs](https://docs.astral.sh/uv/)

## CSV → Parquet → Cloud Storage → BigQuery (local)

Runs against [localgcp](https://localgcp.com/), no real GCP account needed.

```bash
localgcp up --services=bigquery        # BigQuery emulator needs Docker
eval $(localgcp env)                   # sets STORAGE_EMULATOR_HOST

python scrape_hockey_teams_senior.py   # 1. writes output/hockey_teams.csv
python build_parquet.py                # 2. CSV -> output/hockey_teams.parquet (x200 rows)
python upload_to_gcs.py                # 3. file -> gs://comet-learning/hockey/hockey_teams.parquet
python load_to_bigquery.py             # 4. table local-project.hockey.hockey_teams
```

**One command:** `python run_pipeline.py` runs all four steps and stops at the first
failure (the exit code is the failed step's, so schedulers can detect it).
Options: `--skip-scrape`, `--copies N`, `--mode insert|uri`, `--step-timeout SECONDS`
(default 900), `--preflight-timeout SECONDS` (default 60), `--skip-preflight`.
Before any step it sends a real request to Cloud Storage and BigQuery and exits with code 2 if either is not healthy.

Query it: `SELECT year, AVG(win_pct) FROM hockey.hockey_teams GROUP BY year`
(use unquoted `dataset.table` names; the emulator cannot resolve backticked full names).

Settings come from environment variables (defaults in `common/gcp_local.py`):
`GCP_PROJECT_ID`, `GCS_BUCKET`, `GCS_OBJECT`, `BQ_DATASET`, `BQ_TABLE`, `BIGQUERY_EMULATOR_HOST`.

LocalBQ does not implement BigQuery load jobs or streaming inserts, so
`load_to_bigquery.py` defaults to `--mode insert` (reads the Parquet file from
Cloud Storage and inserts with SQL). On real GCP use `--mode uri`, which lets
BigQuery read `gs://...` directly.
