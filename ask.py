"""
Step 4: Answering questions about a 10-K, for free.

No AI service and no API key. Instead of generating an answer, we FIND the
filing's own sentences that answer the question, so every word you see is
quoted straight from the 10-K and easy to verify:

  1. Split the filing into ~350-word chunks, each labeled with its section
     ("Item 7. Management's Discussion and Analysis", etc.).
  2. Expand the question with finance synonyms ("buybacks" also searches
     "repurchase"), because filings use formal wording.
  3. Rank chunks with BM25, a classic keyword-search formula, then rank the
     individual sentences inside the best chunks the same way.
  4. Return the top sentences with a sentence of context on each side.
     Questions like "how much..." prefer sentences that contain numbers.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

from parse import Paragraph

CHUNK_WORDS = 350
TOP_K = 20  # chunks to search for answer sentences

SECTION_HEADING = re.compile(r"^item\s*(\d{1,2}[a-c]?)\b[.:\s]*(.{0,90})", re.I)
# Some filers (banks especially) put the annual report after Item 15 with its own
# headings, so we also recognize these standard section names.
KNOWN_SECTION = re.compile(
    r"^(management['’]s discussion and analysis|notes to (the )?consolidated financial statements"
    r"|consolidated (balance sheets?|statements? of [a-z ,'’()]+)"
    r"|report of independent registered public accounting firm|glossary of terms)",
    re.I,
)
TOKEN = re.compile(r"[a-z0-9]+")
STOP = set("""the a an and or of to in on for by with as is are was were be been being that this
these those its it their our we us from at which may could would can will such other any what
how does do did not have has had more than also into about there much many what which when
where who why say says said tell company companys""".split())


@dataclass
class Chunk:
    section: str
    text: str


def chunk_filing(paragraphs: list[Paragraph]) -> list[Chunk]:
    """Group paragraphs into ~CHUNK_WORDS-word chunks, tracking the current Item."""
    chunks, section, buf, count = [], "Cover / Table of Contents", [], 0

    def flush():
        nonlocal buf, count
        if buf:
            chunks.append(Chunk(section, " ".join(buf)))
        buf, count = [], 0

    for p in paragraphs:
        m = SECTION_HEADING.match(p.text)
        # A real section heading is short (the TOC repeats them too, which is harmless).
        if m and len(p.text) < 120:
            flush()
            section = f"Item {m.group(1).upper()}. {m.group(2).strip(' .:')}".strip()
            continue
        if p.is_heading and len(p.text) < 90 and KNOWN_SECTION.match(p.text):
            label = p.text[0].upper() + p.text[1:]
            if label != section:
                flush()
                section = label
            continue  # these repeat as page headers; don't add them as text
        buf.append(p.text)
        count += len(p.text.split())
        if count >= CHUNK_WORDS:
            flush()
    flush()
    return chunks


def _stem(word: str) -> str:
    """Crude suffix trimming so "dividends"/"dividend" and "repurchased"/"repurchase"
    match. Not linguistically perfect; it only needs to be consistent."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and not word.endswith("ss") and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    # Only when no suffix was removed: "repurchase" -> "repurchas" (matches "repurchased"),
    # "employee" -> "employe" (matches "employees" -> "employe").
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in TOKEN.findall(text.lower()) if t not in STOP]


