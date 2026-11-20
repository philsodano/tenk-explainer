"""Regression tests for the year-over-year diff (real filings, verified by hand)."""
import edgar
import parse
import diff


def _changes(ticker):
    f = edgar.list_10ks(ticker)
    return diff.compare(parse.risks_for_filing(f[1]), parse.risks_for_filing(f[0])), f


def test_counts_add_up():
    for t in ["AAPL", "MSFT", "KO"]:
        changes, f = _changes(t)
        s = diff.summarize(changes)
        n_new = len([r for r in parse.risks_for_filing(f[0]) if r["title"] != "(Introduction)"])
        n_old = len([r for r in parse.risks_for_filing(f[1]) if r["title"] != "(Introduction)"])
        assert s["new"] + s["modified"] + s["unchanged"] == n_new
        assert s["removed"] + s["modified"] + s["unchanged"] == n_old


def test_nvda_new_risk():
    changes, _ = _changes("NVDA")
    assert [c.title for c in changes if c.status == "new"] == [
        "Commercial arrangements expose us to counterparty risks."]


def test_msft_rewrites_and_splits():
    changes, _ = _changes("MSFT")
    rewritten = {c.old_title[:30] for c in changes if c.rewritten}
    assert "Our focus on cloud-based and A" in rewritten
    assert "We may have excessive outages," in rewritten
    split = next(c for c in changes if c.title == "We may experience other quality problems.")
    assert split.status == "new" and split.reused > 0.8  # split out of "supply or quality problems"


def test_tsla_genuine_changes_not_forced_into_rewrites():
    changes, _ = _changes("TSLA")
    s = diff.summarize(changes)
    assert s["new"] == 2 and s["removed"] == 2
    assert not any(c.rewritten for c in changes)


def test_sentence_splitter_handles_abbreviations():
    assert diff.split_sentences("The U.S. Department of Commerce acted. Next.") == [
        "The U.S. Department of Commerce acted.", "Next."]
    assert diff.split_sentences("If Mr. Musk sells shares, prices may fall. Next.") == [
        "If Mr. Musk sells shares, prices may fall.", "Next."]
    assert diff.split_sentences('See "Item 1. Business" of this report. Next.') == [
        'See "Item 1. Business" of this report.', "Next."]


def test_expanded_sentence_counts_as_reworded():
    old = "Supply chains can be highly concentrated, and an escalation of tensions could result in disruptions."
    new = ("Supply chains can be highly concentrated, and an escalation of tensions, conflict or "
           "instability in regions where the Company relies on single sources could result in disruptions.")
    added, removed, reworded = diff._sentence_diff(old, new)
    assert reworded and not added and not removed


def test_phrase_changes_find_known_shifts():
    f = edgar.list_10ks("TSLA")
    added, dropped = diff.phrase_changes(parse.risks_for_filing(f[1]), parse.risks_for_filing(f[0]))
    assert "ceo performance award" in [p for p, _ in added]
    assert "esg practices" in [p for p, _ in dropped]
