"""Property 5: Row accounting.

**Validates: Requirements 5.5, 5.6, 5.8, 5.9**
"""
import os
import sys

from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrape_hockey_teams import parse  # noqa: E402

CELLS = ["name", "year", "wins", "losses", "ot-losses", "pct", "gf", "ga", "diff"]
MISSING_CHOICES = [c for c in CELLS if c != "name"]

# Text that cannot introduce markup.
safe_text = st.text(
    alphabet=st.characters(blacklist_characters="<>&", blacklist_categories=("Cs",)),
    max_size=30,
)
names = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij ", min_size=1, max_size=12).filter(
    lambda s: s.strip() != ""
)
bad_numbers = st.sampled_from(["abc", "N/A", "--", "x1", "1.5", "one"])


def _row_html(cells: dict) -> str:
    tds = []
    for cls in CELLS:
        if cls not in cells:
            continue
        extra = ""
        if cls in ("pct", "diff"):
            extra = " text-success"
        tds.append(f'<td class="{cls}{extra}">{cells[cls]}</td>')
    return '<tr class="team">' + "".join(tds) + "</tr>"


def _good_cells(name: str) -> dict:
    return {
        "name": name, "year": "1990", "wins": "30", "losses": "40",
        "ot-losses": "5", "pct": "0.375", "gf": "250", "ga": "300", "diff": "-50",
    }


@st.composite
def row_specs(draw):
    kind = draw(st.sampled_from(["ok", "ok_blank_ot", "empty_name", "missing", "bad_ot", "bad_wins"]))
    name = draw(names)
    cells = _good_cells(name)
    if kind == "ok_blank_ot":
        cells["ot-losses"] = ""
    elif kind == "empty_name":
        if draw(st.booleans()):
            del cells["name"]
        else:
            cells["name"] = draw(st.sampled_from(["", "   ", "\xa0"]))
    elif kind == "missing":
        del cells[draw(st.sampled_from(MISSING_CHOICES))]
    elif kind == "bad_ot":
        cells["ot-losses"] = draw(bad_numbers)
    elif kind == "bad_wins":
        cells["wins"] = draw(bad_numbers)
    return kind, cells


@settings(max_examples=100, deadline=None)
@given(specs=st.lists(row_specs(), max_size=15), noise=safe_text)
def test_row_accounting(specs, noise):
    body = noise + "".join(_row_html(c) + noise for _, c in specs)
    html = f"<html><body><table>{body}</table></body></html>"

    rows, failed = parse(html)  # must never raise

    assert len(rows) + len(failed) == len(specs)

    failed_by_index = {f["index"]: f for f in failed}
    assert len(failed_by_index) == len(failed)  # unique indexes

    for i, (kind, _) in enumerate(specs):
        if kind in ("empty_name", "missing", "bad_ot", "bad_wins"):
            assert i in failed_by_index, f"row {i} ({kind}) should have failed"
            assert failed_by_index[i]["reason"]
        else:
            assert i not in failed_by_index, f"row {i} ({kind}) should have parsed"


@settings(max_examples=50, deadline=None)
@given(text=st.text(max_size=200))
def test_parse_never_raises_on_arbitrary_text(text):
    rows, failed = parse(text)
    assert rows == [] and failed == []