class BM25:
    """Rank chunks by keyword relevance. k1/b are the standard defaults."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(_tokens(d)) for d in docs]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg = sum(self.lens) / max(len(self.lens), 1)
        df = Counter(term for tf in self.tfs for term in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def scores(self, query) -> list[float]:
        """`query` is a string, or a {term: weight} dict to weight terms differently."""
        q = Counter(_tokens(query)) if isinstance(query, str) else query
        out = []
        for tf, length in zip(self.tfs, self.lens):
            s = 0.0
            for t, weight in q.items():
                if t in tf:
                    f = tf[t]
                    s += weight * self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / self.avg))
            out.append(s)
        return out


def retrieve(chunks: list[Chunk], question, k: int = TOP_K) -> list[Chunk]:
    scores = BM25([c.section + " " + c.text for c in chunks]).scores(question)
    best = sorted(range(len(chunks)), key=lambda i: -scores[i])[:k]
    best = [i for i in best if scores[i] > 0]
    return [chunks[i] for i in sorted(best)]  # keep document order for readability


# Filings use formal wording; people don't. Each key also searches its synonyms.
SYNONYMS = {
    "buyback": "repurchase repurchased repurchases",
    "buybacks": "repurchase repurchased repurchases",
    "revenue": "net sales revenues",
    "revenues": "net sales revenue",
    "sales": "revenue revenues",
    "profit": "net income earnings",
    "profits": "net income earnings",
    "earnings": "net income",
    "employees": "employee headcount workforce",
    "staff": "employees headcount workforce",
    "workers": "employees headcount workforce",
    "lawsuit": "litigation legal proceedings",
    "lawsuits": "litigation legal proceedings",
    "sued": "litigation legal proceedings",
    "debt": "borrowings notes indebtedness term debt",
    "ceo": "chief executive officer",
    "cfo": "chief financial officer",
    "ai": "artificial intelligence",
    "dividend": "dividends",
    "pay": "paid payments",
    "paid": "pay payments",
    "cash": "cash equivalents",
    "spend": "expenses expenditures",
    "spending": "expenses expenditures",
    "capex": "capital expenditures property equipment",
    "china": "prc greater china",
    "tariffs": "tariff trade",
    "competitors": "competition competitive",
    "hack": "cybersecurity cyberattacks breach",
    "hacks": "cybersecurity cyberattacks breach",
}

NUMBER = re.compile(r"\$\s?\d|\d+(\.\d+)?\s?%|\b\d{1,3}(,\d{3})+\b|\b\d+(\.\d+)?\s(million|billion|thousand)\b", re.I)
WANTS_NUMBER = re.compile(r"^\s*(how (much|many|big|large)|what (was|were|is|are)\b|what percent)", re.I)


def expand_query(question: str, company: str = "", vocabulary: set | None = None) -> dict:
    """Turn a question into weighted search terms.

    * words you typed: 1.0
    * finance synonyms: they fill vocabulary gaps. If the word you typed never
      appears in the filing ("buybacks"), its synonyms ("repurchase") get full
      weight. If it does appear ("employees"), synonyms are extras: weight 0.3.
    * the company's own name: 0.2 (it's all over its own filing, so it says
      little about which passage is relevant)
    """
    weights = {}
    for t in _tokens(question):
        weights[t] = max(weights.get(t, 0), 1.0)
    for word in TOKEN.findall(question.lower()):
        if word not in SYNONYMS:
            continue
        in_filing = vocabulary is None or any(t in vocabulary for t in _tokens(word))
        syn_weight = 0.3 if in_filing else 1.0
        for t in _tokens(SYNONYMS[word]):
            weights[t] = max(weights.get(t, 0), syn_weight)
    for t in _tokens(company):
        if t in weights:
            weights[t] = 0.2
    return weights


@dataclass
class Passage:
    section: str
    sentence: str        # the matching sentence
    before: str          # previous sentence (context), may be ""
    after: str           # next sentence (context), may be ""
    score: float


@dataclass
class Answer:
    passages: list[Passage]
    query_terms: list[str]     # terms to highlight (question + synonyms)
    chunks_searched: int


def ask(question: str, chunks: list[Chunk], max_passages: int = 5, company: str = "") -> Answer:
    """Find the sentences in the filing that best answer the question."""
    from diff import split_sentences  # abbreviation-aware splitter ("U.S.", "Mr.")

    chunk_bm25 = BM25([c.section + " " + c.text for c in chunks])
    query = expand_query(question, company, vocabulary=set(chunk_bm25.idf))
    # Words to highlight in the results: what was typed plus the synonyms.
    typed = set(TOKEN.findall(question.lower())) - STOP
    syns = {w for t in typed for w in TOKEN.findall(SYNONYMS.get(t, ""))}
    terms = sorted(typed | syns, key=len, reverse=True)

    # Score every chunk (for topic), then every sentence in the filing.
    chunk_scores = chunk_bm25.scores(query)
    best_chunk = max(chunk_scores) if chunk_scores else 0
    if best_chunk <= 0:
        return Answer([], terms, 0)

    candidates = []  # (section, sentences, index, chunk score 0..1)
    for c, cs in zip(chunks, chunk_scores):
        sents = split_sentences(c.text)
        for i in range(len(sents)):
            candidates.append((c.section, sents, i, cs / best_chunk))

    bm25 = BM25([s[i] for _, s, i, _ in candidates])
    scores = bm25.scores(query)
    wants_number = bool(WANTS_NUMBER.search(question))
    ranked = []
    for (section, sents, i, chunk_rel), score in zip(candidates, scores):
        if score <= 0:
            continue
        sentence = sents[i]
        # A sentence in a highly relevant passage is more likely on-topic.
        score *= 1 + 0.5 * chunk_rel
        # Very short or very long "sentences" are usually table fragments or run-on lists.
        n_words = len(sentence.split())
        if n_words < 6:
            score *= 0.5
        elif n_words > 120:
            score *= 0.7
        if wants_number:
            score *= 1.4 if NUMBER.search(sentence) else 0.8
        ranked.append((score, section, sents, i))
    ranked.sort(key=lambda r: -r[0])

    passages, used = [], set()
    for score, section, sents, i in ranked:
        key = " ".join(sents[i].lower().split())
        # Skip duplicates and neighbors of a sentence we already show.
        if key in used or any(id(sents) == u[0] and abs(i - u[1]) <= 1 for u in used if isinstance(u, tuple)):
            continue
        used.add(key)
        used.add((id(sents), i))
        passages.append(Passage(
            section=section,
            sentence=sents[i],
            before=sents[i - 1] if i > 0 else "",
            after=sents[i + 1] if i + 1 < len(sents) else "",
            score=round(score, 2),
        ))
        if len(passages) >= max_passages:
            break
    return Answer(passages, terms, sum(1 for x in chunk_scores if x > 0))


if __name__ == "__main__":
    import sys
    import edgar
    import parse

    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    question = sys.argv[2] if len(sys.argv) > 2 else "How much did the company spend on buybacks?"
    filing = edgar.list_10ks(ticker)[0]
    chunks = chunk_filing(parse.html_to_paragraphs(edgar.download_filing(filing)))
    ans = ask(question, chunks, company=filing["company"])
    print(f"Q: {question}\n")
    for p in ans.passages:
        print(f"[{p.section}]  (score {p.score})\n  {p.sentence}\n")
