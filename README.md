# Comet Men's Sneakers Scraper

A Python program that uses [Playwright](https://playwright.dev/python/) to drive a
headless Chromium browser, parse the product cards rendered on
[wearcomet.com](https://www.wearcomet.com), and export men's sneakers to a CSV file.

## How it works

The collection page uses infinite scroll on the client, but Shopify also renders
the same collection server-side at `?page=N`. The scraper walks those pages, waits
for the product cards to render, then parses each `.card-wrapper` card out of the
live DOM (title, price, compare-at price, badge, sold-out state, image, URL). It
stops when a page returns no new products.

## Setup & usage (uv — recommended)

The script declares its own dependencies inline (PEP 723), so `uv` builds the
environment on its own. No venv or `pip install` is needed.

```bash
# One-time: download the Chromium browser Playwright drives
uv run --with playwright==1.64.1 playwright install chromium

uv run scrape_comet.py                       # -> output/men_sneakers.csv
uv run scrape_comet.py --headed              # watch the browser
uv run scrape_comet.py --out output/mens.csv
uv run scrape_comet.py --collection women-sneakers --out output/women_sneakers.csv
```

## Setup (pip / venv alternative)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Download the Chromium browser Playwright drives (one time):
python -m playwright install chromium
```

## Usage

```bash
# Default: scrape men's sneakers -> output/men_sneakers.csv
python3 scrape_comet.py

# Watch the browser work:
python3 scrape_comet.py --headed

# Custom output path:
python3 scrape_comet.py --out output/mens.csv

# Scrape a different collection (same site/theme), e.g. women's:
python3 scrape_comet.py --collection women-sneakers --out output/women_sneakers.csv
```

## Output

`output/men_sneakers.csv` with columns:

| column             | description                                   |
| ------------------ | --------------------------------------------- |
| `title`            | product name                                  |
| `subtitle`         | secondary caption/colour, if present          |
| `price`            | current price (numeric)                       |
| `compare_at_price` | original price when discounted, else empty    |
| `currency`         | currency symbol parsed from the price (e.g. ₹)|
| `badge`            | card badge text (New In, Restocked, ...)      |
| `sold_out`         | `yes` / `no`                                  |
| `image`            | primary product image URL                     |
| `url`              | product page URL                              |
| `page`             | collection page the product was found on      |

## Notes

- Selectors target Shopify's Dawn-style theme (`.card-wrapper`, `.card__heading`,
  `.price-item`). If Comet changes its theme, update `EXTRACT_JS` in
  `scrape_comet.py`.
- Be a considerate scraper: this fetches a handful of pages sequentially. Avoid
  running it in a tight loop.
