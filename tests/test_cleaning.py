"""Property 6: Numeric and text parsing.

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7**
"""
import math
import re

from hypothesis import given, strategies as st

from scrape_hockey_teams import clean_text, to_float, to_int

blank_st = st.text(alphabet=" \t\r\n\xa0\u2003", max_size=10)
# Letters/punctuation only: never a valid number (no digits, no lone sign-digit).
non_numeric_st = st.text(
    alphabet=st.sampled_from(list("abcdxyzABCXYZ!@#$%^&*()_=<>?/;:'\" ")),
    min_size=1,
    max_size=12,
)


@given(st.integers())
def test_to_int_signed(n):
    assert to_int(f"{n:+d}") == n


@given(st.integers())
def test_to_int_unicode_minus(n):
    assert to_int(f"{n:+d}".replace("-", "\u2212")) == n


@given(st.integers())
def test_to_int_thousands_separators(n):
    assert to_int(f"{n:,}") == n


@given(blank_st | non_numeric_st)
def test_to_int_and_to_float_none_for_blank_or_non_numeric(s):
    assert to_int(s) is None
    assert to_float(s) is None


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_to_float_roundtrip(x):
    assert to_float(str(x)) == x


@given(st.sampled_from(["nan", "inf", "-inf", "NaN", "Infinity", "1e999", "-1e999"]))
def test_to_float_rejects_non_finite(s):
    assert to_float(s) is None


@given(st.text())
def test_clean_text_no_edge_or_repeated_whitespace(s):
    out = clean_text(s)
    assert out == out.strip()
    assert not re.search(r"\s{2,}", out)
    assert "\xa0" not in out


def test_clean_text_none_is_empty():
    assert clean_text(None) == ""
