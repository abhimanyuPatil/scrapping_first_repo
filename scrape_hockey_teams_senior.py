"""
scrape_hockey_teams_senior.py
─────────────────────────────
Senior-level scraper for https://www.scrapethissite.com/pages/forms/

Pipeline stages (each has a single responsibility):
  1. Fetch    – HTTP with exponential backoff, shared session
  2. Parse    – BeautifulSoup → raw dicts
  3. Validate – field-level + cross-field checks, row-level error capture
  4. Dedupe   – composite key (name, year) guards against duplicate rows
  5. QA       – dataset-level sanity checks AFTER all pages are collected
  6. Store    – atomic CSV write (write-to-temp then rename)
"""

from __future__ import annotations

import csv
import json
import logging
import math
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────────────────────────────────────
# Config
#
# Every tunable value lives here — not scattered through the code.
# When you need to change something you change it in ONE place.
# ─────────────────────────────────────────────────────────────────────────────
BASE_URL        = "https://www.scrapethissite.com/pages/forms/"
PER_PAGE        = 100       # rows per page (site max = 100)
RETRIES         = 4         # max HTTP attempts per page
DELAY_SECONDS   = 1.0       # polite pause between successful page fetches
MAX_PAGES_GUARD = 20        # safety ceiling — stop even if pagination claims more
MIN_EXPECTED    = 400       # QA: fail if fewer rows than this
MAX_EXPECTED    = 800       # QA: fail if more rows than this
OUTPUT_PATH     = Path("output/hockey_teams.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


# ─────────────────────────────────────────────────────────────────────────────
# Structured JSON logging
#
# Plain text logs are readable but not queryable.
# JSON logs can be filtered and aggregated by tools like jq, CloudWatch,
# or Datadog. Each line is a self-contained event object.
#
# Example output:
#   {"ts": "2024-01-01 12:00:00", "level": "INFO", "msg": "Fetching page",
#    "page": 1, "attempt": 1}
# ─────────────────────────────────────────────────────────────────────────────
class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line, including any extra= kwargs."""

    def format(self, record: logging.LogRecord) -> str:
        # Base fields every log line has
        payload: dict = {
            "ts":    self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "msg":   record.getMessage(),
        }
        # Merge in anything passed as extra={...} to the logger call
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
    logger = logging.getLogger("hockey_senior")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _setup_logging()


# ─────────────────────────────────────────────────────────────────────────────
# Schema and validation
#
# Why not just a dict?
# A dict lets any key be any value. A TeamRecord says:
#   "name is always a str, wins is always Optional[int], etc."
# This catches mistakes early — when the data is created — not later when
# some downstream code tries to do arithmetic on a string.
#
# Why is validation separate from the dataclass?
# The dataclass defines the shape. The validator defines the rules.
# Keeping them separate means you can test each independently.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TeamRecord:
    """One validated row from the NHL stats table."""
    name:      str
    year:      int
    wins:      Optional[int]   = None
    losses:    Optional[int]   = None
    ot_losses: Optional[int]   = None
    win_pct:   Optional[float] = None
    gf:        Optional[int]   = None
    ga:        Optional[int]   = None
    diff:      Optional[int]   = None


class RowValidationError(Exception):
    """Raised when a raw dict cannot be turned into a valid TeamRecord."""


def validate_row(raw: dict) -> TeamRecord:
    """
    Convert and validate a raw dict into a TeamRecord.

    Checks performed:
      - name must be a non-empty string
      - year must be an int in [1990, 2030]
      - win_pct must be in [0.0, 1.0] when present
      - diff must equal gf - ga when all three are present

    Raises RowValidationError with a list of all problems found.
    This is intentionally exhaustive — it collects ALL errors in one row
    rather than stopping at the first one, so you see the full picture.
    """
    errors: list[str] = []

    # ── name ──
    name = raw.get("name") or ""
    if not name:
        errors.append("name is empty")

    # ── year ──
    year = raw.get("year")
    if not isinstance(year, int):
        errors.append(f"year is not an int: {year!r}")
    elif not (1990 <= year <= 2030):
        errors.append(f"year {year!r} is outside expected range [1990, 2030]")

    # ── win_pct ──
    win_pct = raw.get("win_pct")
    if win_pct is not None:
        if not isinstance(win_pct, float):
            errors.append(f"win_pct is not a float: {win_pct!r}")
        elif not (0.0 <= win_pct <= 1.0):
            errors.append(f"win_pct {win_pct!r} is outside [0.0, 1.0]")

    # ── cross-field: diff must equal gf - ga ──
    gf   = raw.get("gf")
    ga   = raw.get("ga")
    diff = raw.get("diff")
    if None not in (gf, ga, diff):
        expected_diff = gf - ga  # type: ignore[operator]
        if expected_diff != diff:
            errors.append(
                f"diff mismatch: {gf} - {ga} = {expected_diff}, "
                f"but diff field = {diff}"
            )

    if errors:
        raise RowValidationError(errors)

    return TeamRecord(
        name      = name,
        year      = year,                   # type: ignore[arg-type]
        wins      = raw.get("wins"),
        losses    = raw.get("losses"),
        ot_losses = raw.get("ot_losses"),
        win_pct   = win_pct,
        gf        = gf,
        ga        = ga,
        diff      = diff,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Text cleaning helpers
# ─────────────────────────────────────────────────────────────────────────────
_INT_RE   = re.compile(r"[+-]?[0-9]+", re.ASCII)
_FLOAT_RE = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", re.ASCII
)


def clean_text(s: Optional[str]) -> str:
    """Normalize unicode, replace NBSP, collapse whitespace, strip edges."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _numeric_text(s: Optional[str]) -> str:
    """Prepare text for numeric parsing: normalize, replace Unicode minus."""
    return clean_text(s).replace("\u2212", "-").replace(",", "")


def to_int(s: str) -> Optional[int]:
    """Parse integer from text. Returns None for blank or non-integer input."""
    t = _numeric_text(s)
    if not _INT_RE.fullmatch(t):
        return None
    try:
        return int(t)
    except ValueError:
        return None


def to_float(s: str) -> Optional[float]:
    """Parse float from text. Returns None for blank or non-numeric input."""
    t = _numeric_text(s)
    if not _FLOAT_RE.fullmatch(t):
        return None
    try:
        value = float(t)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def get_text(block, css_class: str) -> str:
    """Return cleaned text of the first element with the given CSS class."""
    el = block.select_one(f".{css_class}")
    return clean_text(el.get_text()) if el else ""


# ─────────────────────────────────────────────────────────────────────────────
# HTTP — shared session + exponential backoff
#
# Shared Session: one TCP/TLS connection is reused across all page requests.
# Without a session, Python opens and closes a connection for every request.
#
# Exponential backoff: on failure, wait 2s → 4s → 8s before retrying.
# A flat retry loop hammers a struggling server and makes things worse.
# Backing off gives the server time to recover.
# ─────────────────────────────────────────────────────────────────────────────
_session = requests.Session()
_session.headers.update(HEADERS)


def fetch_page(page_num: int) -> str:
    """
    Fetch one page with exponential backoff.
    Returns raw HTML on success.
    Raises RuntimeError if every attempt fails.
    """
    url = f"{BASE_URL}?page_num={page_num}&per_page={PER_PAGE}"
    last_exc: Optional[Exception] = None

    for attempt in range(1, RETRIES + 1):
        try:
            log.info("Fetching page", extra={"page": page_num, "attempt": attempt})
            response = _session.get(url, timeout=30)
            response.raise_for_status()
            return response.text

        except requests.RequestException as exc:
            last_exc = exc
            wait = 2 ** attempt      # attempt 1 → 2s, 2 → 4s, 3 → 8s, 4 → 16s
            log.warning(
                "Fetch failed",
                extra={"page": page_num, "attempt": attempt,
                       "wait_s": wait, "error": str(exc)},
            )
            if attempt < RETRIES:
                time.sleep(wait)     # only sleep if there are more attempts left

    raise RuntimeError(
        f"All {RETRIES} attempts failed for page {page_num}"
    ) from last_exc


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic pagination
#
# The site renders a <ul class="pagination"> listing every page as a link:
#   <a href="/pages/forms/?page_num=3&per_page=100">3</a>
#
# We read those numbers from the HTML instead of hardcoding a MAX_PAGES
# constant. If the dataset grows next year from 582 to 700 rows the scraper
# automatically picks up the extra pages — zero code changes needed.
#
# MAX_PAGES_GUARD is kept as a safety ceiling. It prevents a bug in the
# pagination parser from causing an infinite or runaway loop.
# ─────────────────────────────────────────────────────────────────────────────
def get_total_pages(html: str) -> int:
    """
    Parse the pagination widget and return the highest page number found.
    Falls back to MAX_PAGES_GUARD if the widget is missing or unreadable.
    """
    soup = BeautifulSoup(html, "html.parser")
    pagination = soup.find("ul", class_="pagination")

    if not pagination:
        log.warning(
            "Pagination widget not found, using guard value",
            extra={"guard": MAX_PAGES_GUARD},
        )
        return MAX_PAGES_GUARD

    page_nums: list[int] = []
    for a in pagination.find_all("a", href=True):
        # Parse the href query string to extract page_num=N
        qs = parse_qs(urlparse(a["href"]).query)
        if "page_num" in qs:
            try:
                page_nums.append(int(qs["page_num"][0]))
            except ValueError:
                pass  # ignore non-integer page numbers (e.g. "next" button)

    if not page_nums:
        log.warning(
            "No page numbers found in pagination, using guard value",
            extra={"guard": MAX_PAGES_GUARD},
        )
        return MAX_PAGES_GUARD

    total = min(max(page_nums), MAX_PAGES_GUARD)  # never exceed the safety ceiling
    log.info("Discovered total pages", extra={"total_pages": total})
    return total


# ─────────────────────────────────────────────────────────────────────────────
# Parsing — HTML rows → validated TeamRecord objects
# ─────────────────────────────────────────────────────────────────────────────
def parse_rows(
    html: str,
    page_num: int,
) -> tuple[list[TeamRecord], list[dict]]:
    """
    Extract every <tr class="team"> row and validate each one.

    Returns:
        good   – rows that passed validation as TeamRecord objects
        failed – rows that failed, with the raw data and error list attached,
                 so they can be investigated after the run

    Why capture failures instead of crashing?
    One bad row in page 4 should not abort the scrape of pages 5 and 6.
    Capture the problem, continue, and report at the end.
    """
    soup   = BeautifulSoup(html, "html.parser")
    blocks = soup.find_all("tr", class_="team")
    good:   list[TeamRecord] = []
    failed: list[dict]       = []

    for index, row in enumerate(blocks):
        raw = {
            "name":      get_text(row, "name"),
            "year":      to_int(get_text(row, "year")),
            "wins":      to_int(get_text(row, "wins")),
            "losses":    to_int(get_text(row, "losses")),
            "ot_losses": to_int(get_text(row, "ot-losses")),
            "win_pct":   to_float(get_text(row, "pct")),
            "gf":        to_int(get_text(row, "gf")),
            "ga":        to_int(get_text(row, "ga")),
            "diff":      to_int(get_text(row, "diff")),
        }

        try:
            good.append(validate_row(raw))
        except RowValidationError as exc:
            failed.append({
                "page":   page_num,
                "index":  index,
                "raw":    raw,
                "errors": exc.args[0],      # list of error strings
            })
            log.warning(
                "Row failed validation",
                extra={"page": page_num, "index": index, "errors": exc.args[0]},
            )

    return good, failed


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
#
# Why do duplicates occur?
#   - Pagination overlap: some sites repeat the last row of page N as the
#     first row of page N+1
#   - Re-running the scraper on data already saved
#   - The source site serving duplicate rows
#
# The unique key is (name, year): a team appears exactly once per season
# in this dataset. If we see the same key twice, we keep the first occurrence
# and log the duplicate so we know it happened.
# ─────────────────────────────────────────────────────────────────────────────
def deduplicate(
    records: list[TeamRecord],
) -> tuple[list[TeamRecord], int]:
    """
    Remove records with duplicate (name, year) keys.
    Returns (unique_records, duplicate_count).
    """
    seen:      set[tuple[str, int]] = set()
    unique:    list[TeamRecord]     = []
    dup_count: int                  = 0

    for record in records:
        key = (record.name, record.year)
        if key in seen:
            dup_count += 1
            log.warning(
                "Duplicate skipped",
                extra={"name": record.name, "year": record.year},
            )
        else:
            seen.add(key)
            unique.append(record)

    return unique, dup_count


# ─────────────────────────────────────────────────────────────────────────────
# Quality checks — dataset-level assertions
#
# Individual row validation (validate_row) catches row-level problems.
# QA catches dataset-level problems:
#   "We got 12 rows instead of ~580 — something went very wrong."
#
# These run AFTER all pages are collected and deduplicated.
# Hard failures raise an exception and abort the write.
# Soft warnings log but let the run continue.
# ─────────────────────────────────────────────────────────────────────────────
def quality_check(records: list[TeamRecord], failed: list[dict]) -> None:
    """
    Assert the final dataset looks sane.
    Raises ValueError on hard failures (bad row count).
    Logs warnings for soft issues (validation failures, year gaps).
    """
    total = len(records)
    log.info(
        "Running quality checks",
        extra={"total_rows": total, "failed_rows": len(failed)},
    )

    # Hard check: row count must be within the expected window.
    # If it isn't, something went badly wrong with fetching or parsing.
    if not (MIN_EXPECTED <= total <= MAX_EXPECTED):
        raise ValueError(
            f"Row count {total} is outside expected range "
            f"[{MIN_EXPECTED}, {MAX_EXPECTED}]. Aborting write."
        )

    # Soft check: any rows that failed validation?
    if failed:
        log.warning(
            "Rows failed validation — inspect before trusting the data",
            extra={"count": len(failed)},
        )

    # Soft check: year coverage should start at 1990
    years    = sorted({r.year for r in records})
    min_year = years[0]
    max_year = years[-1]
    if min_year != 1990:
        log.warning(
            "Dataset does not start at 1990",
            extra={"earliest_year": min_year},
        )

    log.info(
        "Quality checks passed",
        extra={"rows": total, "year_range": f"{min_year}–{max_year}"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Atomic CSV write
#
# Why "atomic"?
# Writing directly to the final file: if your process crashes or is killed
# halfway through, you end up with a corrupt file — partial data, truncated
# rows. This is especially bad for data pipelines that run on a schedule.
#
# The write-then-rename pattern:
#   1. Write all data to output/hockey_teams.csv.tmp
#   2. When the write is complete, rename .tmp → .csv
#
# A rename is atomic at the OS level: it either fully completes or
# does not happen at all. The final file is always either the previous
# complete version or the new complete version — never partial.
# ─────────────────────────────────────────────────────────────────────────────
_CSV_FIELDS = [
    "name", "year", "wins", "losses", "ot_losses",
    "win_pct", "gf", "ga", "diff",
]


def save_to_csv(records: list[TeamRecord], path: Path) -> None:
    """Write records to CSV atomically via a .tmp intermediate file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")

    try:
        with tmp_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            for record in records:
                # asdict() converts the dataclass to a plain dict for the writer
                writer.writerow(asdict(record))

        # Atomic rename: replaces the destination in a single OS call
        tmp_path.replace(path)
        log.info("Saved CSV", extra={"path": str(path), "rows": len(records)})

    except Exception:
        # Always clean up the temp file on failure
        if tmp_path.exists():
            tmp_path.unlink()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline — orchestrates all stages
# ─────────────────────────────────────────────────────────────────────────────
def run() -> int:
    """
    Execute the full pipeline:
      Stage 1 — Fetch page 1 and discover total page count dynamically
      Stage 2 — Fetch remaining pages with polite delay between each
      Stage 3 — Deduplicate by (name, year)
      Stage 4 — Dataset-level quality checks
      Stage 5 — Atomic CSV write

    Returns 0 for success, 1 for failure (Unix convention).
    """
    all_records: list[TeamRecord] = []
    all_failed:  list[dict]       = []

    # ── Stage 1: Fetch page 1, discover total page count ──────────────────
    # We always need page 1 anyway, so fetch it first and read the pagination
    # widget from the response. This avoids a separate "discovery" request.
    try:
        first_page_html = fetch_page(1)
    except RuntimeError as exc:
        log.error("Could not fetch first page, aborting", extra={"error": str(exc)})
        return 1

    total_pages          = get_total_pages(first_page_html)
    good, failed         = parse_rows(first_page_html, page_num=1)
    all_records.extend(good)
    all_failed.extend(failed)

    # ── Stage 2: Fetch remaining pages ────────────────────────────────────
    for page_num in range(2, total_pages + 1):
        time.sleep(DELAY_SECONDS)   # pause before every new page request

        try:
            html = fetch_page(page_num)
        except RuntimeError as exc:
            # One page failing does not abort the whole job.
            # Log it and continue — QA will catch if too many rows are missing.
            log.error(
                "Skipping page after all retries exhausted",
                extra={"page": page_num, "error": str(exc)},
            )
            continue

        good, failed = parse_rows(html, page_num=page_num)

        # Stop early if a page returns no rows at all.
        # This means we've gone past the last real page of data.
        if not good:
            log.info(
                "Empty page — stopping early",
                extra={"page": page_num},
            )
            break

        all_records.extend(good)
        all_failed.extend(failed)

    log.info(
        "Fetching complete",
        extra={"total_records": len(all_records), "total_failed": len(all_failed)},
    )

    # ── Stage 3: Deduplicate ───────────────────────────────────────────────
    unique_records, dup_count = deduplicate(all_records)
    if dup_count:
        log.warning("Duplicates removed", extra={"count": dup_count})

    # ── Stage 4: Quality check ─────────────────────────────────────────────
    try:
        quality_check(unique_records, all_failed)
    except ValueError as exc:
        log.error("Quality check failed — not writing output", extra={"error": str(exc)})
        return 1

    # ── Stage 5: Atomic write ──────────────────────────────────────────────
    save_to_csv(unique_records, OUTPUT_PATH)

    return 0   # 0 = success (Unix convention)


if __name__ == "__main__":
    raise SystemExit(run())
