"""Property 2: Schema of parsed records.

**Validates: Requirements 5.2, 5.3, 5.4, 5.7**
"""
import html as html_lib
import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scrape_hockey_teams  # noqa: E402

EXPECTED_KEYS = {"team", "year", "wins", "losses", "ot_losses", "win", "gf", "ga", "diff"}
INT_FIELDS = ("year", "wins", "losses", "gf", "ga", "diff")

team_names = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), max_codepoint=0x24F),
    min_size=1,
    max_size=20,
)

rows = st.fixed_dictionaries(
    {
        "team": team_names,
        "year": st.integers(1900, 2100),
        "wins": st.integers(0, 200),
        "losses": st.integers(0, 200),
        "ot_losses": st.one_of(st.none(), st.integers(0, 100)),
        "win": st.floats(0.0, 1.0, allow_nan=False, allow_infinity=False),
        "gf": st.integers(0, 1000),
        "ga": st.integers(0, 1000),
        "diff": st.integers(-500, 500),
    }
)


def render_page(records):
    """Render records as site-style tr.team rows."""
    trs = []
    for r in records:
        ot = "" if r["ot_losses"] is None else str(r["ot_losses"])
        diff = f"{r['diff']:+d}" if r["diff"] > 0 else str(r["diff"])
        trs.append(
            '<tr class="team">'
            f'<td class="name"> {html_lib.escape(r["team"])} </td>'
            f'<td class="year">{r["year"]}</td>'
            f'<td class="wins">{r["wins"]}</td>'
            f'<td class="losses">{r["losses"]}</td>'
            f'<td class="ot-losses">{ot}</td>'
            f'<td class="pct text-success">{r["win"]!r}</td>'
            f'<td class="gf">{r["gf"]}</td>'
            f'<td class="ga">{r["ga"]}</td>'
            f'<td class="diff text-danger">{diff}</td>'
            "</tr>"
        )
    return "<table>" + "".join(trs) + "</table>"


@settings(max_examples=100, deadline=None)
@given(st.lists(rows, min_size=1, max_size=10))
def test_parsed_records_match_schema(records):
    parsed, failed = scrape_hockey_teams.parse(render_page(records))

    assert failed == []
    assert len(parsed) == len(records)

    for src, rec in zip(records, parsed):
        assert set(rec.keys()) == EXPECTED_KEYS

        for field in INT_FIELDS:
            assert type(rec[field]) is int  # int, and not bool
            assert rec[field] == src[field]

        assert type(rec["win"]) is float
        assert rec["win"] == src["win"]  # unscaled fraction

        assert isinstance(rec["team"], str) and rec["team"] != ""
        assert rec["team"] == src["team"]

        if src["ot_losses"] is None:
            assert rec["ot_losses"] is None
        else:
            assert type(rec["ot_losses"]) is int
            assert rec["ot_losses"] == src["ot_losses"]
