"""
Step 1: Fetching 10-K filings from SEC EDGAR.

EDGAR is the SEC's free database of company filings. We use three things:
  1. A ticker -> CIK lookup file. (CIK = the SEC's ID number for a company.)
  2. The "submissions" API, which lists every filing a company has made.
  3. The filing archive, where the actual 10-K documents live.

The SEC asks every program to send a User-Agent header that says who you are
and gives a contact email. Set yours with an environment variable:
    export SEC_USER_AGENT="Your Name your@email.com"
"""

import os
import time
from pathlib import Path

from functools import lru_cache

import requests

USER_AGENT = os.environ.get("SEC_USER_AGENT", "10k-explainer contact@example.com")
HEADERS = {"User-Agent": USER_AGENT}

# We save downloaded files here so we don't download the same thing twice.
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)


def _get(url: str) -> requests.Response:
    """Download a URL. The SEC allows at most 10 requests per second,
    so we pause briefly before each request to stay well under that."""
    time.sleep(0.15)
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()  # turns HTTP errors (404 etc.) into Python errors
    return response


@lru_cache(maxsize=1)
def _ticker_table() -> dict:
    return _get("https://www.sec.gov/files/company_tickers.json").json()


def get_cik(ticker: str) -> str:
    """Turn a ticker like 'AAPL' into a 10-digit CIK like '0000320193'."""
    data = _ticker_table()
    # The file looks like: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    for company in data.values():
        if company["ticker"].upper() == ticker.upper():
            return str(company["cik_str"]).zfill(10)  # pad with zeros to 10 digits
    raise ValueError(f"Ticker not found: {ticker}")


@lru_cache(maxsize=64)
def list_10ks(ticker: str) -> list[dict]:
    """Return the company's 10-K filings, newest first."""
    cik = get_cik(ticker)
    data = _get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()

    # "recent" holds parallel lists: form[i], filingDate[i], etc. all describe filing i.
    # It only covers the latest ~1,000 filings (or one year). Big filers like banks
    # submit thousands of documents a year, so older 10-Ks live in extra "files"
    # pages with the same structure. We read those too.
    pages = [data["filings"]["recent"]]
    for extra in data["filings"].get("files", []):
        pages.append(_get(f"https://data.sec.gov/submissions/{extra['name']}").json())

    filings = []
    for page in pages:
        for i, form in enumerate(page["form"]):
            if form == "10-K":
                accession = page["accessionNumber"][i]  # e.g. "0000320193-24-000123"
                doc = page["primaryDocument"][i]        # e.g. "aapl-20240928.htm"
                if not doc:
                    continue  # very old filings sometimes lack a primary document
                filings.append({
                    "company": data["name"],
                    "filing_date": page["filingDate"][i],
                    "period": page["reportDate"][i],
                    "url": (
                        "https://www.sec.gov/Archives/edgar/data/"
                        f"{int(cik)}/{accession.replace('-', '')}/{doc}"
                    ),
                })
    filings.sort(key=lambda f: f["filing_date"], reverse=True)
    return filings


def download_filing(filing: dict) -> str:
    """Download a filing's HTML (or read it from the cache) and return it."""
    cache_file = CACHE_DIR / filing["url"].split("/data/")[1].replace("/", "_")
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    html = _get(filing["url"]).text
    cache_file.write_text(html, encoding="utf-8")
    return html


# This block only runs when you run the file directly: python edgar.py
if __name__ == "__main__":
    filings = list_10ks("AAPL")
    print(f"Found {len(filings)} 10-Ks for {filings[0]['company']}:")
    for f in filings[:5]:
        print(f"  {f['filing_date']}  (fiscal year ending {f['period']})")

    html = download_filing(filings[0])
    print(f"\nDownloaded the latest one: {len(html):,} characters of HTML")
