from __future__ import annotations

import csv
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs

from bs4 import BeautifulSoup
import requests
import re

DOMAIN_URL    = "https://www.scrapethissite.com"
BASE_URL      = "https://www.scrapethissite.com/pages/frames/"
RETRIES       = 4
DELAY_SECONDS = 1.0
OUTPUT_PATH   = Path("output/turtles.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

_session = requests.Session()
_session.headers.update(HEADERS)
class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line, including any extra= kwargs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts":    self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "msg":   record.getMessage(),
        }
        _STANDARD_ATTRS = logging.LogRecord.__dict__.keys() | {
            "message", "asctime", "taskName"
        }
        for key, val in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = val
        return json.dumps(payload, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger("turtles")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _setup_logging()

@dataclass
class TurtleFamily:
    """One turtle family record"""   
    title: str
    common_name: str
    discovered_year: str
    discovered_by: str

def parse_family(html: str)-> TurtleFamily:
    soup = BeautifulSoup(html, "html.parser")

    # FIX #4: Check if elements exist before calling methods on them.
    # soup.find() returns None when the element is missing. Calling .get_text()
    # on None raises AttributeError and crashes the scraper. Always check first.
    family_name_el = soup.find("span", class_="family-name")
    family_name = family_name_el.get_text(strip=True) if family_name_el else ""

    common_name_el = soup.find("strong", class_="common-name")
    common_name = common_name_el.get_text(strip=True) if common_name_el else ""

    # FIX #12: Only search within the <p> tag, not the entire HTML document.
    # soup.get_text() returns ALL text including navigation, footer, copyright
    # notices. The regex would match the first 4-digit number it finds anywhere,
    # which might be "2024" from the footer instead of "1887" from the description.
    # Narrowing the scope to the <p> tag ensures we only parse the actual content.
    paragraph = soup.find("p")
    description_text = paragraph.get_text() if paragraph else ""

    # FIX #5: Handle the case where regex returns None (no match found).
    # If the text doesn't contain a 4-digit year or "by SomeName", year_match
    # or by_match will be None, and calling .group() raises AttributeError.
    # Use .group() only when a match exists, otherwise default to empty string.
    year_match = re.search(r"\b\d{4}\b", description_text)
    discovered_year = year_match.group(0) if year_match else ""

    by_match = re.search(r"by\s+([A-Za-z]+)", description_text)
    discovered_by = by_match.group(1) if by_match else ""

    return TurtleFamily(
        title=family_name,
        common_name=common_name,
        discovered_year=discovered_year,
        discovered_by=discovered_by
    )

def fetch_and_parse_families(src_list: List[str]) -> List[TurtleFamily]:
    families = []
    for src in src_list:
        for attempt in range(1, RETRIES+1):
            log.info("Fetching family with src", extra={"src": src, "attempt": attempt})
            url = DOMAIN_URL + src
            try:
                response = _session.get(url=url, timeout=30)
                # FIX #7: raise_for_status() was missing. Without it, a 404 or 500
                # response would silently pass through and parse_family would try
                # to parse error HTML as turtle data, resulting in wrong/empty fields.
                response.raise_for_status()
                family = parse_family(response.text)
                families.append(family)
                break
            except requests.RequestException as ex:
                wait = 2 ** attempt
                log.warning(
                    "Fetch failed",
                    extra={"attempt": attempt, "url": url, "wait_s": wait, "error": str(ex)},
                )
                if attempt < RETRIES:
                    time.sleep(wait)
        
        # FIX #8: for...else must be aligned with the for statement itself, not
        # sitting outside at module level. The else block fires when the loop
        # completes WITHOUT hitting a break — meaning all retry attempts failed.
        # Previously this was incorrectly indented and would execute in the wrong
        # condition or not at all.
        else:
            log.error("All retries exhausted for src", extra={"src": src})
        
        time.sleep(DELAY_SECONDS)
    
    return families

def parse_list(html: str)-> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    
    src_list = []

    blocks = soup.find_all("div", class_="turtle-family-card")

    for block in blocks:
        a_tag = block.find("a", href= True)
        src_list.append(a_tag.get("href"))

    return src_list

def fetch_turtle_families_src(src: str) -> List[str]:
    """Fetch families and src"""
    url = DOMAIN_URL + src
    for attempt in range(1, RETRIES + 1):
        try:
            log.info("Fetching turtle families src", extra={"attempt": attempt})
            response = _session.get(url=url, timeout=30)
            response.raise_for_status()
            src_list = parse_list(response.text)
            return src_list
        
        except requests.RequestException as ex:
            wait = 2 ** attempt
            log.error(
                "Initial fetch failed",
                extra={"attempt": attempt, "error": str(ex)}
            )
            if attempt < RETRIES:
                time.sleep(wait)
    else:
        log.error("All retries failed to fetch initial html")

    return []

def get_src_for_turtle_families(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    element = soup.find(id="iframe")
    if not element:
        log.error("iframe element not found")
        return ''
    
    return element.get("src")


def get_initial_html()-> str:
    for attempt in range(1, RETRIES + 1):
        try:
            url = BASE_URL
            log.info("Fetching initial html", extra={"attempt": attempt})
            response = _session.get(url=url, timeout=30)
            response.raise_for_status()
            return response.text
        
        except requests.RequestException as ex:
            wait = 2 ** attempt
            log.error(
                "Initial fetch failed",
                extra={"attempt": attempt, "error": str(ex)}
            )
            if attempt < RETRIES:
                time.sleep(wait)
    else:
        log.error("All retries failed to fetch initial html")

    return ""


def run()-> int:
    """
    Execute following actions and return 1 if all operations successful
        1. Get initial html and fetch the iframe src
        2. Use iframe src to get list of Turtle families
        3. Loop through turtle families to get family information
    """

    initial_html = get_initial_html()
    if not initial_html:
        log.error("failed to fetch initial html")
        return 1

    src = get_src_for_turtle_families(initial_html)
    if not src:
        log.error("failed to fetch src")
        return 1

    turtle_families_src = fetch_turtle_families_src(src)

    if not turtle_families_src:
        log.error("No family src list found")
        return 1
    
    families = fetch_and_parse_families(turtle_families_src)

    log.info("Fetched families", extra={"families": families})

    return 0

if __name__ == "__main__":
    raise SystemExit(run())