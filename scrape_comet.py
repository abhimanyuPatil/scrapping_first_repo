#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["playwright==1.64.1"]
# ///
"""Scrape men's sneakers from Comet (https://www.wearcomet.com) with Playwright.

This program launches a headless Chromium browser via Playwright, navigates the
men's sneakers collection page by page, parses each product card out of the
rendered HTML, and writes the result to a CSV file in the output/ directory.

The collection uses infinite scroll on the client, but Shopify also renders the
same collection server-side under `?page=N`. We walk those pages so that we get
every product's HTML reliably instead of depending on lazy-loaded scroll state.

Usage (with uv):
    uv run scrape_comet.py
    uv run scrape_comet.py --headed              # watch the browser
    uv run scrape_comet.py --out output/mens.csv # custom output path
    uv run scrape_comet.py --collection women-sneakers
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List

from playwright.sync_api import Page, TimeoutError as PwTimeoutError, sync_playwright

BASE_URL = "https://www.wearcomet.com"
DEFAULT_COLLECTION = "men-sneakers"
MAX_PAGES = 50  # safety bound to avoid an infinite loop


@dataclass
class Product:
    """One product parsed from a collection card."""

    title: str = ""
    subtitle: str = ""
    price: str = ""
    compare_at_price: str = ""
    currency: str = ""
    badge: str = ""
    sold_out: str = "no"
    image: str = ""
    url: str = ""
    page: int = field(default=0)


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------
# This JavaScript runs in the page context and reads data out of each rendered
# product card (Shopify "Dawn"-style theme: `.card-wrapper` / `.card`).
# Keeping the extraction in one evaluate() call means we parse the HTML the
# browser actually rendered, exactly as a user would see it.
EXTRACT_JS = r"""
() => {
  const abs = (u) => {
    if (!u) return "";
    if (u.startsWith("//")) return "https:" + u;
    return u;
  };

  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

  // A price node like "₹ 4299" -> split symbol and number.
  const parsePrice = (text) => {
    const t = clean(text);
    const m = t.match(/([^\d\s]+)?\s*([\d][\d,]*(?:\.\d+)?)/);
    if (!m) return { currency: "", amount: "" };
    return { currency: (m[1] || "").trim(), amount: (m[2] || "").replace(/,/g, "") };
  };

  const cards = Array.from(document.querySelectorAll(".card-wrapper, .card"));
  const seen = new Set();
  const out = [];

  for (const card of cards) {
    const link = card.querySelector('a[href*="/products/"]');
    if (!link) continue;                       // skip non-product cards
    const path = link.getAttribute("href").split("?")[0];
    if (seen.has(path)) continue;              // de-dupe (image + title link to same product)
    seen.add(path);

    const headingEl = card.querySelector(".card__heading a, .card__heading, .full-unstyled-link");
    const title = clean(headingEl ? headingEl.textContent : link.textContent);

    // Some themes put a colour / subtitle in a secondary element. Exclude the
    // Shopify "unit price" caption, which is not a product subtitle.
    const subEl = card.querySelector(".card__subtitle, .card-information__text");
    let subtitle = clean(subEl ? subEl.textContent : "");
    if (/unit price/i.test(subtitle)) subtitle = "";

    // Sale price takes precedence over regular price when a product is on sale.
    const saleEl = card.querySelector(".price__sale .price-item--sale, .price-item--sale");
    const regEl = card.querySelector(".price-item--regular");
    const priceEl = saleEl || regEl || card.querySelector(".price-item");
    const price = parsePrice(priceEl ? priceEl.textContent : "");

    // Original price shown struck-through when discounted.
    const compareEl = card.querySelector(
      ".price__sale .price-item--regular, s .price-item, .price-item--last, del"
    );
    const compare = compareEl ? parsePrice(compareEl.textContent) : { amount: "" };

    const badgeEl = card.querySelector(".card__badge, .badge");
    const badge = clean(badgeEl ? badgeEl.textContent : "");

    const soldOut = /sold out|out of stock/i.test(card.textContent) ? "yes" : "no";

    const img = card.querySelector("img");
    const image = abs(img ? (img.currentSrc || img.getAttribute("src")) : "");

    // Skip promotional / banner cards that link to a product URL but carry no
    // real product information (no title and no price).
    if (!title && !price.amount) continue;

    out.push({
      title,
      subtitle,
      price: price.amount,
      compare_at_price: compare.amount,
      currency: price.currency,
      badge,
      sold_out: soldOut,
      image,
      url: new URL(path, location.origin).href,
    });
  }

  return out;
}
"""


def parse_collection_page(page: Page, collection: str, page_num: int) -> List[Product]:
    """Load one collection page and return the products parsed from its HTML."""
    url = f"{BASE_URL}/collections/{collection}?page={page_num}"
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    # Wait for product cards to render; if none appear this page is empty.
    try:
        page.wait_for_selector(".card-wrapper, .card", timeout=15_000)
    except PwTimeoutError:
        return []

    raw = page.evaluate(EXTRACT_JS)
    products = []
    for item in raw:
        products.append(Product(page=page_num, **item))
    return products


def scrape(collection: str, headed: bool) -> List[Product]:
    """Walk every collection page and collect all unique products."""
    all_products: List[Product] = []
    seen_urls = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        for page_num in range(1, MAX_PAGES + 1):
            products = parse_collection_page(page, collection, page_num)
            if not products:
                break  # reached past the last page

            new_on_page = 0
            for p in products:
                if p.url in seen_urls:
                    continue
                seen_urls.add(p.url)
                all_products.append(p)
                new_on_page += 1

            print(f"  page {page_num}: {len(products)} cards, {new_on_page} new")

            # If a page returns only products we have already seen, Shopify is
            # repeating the last page -> stop.
            if new_on_page == 0:
                break

        browser.close()

    return all_products


def write_csv(products: List[Product], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "title",
        "subtitle",
        "price",
        "compare_at_price",
        "currency",
        "badge",
        "sold_out",
        "image",
        "url",
        "page",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for p in products:
            writer.writerow(asdict(p))


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape men's sneakers from Comet.")
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=f"Collection handle to scrape (default: {DEFAULT_COLLECTION}).",
    )
    parser.add_argument(
        "--out",
        default="output/men_sneakers.csv",
        help="Output CSV path (default: output/men_sneakers.csv).",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run the browser in headed mode so you can watch it.",
    )
    args = parser.parse_args(argv)

    print(f"Scraping collection '{args.collection}' from {BASE_URL} ...")
    products = scrape(args.collection, headed=args.headed)

    if not products:
        print("No products found. The page structure may have changed.", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    write_csv(products, out_path)
    print(f"Done. Wrote {len(products)} products to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
