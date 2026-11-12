# Common Scraping Utilities

Shared modules for all web scrapers in this project. Centralizes retry logic, logging, HTTP handling, and storage patterns.

---

## Modules

### `config.py`
Shared constants used across all scrapers.

```python
from common.config import RETRIES, DELAY_SECONDS, HEADERS, TIMEOUT_SECONDS

# RETRIES = 4
# DELAY_SECONDS = 1.0
# TIMEOUT_SECONDS = 30
# HEADERS = {...User-Agent...}
```

---

### `logging_utils.py`
Structured JSON logging to stderr.

```python
from common import setup_logger

log = setup_logger("my_scraper")
log.info("Fetching page", extra={"page": 1, "attempt": 1})
# Output: {"ts": "2024-01-01 12:00:00", "level": "INFO", "msg": "Fetching page", "page": 1, "attempt": 1}
```

**Why JSON logs?**  
Plain text logs are human-readable but not queryable. JSON logs can be filtered and aggregated by tools like `jq`, CloudWatch, or Datadog.

---

### `http.py`
HTTP utilities with exponential backoff retry.

```python
from common import create_session, fetch_with_retry, setup_logger

log = setup_logger("my_scraper")
session = create_session()

# Fetch with automatic retry on failure
html = fetch_with_retry(
    session=session,
    url="https://example.com",
    timeout=30,
    retries=4,
    log=log,
)
```

**Retry behavior:**
- Attempt 1 fails → wait 2s
- Attempt 2 fails → wait 4s
- Attempt 3 fails → wait 8s
- Attempt 4 fails → wait 16s
- Waits are capped at 60 seconds max

---

### `storage.py`
Atomic file writes (CSV and JSON).

```python
from pathlib import Path
from dataclasses import dataclass
from common import save_to_csv_atomic, save_to_json_atomic, setup_logger

@dataclass
class Team:
    name: str
    year: int
    wins: int

teams = [Team("Boston", 1990, 44), Team("Calgary", 1990, 46)]
log = setup_logger("hockey")

# Save to CSV atomically
save_to_csv_atomic(
    records=teams,
    path=Path("output/teams.csv"),
    fieldnames=["name", "year", "wins"],
    log=log,
)

# Save to JSON atomically
save_to_json_atomic(
    data=[{"name": "Boston", "year": 1990, "wins": 44}],
    path=Path("output/teams.json"),
    log=log,
)
```

**Why atomic writes?**  
If the process crashes mid-write, you get a corrupt file. Atomic writes prevent this by writing to a `.tmp` file first, then renaming it. The rename is atomic at the OS level — the file is either complete or doesn't exist.

---

## Example: Minimal scraper using common utilities

```python
"""Minimal scraper using common utilities."""
from pathlib import Path
from dataclasses import dataclass
from bs4 import BeautifulSoup

from common import (
    setup_logger,
    create_session,
    fetch_with_retry,
    save_to_csv_atomic,
)

log = setup_logger("minimal")

@dataclass
class Item:
    title: str
    price: str

def parse(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.find_all("div", class_="item"):
        title = card.find("h3").get_text(strip=True)
        price = card.find("span", class_="price").get_text(strip=True)
        items.append(Item(title=title, price=price))
    return items

def run() -> int:
    session = create_session()
    html = fetch_with_retry(session, "https://example.com/items", log=log)
    items = parse(html)
    
    save_to_csv_atomic(
        records=items,
        path=Path("output/items.csv"),
        fieldnames=["title", "price"],
        log=log,
    )
    
    return 0

if __name__ == "__main__":
    raise SystemExit(run())
```

---

## Migration guide

When refactoring an existing scraper to use `common/`:

1. **Replace logging setup:**
   ```python
   # Before
   class _JsonFormatter(...): ...
   def _setup_logging(): ...
   log = _setup_logging()
   
   # After
   from common import setup_logger
   log = setup_logger("my_scraper")
   ```

2. **Replace constants:**
   ```python
   # Before
   RETRIES = 4
   DELAY_SECONDS = 1.0
   HEADERS = {...}
   
   # After
   from common.config import RETRIES, DELAY_SECONDS, HEADERS
   ```

3. **Replace retry loop:**
   ```python
   # Before
   for attempt in range(1, RETRIES + 1):
       try:
           response = session.get(url)
           response.raise_for_status()
           return response.text
       except requests.RequestException as ex:
           wait = 2 ** attempt
           log.warning(...)
           time.sleep(wait)
   
   # After
   from common import fetch_with_retry
   html = fetch_with_retry(session, url, log=log)
   ```

4. **Replace save logic:**
   ```python
   # Before
   tmp_path = path.with_suffix(".tmp")
   with tmp_path.open("w") as f:
       writer = csv.DictWriter(f, fieldnames=...)
       ...
   tmp_path.replace(path)
   
   # After
   from common import save_to_csv_atomic
   save_to_csv_atomic(records, path, fieldnames, log=log)
   ```

---

## Testing

Run the smoke test to verify all modules work:

```bash
cd /path/to/comet
uv run python3 -c "
from common import setup_logger, create_session

log = setup_logger('test')
log.info('Testing common module', extra={'status': 'success'})

session = create_session()
print('✓ Session created')
"
```

Expected output:
```
{"ts": "...", "level": "INFO", "msg": "Testing common module", "status": "success"}
✓ Session created
```

No virtual environment setup needed — `uv run` handles dependencies automatically.
