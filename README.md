# 10-K Explainer

Pick a public company and see **what changed in its risk factors** since last year,
browse its risks, and **ask questions** about its annual report. Answers are the filing's
own sentences, word for word, labeled with the section they come from.

**Completely free.** Data comes from SEC EDGAR's free API, and everything else runs
locally with open-source libraries. No API keys, no accounts, no AI service.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export SEC_USER_AGENT="Your Name your@email.com"   # the SEC requires contact info
.venv/bin/streamlit run app.py
```

Run the tests (they download real filings, so the first run takes a couple of minutes):

```bash
.venv/bin/python -m pytest tests -q
```

## How it works

| File | What it does |
|---|---|
| `edgar.py` | Ticker → SEC company ID (CIK) → list of 10-K filings → download (cached in `cache/`). Reads the SEC's extra filing-history pages, which big filers like banks need. |
| `parse.py` | Turns filing HTML into clean paragraphs, finds the real "Item 1A. Risk Factors" section (not the table of contents), and groups it into categories and individual risks. |
| `diff.py` | Matches each risk to last year's version, classifies it as new / removed / modified / rewritten / unchanged, and diffs sentences. Flags risks that were split or merged. |
| `ask.py` | "Ask a question": splits the filing into labeled chunks, expands your question with finance synonyms ("buybacks" → "repurchase"), and ranks every sentence with BM25 keyword search. Returns the best sentences with context. |
| `app.py` | The Streamlit web app. |

## Things the parser handles (each one was a real bug found on a real filing)

- The table of contents and cross-references also say "Item 1A". The longest span wins.
- Words split across HTML tags ("RIS" + "K FACTORS").
- Running page headers and footers ("Apple Inc. | 2026 Form 10-K | 19", "Alphabet Inc.").
- Risk titles in the same paragraph as their body (Microsoft), with the period outside the bold.
- Title Case titles with no period (Amazon) vs. italic sub-headings inside a risk (Microsoft).
  The parser learns each filing's title style.
- Summary sections and italic intro sentences that look like headings (Nvidia, JPMorgan).
- Abbreviations that look like sentence ends ("U.S.", "Mr.", "Item 1.").

## How the diff decides what changed

1. Pair risks whose **titles** are similar or whose **bodies share 5-word phrases**.
2. Pair leftovers that cover the **same topic in new words** (vocabulary similarity ≥ 0.6;
   on real filings, true rewrites scored 0.64–0.94 and unrelated risks ~0.2).
3. For "new" and "removed" risks, report how much text was **reused** elsewhere. A "new"
   risk built from 87% old text was split out of an existing one, not freshly written.
4. **Highlights** list brand-new and dropped risks, the most-edited ones, and **phrases that
   appeared or disappeared** (used 2+ times in one year, never in the other). For example,
   Tesla added "ceo performance award" and "robotaxi" and dropped "esg practices" and
   "gigafactory new york". Coca-Cola dropped "diverse perspectives".

## How good is "Ask a question"?

It finds sentences by keywords. It doesn't *understand* the question the way an AI would.
Measured with questions whose answers I located in the filings by hand:

- 12 questions used while tuning: correct sentence in the top 3 for **12/12**
- 8 new questions written afterwards and scored once: **7/8**, all 7 ranked first.
  The miss ("Where are Apple products manufactured?") returned on-topic sentences, but not
  the one naming the countries.

Both sets live in `tests/test_ask.py`, so any change that makes search worse fails the tests.

## Limits

- Tested on AAPL, MSFT, JPM, TSLA, KO, NVDA, AMZN, GOOGL and WMT. Other filers may format things
  in ways it hasn't seen. Always check important details against the original filing (linked in the app).
- 10-K only. Foreign companies file 20-F, which isn't supported.
- Search uses keywords, so try the filing's vocabulary if a question misses
  ("repurchase" not "buyback", "net sales" not "revenue"). Common synonyms are built in.
- Figures inside tables come out as long runs of numbers. Open the filing for the full table.

## Free hosting

To put it online for free, push this folder to a public GitHub repo and deploy it on
Streamlit Community Cloud (share.streamlit.io). Set `SEC_USER_AGENT` in the app's secrets settings.
