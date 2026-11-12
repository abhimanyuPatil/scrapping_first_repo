from dataclasses import dataclass
import json
import logging
import math
import re
import sys
import time
import unicodedata
from collections import Counter
from typing import Optional, List
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.scrapethissite.com/pages/forms/"
RETRIES = 4
PER_PAGE = 100
DELAY_SECONDS = 1.0  # polite pause between page requests
MAX_PAGES = 5  # runaway-loop guard
MIN_EXPECTED = 400
MAX_EXPECTED = 800  # sanity window around the observed 582 rows
WIN_PCT_TOLERANCE = 0.02
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# Logs go to stderr so stdout stays clean JSON.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("scrape_hockey_teams")

_INT_RE = re.compile(r"[+-]?[0-9]+", re.ASCII)
_FLOAT_RE = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII
)

@dataclass
class Team:
    """One hockey team parsed from html"""

    name: str = ""
    year: str = ""
    wins: int = ""
    losses: int = ""
    ot_losses: int = ""
    win_percentage: float = ""
    gf: int = ""
    ga: int = ""
    diff: int = ""

# ---------- cleaning helpers ----------

def clean_text(s: Optional[str]) -> str:
    """Normalize unicode, replace NBSP, collapse whitespace, strip."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def get_text(block, selector: str) -> str:
    """Cleaned text of the first match inside block, or '' if missing."""
    el = block.select_one("." + selector)
    if el is None:
        return ""
    return clean_text(el.get_text())


def _numeric_text(s: Optional[str]) -> str:
    """Clean text, map the Unicode minus (U+2212) to '-', drop thousands commas."""
    return clean_text(s).replace("\u2212", "-").replace(",", "")


def to_int(s: str) -> Optional[int]:
    """Parse an int with optional +/- sign (also U+2212) and thousands
    separators. None for blank, non-numeric or non-integer text (e.g. '3.5')."""
    t = _numeric_text(s)
    if not _INT_RE.fullmatch(t):
        return None
    try:
        return int(t)
    except ValueError:  # e.g. absurdly long digit strings
        return None


def to_float(s: str) -> Optional[float]:
    """Parse a finite float (tolerates sign, U+2212 and thousands separators).
    None for blank or non-numeric text."""
    t = _numeric_text(s)
    if not _FLOAT_RE.fullmatch(t):
        return None
    try:
        value = float(t)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


# ---------- fetching ----------

# Shared session so the TCP/TLS connection is reused across page requests.
session = requests.Session()
session.headers.update(HEADERS)

def parse_rows(html: str) -> tuple([list[dict], list[dict]]):
    """Parses html and returns data"""
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.find_all("tr", class_="team")
    data: list[Team] = []
    failed: list[dict] = []

    for index, team in enumerate(blocks):
        name = get_text(team, "name")
        year = get_text(team, "year")
        wins = to_int(get_text(team, "wins"))
        losses = to_int(get_text(team, "losses"))
        ot_losses = to_int(get_text(team, "ot-losses"))
        win_percentage = to_float(get_text(team, "pct"))
        gf = to_int(get_text(team, "gf"))
        ga = to_int(get_text(team, "ga"))
        diff = to_int(get_text(team, "diff"))

        if not name:
            failed.append(
                {
                    "index": index,
                    "name": name,
                    "reason": "Missing team name"
                }
            )
            continue

        data.append(
            {
                "name": name,
                "year": year,
                "wins": wins,
                "losses": losses,
                "ot_losses": ot_losses,
                "win_percentage": win_percentage,
                "gf": gf,
                "ga": ga,
                "diff": diff
            }
        )

    return data

def fetch_and_parse() -> List[Team]:
    """Fetch html from page with retries. Returns '' on failure"""
    teams = []
    for page_number in range(1,MAX_PAGES+1):
        for attempt in range(1, RETRIES):
            log.info("Fetching for page %d", page_number)
            url = BASE_URL + "?page=" + str(page_number) + "&per_page=" + str(PER_PAGE)
            try:
                response = session.get(url, timeout=30)
                response.raise_for_status()
                table_rows = parse_rows(response.text)
                teams.extend(table_rows)
                break
            except requests.RequestException as exc:
                log.warning("Fetch attempt %d/%d failed: %s", attempt, RETRIES, exc)
        else:
            log.warning("All attempts exhausted for page %d", page_number)
        
        if not table_rows:                                     # ← stop condition
              log.info("No rows on page %d, stopping.", page_number)
              break

        time.sleep(DELAY_SECONDS)

    return teams


def main() -> int:
    fetched_teams = fetch_and_parse()
    log.info("Fetched %s teams", json.dumps(fetched_teams, indent=2, ensure_ascii=False))
    return 1

if __name__ == "__main__":
    raise SystemExit(main())