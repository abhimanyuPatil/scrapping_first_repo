from __future__ import annotations

import csv
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from bs4 import BeautifulSoup
import requests

# FIX: all constants defined locally instead of imported from other scraper files.
# Importing HEADERS from scrape_hockey_teams and RETRIES from scrape_hockey_teams_senior
# creates tight coupling — a change in those files silently breaks this scraper.
# Each scraper should own its own constants, or they should live in a shared config.py.
BASE_URL      = "https://www.scrapethissite.com/pages/ajax-javascript/"
RETRIES       = 4
DELAY_SECONDS = 1.0
OUTPUT_PATH   = Path("output/movies.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# NOTE: _JsonFormatter is duplicated from scrape_hockey_teams_senior.py.
# In a real project this would live in a shared logging_utils.py so both
# files import from one place. Left here to keep this file self-contained.
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
    logger = logging.getLogger("movies")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _setup_logging()


@dataclass
class Movie:
    """One movie record from the AJAX response."""
    title:        str
    year:         int
    awards:       int
    nominations:  int
    # FIX: default is False because the API omits best_picture entirely when
    # the film did not win. Only the winner has the key in the JSON response.
    # Without a default, Movie(**d) raises TypeError for every non-winner.
    best_picture: bool = False


_session = requests.Session()
_session.headers.update(HEADERS)


def fetch_initial_html() -> str:
    """Fetch the page HTML to discover available year buttons."""
    url = BASE_URL
    last_exc: Optional[Exception] = None

    for attempt in range(1, RETRIES + 1):
        try:
            log.info("Fetching initial page", extra={"attempt": attempt})
            response = _session.get(url, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.RequestException as ex:
            last_exc = ex
            wait = 2 ** attempt
            log.warning(
                "Fetching initial page failed",
                extra={"attempt": attempt, "wait": wait, "error": str(ex)}
            )
            if attempt < RETRIES:
                time.sleep(wait)

    raise RuntimeError(
        f"All {RETRIES} attempts failed for initial fetch"
    ) from last_exc


def get_years(html: str) -> List[int]:
    """Extract year values from the year-link buttons in the HTML."""
    soup = BeautifulSoup(html, "html.parser")
    year_blocks = soup.find_all("a", class_="year-link")
    if not year_blocks:
        log.warning("Year blocks not found")
        return []

    years = [int(tag.get("id")) for tag in year_blocks]
    log.info("Found years", extra={"years": years})
    return years


def fetch_movies(years: List[int]) -> List[Movie]:
    """
    For each year, call the AJAX endpoint and collect films.
    Returns a flat list of Movie objects across all years.
    """
    movies: List[Movie] = []

    for year in years:
        for attempt in range(1, RETRIES + 1):
            try:
                url = f"{BASE_URL}?ajax=true&year={year}"
                log.info("Fetching movies for year", extra={"year": year, "attempt": attempt})
                response = _session.get(url, timeout=30)

                # FIX: raise_for_status() was missing. Without it a 404 or 500
                # response would silently pass through to .json(), which would
                # either crash with JSONDecodeError (HTML error page) or return
                # wrong data.
                response.raise_for_status()

                # FIX: .json() is now on its own line so JSONDecodeError can be
                # caught separately below. Previously it was chained directly
                # into movies.extend(), making it impossible to distinguish a
                # network error from a JSON parse error.
                data = response.json()

                # FIX: each raw dict is now converted into a Movie object.
                # Previously movies.extend(response.json()) returned List[dict]
                # while the return type annotation said List[Movie] — the Movie
                # dataclass was defined but never used.
                # .get("best_picture", False) is used explicitly rather than **d
                # to handle films that omit the key when best_picture is False.
                movies.extend(
                    Movie(
                        title        = d["title"],
                        year         = d["year"],
                        awards       = d["awards"],
                        nominations  = d["nominations"],
                        best_picture = d.get("best_picture", False),
                    )
                    for d in data
                )

                log.info("Fetched movies", extra={"year": year, "count": len(data)})
                break

            except requests.RequestException as ex:
                wait = 2 ** attempt
                log.warning(
                    "Fetch failed",
                    extra={"attempt": attempt, "year": year, "wait_s": wait, "error": str(ex)},
                )
                if attempt < RETRIES:
                    time.sleep(wait)

            # FIX: JSONDecodeError was not caught at all. It is not a subclass of
            # requests.RequestException, so it would have bubbled up and crashed
            # the entire scraper. It's caught separately here because retrying
            # won't help — if the server returned non-JSON once, it will again.
            except json.JSONDecodeError as ex:
                log.error(
                    "JSON decode failed — skipping year",
                    extra={"year": year, "error": str(ex)},
                )
                break  # exit the retry loop, move to next year

        # FIX: for...else was missing. The else block fires only when the retry
        # loop exhausts all attempts without hitting a break — meaning every
        # attempt failed. Previously this was a silent failure: the year was
        # skipped with no error logged.
        else:
            log.error("All retries exhausted for year", extra={"year": year})

        # FIX: DELAY_SECONDS was defined as a constant but time.sleep() was never
        # called. Each year request was firing back-to-back with no pause.
        time.sleep(DELAY_SECONDS)

    return movies


# ─────────────────────────────────────────────────────────────────────────────
# Storage — atomic CSV write
# FIX: there was no save step at all. All fetched data was printed to logs
# then discarded when run() returned.
# ─────────────────────────────────────────────────────────────────────────────
_CSV_FIELDS = ["title", "year", "awards", "nominations", "best_picture"]


def save_to_csv(movies: List[Movie], path: Path) -> None:
    """Write movies to CSV atomically via a .tmp intermediate file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")

    try:
        with tmp_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            for movie in movies:
                writer.writerow(asdict(movie))
        # Rename is atomic at the OS level — final file is never half-written
        tmp_path.replace(path)
        log.info("Saved CSV", extra={"path": str(path), "rows": len(movies)})
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def run() -> int:
    """
    Execute following pipeline:
        1. Fetch initial page HTML
        2. Collect available years from year buttons
        3. For each year, fetch films from AJAX endpoint
        4. Save to CSV atomically
    """
    try:
        initial_page = fetch_initial_html()
    except RuntimeError as exc:
        log.error("Could not fetch initial page", extra={"error": str(exc)})
        return 1

    years = get_years(initial_page)

    # FIX: `not years` is sufficient — `len(years) == 0` checks the exact same
    # condition. An empty list is already falsy in Python.
    if not years:
        log.warning("No years found, aborting")
        return 1

    all_movies = fetch_movies(years)

    log.info("Fetching complete", extra={"total_records": len(all_movies)})

    # FIX: save step added — previously the pipeline fetched data and discarded it
    save_to_csv(all_movies, OUTPUT_PATH)

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
