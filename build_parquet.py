"""Step 1: turn output/hockey_teams.csv into a bigger Parquet file.

The scraped CSV has only ~580 rows. To make Parquet worth using we repeat
the rows many times and add small random variation, so the copies are not
identical. The result is written to output/hockey_teams.parquet.

Usage:
    python build_parquet.py                 # 200 copies (~116k rows)
    python build_parquet.py --copies 1000
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from common import setup_logger
from common.gcp_local import PARQUET_PATH, SOURCE_CSV

log = setup_logger("build_parquet")

# Say the type of every column explicitly instead of letting pandas guess.
# "Int64" (capital I) is pandas' integer type that can hold missing values.
# Plain "int64" cannot, and pandas would silently turn the column into floats.
DTYPES = {
    "name": "string",
    "year": "int64",
    "wins": "int64",
    "losses": "int64",
    "ot_losses": "Int64",
    "win_pct": "float64",
    "gf": "int64",
    "ga": "int64",
    "diff": "int64",
}


def load_source() -> pd.DataFrame:
    return pd.read_csv(SOURCE_CSV, dtype=DTYPES)


def multiply(df: pd.DataFrame, copies: int, seed: int = 42) -> pd.DataFrame:
    """Repeat df `copies` times, with small random changes to the numbers."""
    rng = np.random.default_rng(seed)
    big = pd.concat([df] * copies, ignore_index=True)
    big["copy_id"] = np.repeat(np.arange(copies), len(df)).astype("int64")

    n = len(big)
    # Nudge wins, losses and goals by a few units. Never go below zero.
    for col in ("wins", "losses", "gf", "ga"):
        noise = rng.integers(-3, 4, size=n)
        big[col] = (big[col] + noise).clip(lower=0).astype("int64")

    # Keep derived columns consistent with the changed numbers.
    big["diff"] = big["gf"] - big["ga"]
    games = big["wins"] + big["losses"] + big["ot_losses"].fillna(0).astype("int64")
    big["win_pct"] = (big["wins"] / games.where(games > 0)).round(3)

    # Make team names distinct per copy so the data is not one team repeated.
    big["name"] = np.where(
        big["copy_id"] == 0,
        big["name"],
        big["name"] + " #" + big["copy_id"].astype(str),
    )
    big["name"] = big["name"].astype("string")

    # A real timestamp column, so BigQuery gets a TIMESTAMP type to show.
    big["scraped_at"] = pd.Timestamp(datetime.now(timezone.utc)).floor("s")
    return big


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--copies", type=int, default=200)
    args = parser.parse_args()

    if not SOURCE_CSV.exists():
        raise SystemExit(f"{SOURCE_CSV} not found. Run scrape_hockey_teams_senior.py first.")

    df = load_source()
    big = multiply(df, args.copies)

    PARQUET_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PARQUET_PATH.with_suffix(".parquet.tmp")  # write then rename, like common/storage.py
    big.to_parquet(tmp, engine="pyarrow", compression="snappy", index=False)
    tmp.replace(PARQUET_PATH)

    log.info(
        "Wrote Parquet",
        extra={
            "path": str(PARQUET_PATH),
            "rows": len(big),
            "csv_kb": round(SOURCE_CSV.stat().st_size / 1024, 1),
            "parquet_kb": round(PARQUET_PATH.stat().st_size / 1024, 1),
        },
    )


if __name__ == "__main__":
    main()
