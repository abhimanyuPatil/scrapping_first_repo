"""Tests for the fetcher (page_url / fetch_html). Never hits the network."""
import os
import sys

import pytest
import requests
from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scrape_hockey_teams as sht  # noqa: E402


# Feature: hockey-teams-scraper, Property 0: page size is explicit
# **Validates: Requirements 1.1, 1.2**
@given(st.integers(min_value=1, max_value=10**9))
def test_page_url_has_page_num_and_single_per_page(n):
    url = sht.page_url(n)
    assert f"page_num={n}" in url
    assert url.count("per_page=100") == 1
    assert url.count("per_page=") == 1


@given(st.integers(max_value=0))
def test_page_url_rejects_invalid_page_num(n):
    with pytest.raises(ValueError):
        sht.page_url(n)


class FakeResp:
    def __init__(self, status=200, text="body"):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def sleeps(monkeypatch):
    rec = []
    monkeypatch.setattr(sht.time, "sleep", lambda s: rec.append(s))
    return rec


def install(monkeypatch, outcomes):
    fake = FakeSession(outcomes)
    monkeypatch.setattr(sht, "session", fake)
    return fake


# **Validates: Requirements 4.1, 4.2**
def test_retries_three_times_then_returns_empty(monkeypatch, sleeps):
    fake = install(monkeypatch, [requests.ConnectionError("x")] * 3)
    assert sht.fetch_html("http://x") == ""
    assert len(fake.calls) == 3


def test_http_500_counts_as_failure(monkeypatch, sleeps):
    fake = install(monkeypatch, [FakeResp(500)] * 3)
    assert sht.fetch_html("http://x") == ""
    assert len(fake.calls) == 3


def test_success_on_later_attempt_stops_retrying(monkeypatch, sleeps):
    fake = install(
        monkeypatch, [requests.Timeout("t"), FakeResp(200, "hello"), FakeResp(200, "no")]
    )
    assert sht.fetch_html("http://x") == "hello"
    assert len(fake.calls) == 2
    assert len(sleeps) == 1


# **Validates: Requirement 4.3**
def test_backoff_delays_strictly_increase(monkeypatch, sleeps):
    install(monkeypatch, [FakeResp(500)] * 3)
    sht.fetch_html("http://x")
    assert len(sleeps) >= 2
    assert all(b > a for a, b in zip(sleeps, sleeps[1:]))


# **Validates: Requirements 1.5**
def test_user_agent_and_timeout(monkeypatch, sleeps):
    fake = install(monkeypatch, [FakeResp(200, "ok")])
    sht.fetch_html("http://x")
    _, kwargs = fake.calls[0]
    assert kwargs["timeout"] == 30
    assert kwargs["headers"]["User-Agent"].startswith("Mozilla/5.0")
