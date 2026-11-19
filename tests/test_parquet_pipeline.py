"""Tests for the CSV -> Parquet -> BigQuery pipeline helpers (no network needed)."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
import pytest

from build_parquet import DTYPES, multiply
from load_to_bigquery import bq_type, sql_literal


@pytest.fixture
def small_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "name": ["Boston Bruins", "Buffalo Sabres"],
            "year": [1990, 1990],
            "wins": [44, 31],
            "losses": [24, 30],
            "ot_losses": [None, 5],
            "win_pct": [0.55, 0.388],
            "gf": [299, 292],
            "ga": [264, 278],
            "diff": [35, 14],
        }
    ).astype(DTYPES)


def test_multiply_row_count_and_columns(small_df):
    big = multiply(small_df, copies=10)
    assert len(big) == 20
    assert {"copy_id", "scraped_at"} <= set(big.columns)
    assert big["copy_id"].nunique() == 10


def test_multiply_keeps_nulls_and_nullable_int(small_df):
    big = multiply(small_df, copies=10)
    assert str(big["ot_losses"].dtype) == "Int64"
    assert big["ot_losses"].isna().sum() == 10  # one missing value per copy


def test_multiply_keeps_diff_consistent_and_nonnegative(small_df):
    big = multiply(small_df, copies=50)
    assert (big["diff"] == big["gf"] - big["ga"]).all()
    assert (big[["wins", "losses", "gf", "ga"]] >= 0).all().all()


def test_multiply_is_repeatable(small_df):
    a = multiply(small_df, copies=5).drop(columns="scraped_at")
    b = multiply(small_df, copies=5).drop(columns="scraped_at")
    pd.testing.assert_frame_equal(a, b)


@pytest.mark.parametrize(
    "arrow_type, expected",
    [
        (pa.int64(), "INTEGER"),
        (pa.float64(), "FLOAT"),
        (pa.string(), "STRING"),
        (pa.bool_(), "BOOLEAN"),
        (pa.timestamp("us", tz="UTC"), "TIMESTAMP"),
    ],
)
def test_bq_type_mapping(arrow_type, expected):
    assert bq_type(arrow_type) == expected


def test_bq_type_rejects_unknown():
    with pytest.raises(ValueError):
        bq_type(pa.list_(pa.int64()))


def test_sql_literal_basic_values():
    assert sql_literal(None) == "NULL"
    assert sql_literal(float("nan")) == "NULL"
    assert sql_literal(42) == "42"
    assert sql_literal(0.55) == "0.55"
    assert sql_literal(True) == "TRUE"


def test_sql_literal_escapes_quotes_and_backslashes():
    assert sql_literal("O'Brien") == "'O\\'Brien'"
    assert sql_literal("a\\b") == "'a\\\\b'"


def test_sql_literal_timestamp():
    ts = datetime(2025, 1, 31, 14, 30, tzinfo=timezone.utc)
    assert sql_literal(ts) == "TIMESTAMP '2025-01-31T14:30:00+00:00'"
