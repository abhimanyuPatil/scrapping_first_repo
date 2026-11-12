"""Data storage utilities with atomic writes.

Atomic writes prevent data corruption if the process crashes mid-write.
The pattern is: write to a temp file, then rename it to the final name.
A rename is atomic at the OS level — the file either fully exists or doesn't.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, List, Optional


def save_to_csv_atomic(
    records: List[Any],
    path: Path,
    fieldnames: List[str],
    log: Optional[Any] = None,
) -> None:
    """
    Write records to CSV atomically via temp file + rename.
    
    The write happens to a .tmp file first, then is renamed to the final path.
    This ensures the final file is never half-written if the process crashes.
    
    Args:
        records: list of dataclass instances or dicts. If dataclass instances,
                 they are automatically converted to dicts via asdict().
        path: final CSV path (e.g., Path("output/hockey_teams.csv"))
        fieldnames: column names for the CSV header, in order
        log: optional logger with .info() method
        
    Raises:
        Exception: any write or conversion error. The temp file is cleaned up
                   automatically on failure.
        
    Example:
        from pathlib import Path
        from dataclasses import dataclass
        from common import save_to_csv_atomic, setup_logger
        
        @dataclass
        class Team:
            name: str
            year: int
            wins: int
        
        teams = [Team("Boston", 1990, 44), Team("Calgary", 1990, 46)]
        log = setup_logger("hockey")
        
        save_to_csv_atomic(
            records=teams,
            path=Path("output/teams.csv"),
            fieldnames=["name", "year", "wins"],
            log=log,
        )
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with tmp_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in records:
                # Convert dataclass to dict if needed, otherwise assume it's already a dict
                if hasattr(record, "__dataclass_fields__"):
                    row = asdict(record)
                else:
                    row = record
                writer.writerow(row)

        # Atomic rename: replaces the destination in a single OS call
        tmp_path.replace(path)
        
        if log:
            log.info("Saved CSV", extra={"path": str(path), "rows": len(records)})

    except Exception:
        # Clean up temp file on failure
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def save_to_json_atomic(
    data: Any,
    path: Path,
    log: Optional[Any] = None,
) -> None:
    """
    Write JSON atomically via temp file + rename.
    
    Args:
        data: any JSON-serializable data (dict, list, etc.)
        path: final JSON path (e.g., Path("output/movies.json"))
        log: optional logger with .info() method
        
    Raises:
        Exception: any serialization or write error. The temp file is cleaned
                   up automatically on failure.
        
    Example:
        from pathlib import Path
        from common import save_to_json_atomic, setup_logger
        
        data = [{"title": "Spotlight", "year": 2015, "awards": 2}]
        log = setup_logger("movies")
        
        save_to_json_atomic(
            data=data,
            path=Path("output/movies.json"),
            log=log,
        )
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

        # Atomic rename
        tmp_path.replace(path)
        
        if log:
            log.info("Saved JSON", extra={"path": str(path)})

    except Exception:
        # Clean up temp file on failure
        if tmp_path.exists():
            tmp_path.unlink()
        raise
