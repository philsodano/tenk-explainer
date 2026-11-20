"""Tests for the free "search the filing" Q&A.

Ground truth for each question was found by grepping the filing independently.
TUNED questions were used while developing the ranking; HELDOUT questions were
written afterwards and scored once without further tuning (7/8 at the time).
"""
import re

import pytest

import ask
import edgar
import parse
from parse import Paragraph

TUNED = [
    ("AAPL", "How much did Apple spend on buybacks?", r"repurchased[^.]*\$85\.8 billion"),
    ("AAPL", "How many employees does the company have?", r"167,000"),
    ("AAPL", "What was total net sales?", r"478,834|\$478\.8 billion"),
    ("MSFT", "What does Microsoft say about OpenAI?", r"OpenAI"),
    ("TSLA", "What lawsuits is Tesla involved in?", r"(lawsuit|litigation|class action|derivative action)"),
    ("JPM", "What was net interest income?", r"\$95\.4 billion"),
    ("KO", "How many employees does Coca-Cola have?", r"65,900"),
    ("NVDA", "What does Nvidia say about export controls to China?", r"export control"),
    ("WMT", "How much did Walmart pay in dividends?", r"dividend[^.]*\$\s?\d"),
    ("AMZN", "What were capital expenditures?", r"capital expenditures were \$"),
    ("GOOGL", "How many employees does Alphabet have?", r"employees"),
    ("KO", "How much revenue did Coca-Cola make?", r"net operating revenues[^.]*\$\s?\d|\$\s?47[,.]\d"),
]
HELDOUT = [
    ("MSFT", "How many people does Microsoft employ?", r"employ\w*[^.]{0,30}approximately [\d,]+ (people|full-time)"),
    ("TSLA", "How many vehicles did Tesla deliver?", r"delivered approximately [\d.]+ million"),
    ("NVDA", "What was Data Center revenue?", r"Data Center revenue[^.]{0,60}(\$|\d+%)|Revenue from Data Center[^.]*\d+%"),
    ("JPM", "What was the CET1 capital ratio?", r"CET1[^.]{0,80}\d+\.\d\s?%"),
    ("WMT", "How many stores does Walmart operate?", r"[\d,]{4,} (retail )?(units|stores)"),
    ("GOOGL", "How much did Alphabet spend on research and development?", r"research and development[^.]{0,100}\$\s?[\d.,]+"),
    ("KO", "How many bottling partners does Coca-Cola have?", r"five largest independent bottling partners|these five bottling partners"),
    # Known miss, kept visible on purpose: "where" questions want place names.
    pytest.param("AAPL", "Where are Apple products manufactured?", r"manufactur[^.]{0,200}(China|Asia|India|Vietnam)",
                 marks=pytest.mark.xfail(reason="ranking doesn't favor place names", strict=False)),
]

_cache = {}


def _search(ticker, question):
    f = edgar.list_10ks(ticker)[0]
    if ticker not in _cache:
        _cache[ticker] = ask.chunk_filing(parse.html_to_paragraphs(edgar.download_filing(f)))
    return ask.ask(question, _cache[ticker], max_passages=3, company=f["company"]).passages


@pytest.mark.parametrize("ticker,question,answer", TUNED + HELDOUT)
def test_answer_in_top_3(ticker, question, answer):
    passages = _search(ticker, question)
    assert any(re.search(answer, p.sentence, re.I) for p in passages), [p.sentence[:120] for p in passages]


def test_stemmer_is_consistent():
    for a, b in [("employees", "employee"), ("repurchased", "repurchase"), ("dividends", "dividend"),
                 ("expenditures", "expenditure"), ("sales", "sale")]:
        assert ask._stem(a) == ask._stem(b)


def test_synonyms_fill_vocabulary_gaps():
    # "buybacks" never appears in filings, so its synonyms get full weight.
    q = ask.expand_query("buybacks", vocabulary={"repurchas"})
    assert q[ask._stem("repurchase")] == 1.0
    # "employees" does appear, so synonyms are just extras.
    q = ask.expand_query("employees", vocabulary={"employe", "workforc"})
    assert q["workforc"] < 1.0


def test_chunking_tracks_sections():
    paras = [Paragraph("Item 5. Market for Registrant's Common Equity", True),
             Paragraph("The Company repurchased stock. " * 80, False),
             Paragraph("Item 7. Management's Discussion and Analysis", True),
             Paragraph("Net sales increased. " * 80, False)]
    sections = [c.section for c in ask.chunk_filing(paras)]
    assert sections[0].startswith("Item 5.") and sections[-1].startswith("Item 7.")
