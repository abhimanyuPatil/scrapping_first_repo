import json
import re
import unicodedata
from collections import Counter
from typing import Optional

from bs4 import BeautifulSoup

from common import create_session, fetch_with_retry, setup_logger

URL = "https://www.scrapethissite.com/pages/simple/"
MIN_EXPECTED = 200
MAX_EXPECTED = 300


log = setup_logger("countries")
session = create_session()

# ---------- cleaning helpers ----------

def clean_text(s: Optional[str]) -> str:
    """Normalise unicode, replace NBSP, collapse whitespace, strip."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def get_text(block, selector: str) -> str:
    """Cleaned text of the first match inside block, or '' if missing."""
    el = block.select_one(selector)
    if el is None:
        return ""
    return clean_text(el.get_text())


def to_int(s: str) -> Optional[int]:
    """Parse an int (tolerates thousands separators). None if invalid."""
    s = clean_text(s).replace(",", "")
    try:
        return int(s)
    except ValueError:
        return None


def to_float(s: str) -> Optional[float]:
    """Parse a float (tolerates thousands separators). None if invalid."""
    s = clean_text(s).replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


# ---------- fetch ----------

def fetch_html(url: str, retries: int = 3) -> str:
    """Fetch a page, retrying transient failures. Returns '' on failure."""
    try:
        html = fetch_with_retry(session=session,url=url,timeout=30,log=log)
        return html
    except RuntimeError as ex:
        log.error("Could not fetch page", extra={"error": str(ex)})
        return ""


# ---------- parse ----------

def parse(html: str) -> tuple[list[dict], list[dict]]:
    """Return (countries, failed_rows)."""
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.find_all("div", class_="country")
    data: list[dict] = []
    failed: list[dict] = []

    for index, c in enumerate(blocks):
        name = get_text(c, ".country-name")
        capital = get_text(c, ".country-capital")
        population = to_int(get_text(c, ".country-population"))
        area = to_float(get_text(c, ".country-area"))

        if not name or population is None or area is None:
            failed.append(
                {
                    "index": index,
                    "name": name,
                    "reason": "missing name or invalid population/area",
                }
            )
            continue

        data.append(
            {"name": name, "capital": capital, "population": population, "area": area}
        )
    return data, failed


# ---------- validate ----------

def validate(data: list[dict]) -> list[str]:
    """Return a list of fatal problems (empty list means OK). Warns on soft issues."""
    problems: list[str] = []

    if not data:
        problems.append("no countries parsed (page layout may have changed)")
        return problems

    if not (MIN_EXPECTED <= len(data) <= MAX_EXPECTED):
        problems.append(
            f"country count {len(data)} outside expected range "
            f"{MIN_EXPECTED}-{MAX_EXPECTED}"
        )

    dupes = [n for n, k in Counter(d["name"] for d in data).items() if k > 1]
    if dupes:
        problems.append(f"duplicate names: {dupes}")

    for d in data:
        if d["population"] < 0:
            problems.append(f"negative population for {d['name']}")
        if d["area"] < 0:
            problems.append(f"negative area for {d['name']}")
        elif d["area"] == 0:
            # Source data genuinely reports 0 for some territories.
            log.warning("Zero area for %s", d["name"])
        if not d["capital"]:
            log.warning("No capital for %s", d["name"])

    return problems


# ---------- main ----------

def main() -> int:
    try:
        html = fetch_with_retry(session=session,url=URL,timeout=30,log=log)
    except RuntimeError as ex:
        log.error("Could not fetch page", extra={"error": str(ex)})

    if not html:
        log.error("Empty response; aborting")
        return 1

    data, failed = parse(html)
    if failed:
        log.warning("%d row(s) failed to parse: %s", len(failed), failed)

    problems = validate(data)
    if problems:
        for p in problems:
            log.error(p)
        return 1

    log.info("Parsed %d countries (%d failed)", len(data), len(failed))
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
