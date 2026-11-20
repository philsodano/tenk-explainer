"""Regression tests for the parser, using real filings.

The expected counts were verified by hand against each filing (listing every
risk title and checking it against the bold/italic headings in the raw HTML).
If a parser change moves any of these numbers, look at why before updating them.

Run with:  .venv/bin/python -m pytest tests -q
"""
import pytest

import edgar
import parse

# (ticker, filing_date, expected number of risks, expected number of categories)
EXPECTED = [
    ("AAPL", "2026-11-03", 28, 5),
    ("AAPL", "2025-10-31", 27, 5),
    ("MSFT", "2026-07-29", 26, 7),
    ("MSFT", "2025-07-30", 23, 7),
    ("TSLA", "2026-01-29", 40, 4),
    ("KO", "2026-02-20", 42, 7),
    ("NVDA", "2026-02-25", 24, 4),
    ("NVDA", "2025-02-26", 23, 4),  # 2026 added "Commercial arrangements..."
    ("AMZN", "2026-02-06", 24, None),
    ("GOOGL", "2026-02-05", 28, 5),
    ("WMT", "2026-03-13", 24, 4),
]


def _filing(ticker, date):
    return next(f for f in edgar.list_10ks(ticker) if f["filing_date"] == date)


@pytest.mark.parametrize("ticker,date,n_risks,n_cats", EXPECTED)
def test_risk_counts(ticker, date, n_risks, n_cats):
    risks = parse.risks_for_filing(_filing(ticker, date))
    real = [r for r in risks if r["title"] != "(Introduction)"]
    assert len(real) == n_risks
    if n_cats is not None:
        assert len({r["category"] for r in risks if r["category"]}) == n_cats
    # Every risk should have real body text, and no title should be page furniture.
    assert all(len(r["body"].split()) >= 40 for r in real)
    assert not any(r["title"].lower().startswith(("item 1a", "the following")) for r in real)


def test_jpm_has_older_10ks():
    # JPM files thousands of documents a year; older 10-Ks are on extra pages.
    assert len(edgar.list_10ks("JPM")) >= 10


def test_specific_titles_present():
    titles = [r["title"] for r in parse.risks_for_filing(_filing("MSFT", "2026-07-29"))]
    # Run-in heading whose closing period sits outside the bold span:
    assert any(t.startswith("If our reputation or our brands are damaged") for t in titles)
    # Label-style sub-headings must NOT be risks:
    assert not any(t.startswith("Competition laws and new market regulation") for t in titles)
