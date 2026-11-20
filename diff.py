"""
Step 3: Comparing risk factors between two years.

1. MATCH each risk in the new filing to one in the old filing. Companies
   often reword titles, so we score each pair two ways and take the higher:
     * title similarity  (difflib, word by word)
     * body overlap      (share of 5-word phrases the two bodies have in common)
   Then we pair up risks greedily, best score first, and ignore weak matches.
2. CLASSIFY: new (no match), removed (no match the other way),
   modified, or unchanged.
3. For modified risks, list the sentences that were added, removed, or reworded.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

MATCH_THRESHOLD = 0.5      # below this, two risks are considered unrelated
REWRITE_THRESHOLD = 0.6   # vocabulary similarity for "same topic, rewritten" (see below)
RESTRUCTURE_MIN = 0.25     # a new/removed risk reusing this much text was restructured
RELATED_MIN = 0.08         # another risk contributing this share of text is "related"
REWORD_THRESHOLD = 0.6     # sentence pairs this similar count as "reworded"
                           # (or >=75% of the shorter one survives inside the longer one)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9’']+", text.lower())


def _shingles(text: str, n: int = 5) -> set[tuple]:
    w = _words(text)
    return {tuple(w[i:i + n]) for i in range(max(len(w) - n + 1, 1))}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


STOPWORDS = set("""the a an and or of to in on for by with as is are be been being that this
these those its it their our we us from at which may could would can will such other any
including not have has had more than also into""".split())


def _topic_vector(text: str) -> Counter:
    return Counter(w for w in _words(text) if w not in STOPWORDS and len(w) > 2)


def _cosine(a: Counter, b: Counter) -> float:
    dot = sum(a[k] * b[k] for k in a if k in b)
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def _containment(a: str, b: str) -> float:
    """Share of the SHORTER sentence's words that appear, in order, in the longer one.
    Catches sentences that were expanded or trimmed, which plain similarity misses."""
    wa, wb = _words(a), _words(b)
    if min(len(wa), len(wb)) < 6:
        return 0.0
    m = SequenceMatcher(None, wa, wb, autojunk=False)
    return sum(blk.size for blk in m.get_matching_blocks()) / min(len(wa), len(wb))


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _words(a), _words(b), autojunk=False).ratio()


# A period after these doesn't end a sentence: "the U.S. Department", "Mr. Musk",
# "Item 1. Business", "Inc. and", "e.g. the".
ABBREVIATION = re.compile(
    r"(\b(?:[A-Z]\.){2,}|\b[A-Z]\.|\b(?:Mr|Mrs|Ms|Dr|St|Inc|Co|Corp|Ltd|No|Nos|vs|etc|Jr|Sr"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Item|Items|Note|Notes|Sec|e\.g|i\.e)\."
    r"|\bItems? \d+[A-C]?\.)[”\"’)]*$"
)


def split_sentences(text: str) -> list[str]:
    # Split after . ! ? or ; followed by a space and a capital/quote/bullet...
    parts = re.split(r"(?<=[.!?;])\s+(?=[A-Z“\"•(])", text)
    # ...then glue back pieces that were split right after an abbreviation.
    out = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if out and ABBREVIATION.search(out[-1]):
            out[-1] += " " + part
        else:
            out.append(part)
    return out


@dataclass
class RiskChange:
    status: str                  # "new", "removed", "modified", "unchanged"
    title: str                   # title in the newer filing (or older, if removed)
    category: str | None
    old_title: str | None = None
    similarity: float = 1.0      # body similarity, 0..1
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    reworded: list[tuple[str, str]] = field(default_factory=list)  # (old, new)
    body: str = ""               # full body text (newer version)
    # For new/removed risks: how much text was carried over from (or into) other
    # risks, and which ones. High reuse means the company restructured its
    # risk factors (split or merged risks) rather than adding brand-new language.
    reused: float = 0.0
    rewritten: bool = False      # matched by topic only: same subject, new wording
    related: list[str] = field(default_factory=list)


def _sentence_diff(old_body: str, new_body: str):
    old_s, new_s = split_sentences(old_body), split_sentences(new_body)
    norm = lambda s: " ".join(_words(s))
    old_set, new_set = {norm(s) for s in old_s}, {norm(s) for s in new_s}
    only_new = [s for s in new_s if norm(s) not in old_set]
    only_old = [s for s in old_s if norm(s) not in new_set]

    # Pair up near-identical leftovers as "reworded" instead of add+remove.
    reworded, used_old = [], set()
    added = []
    for s in only_new:
        best, best_j = 0.0, None
        for j, o in enumerate(only_old):
            if j in used_old:
                continue
            r = max(_ratio(s, o), _containment(s, o) - 0.15)
            if r > best:
                best, best_j = r, j
        if best_j is not None and best >= REWORD_THRESHOLD:
            used_old.add(best_j)
            reworded.append((only_old[best_j], s))
        else:
            added.append(s)
    removed = [o for j, o in enumerate(only_old) if j not in used_old]
    return added, removed, reworded


def compare(old_risks: list[dict], new_risks: list[dict]) -> list[RiskChange]:
    """Compare two lists of risks from parse.group_risks()."""
    old = [r for r in old_risks if r["title"] != "(Introduction)"]
    new = [r for r in new_risks if r["title"] != "(Introduction)"]
    old_sh = [_shingles(r["body"]) for r in old]
    new_sh = [_shingles(r["body"]) for r in new]

    # Score every (new, old) pair.
    pairs = []
    for i, n in enumerate(new):
        for j, o in enumerate(old):
            title_sim = _ratio(n["title"], o["title"])
            body_sim = _jaccard(new_sh[i], old_sh[j])
            # Body overlap is a strong signal even at modest values, so scale it up.
            score = max(title_sim, min(1.0, body_sim * 1.5))
            if score >= MATCH_THRESHOLD:
                pairs.append((score, i, j, body_sim))

    # Greedy matching: best pairs first, each risk used at most once.
    pairs.sort(reverse=True)
    match_new, match_old = {}, set()
    for score, i, j, body_sim in pairs:
        if i not in match_new and j not in match_old:
            match_new[i] = (j, body_sim)
            match_old.add(j)

    # Second pass: pair leftover risks that cover the same TOPIC in new words.
    # Rewritten text keeps its vocabulary even when phrasing changes. Measured on
    # real filings: true rewrites score 0.64-0.94; different risks in the same
    # filing score ~0.20 median, ~0.40 at the 95th percentile.
    rewritten = set()
    left_new = [i for i in range(len(new)) if i not in match_new]
    left_old = [j for j in range(len(old)) if j not in match_old]
    topic_pairs = sorted(
        ((_cosine(_topic_vector(new[i]["title"] + " " + new[i]["body"]),
                  _topic_vector(old[j]["title"] + " " + old[j]["body"])), i, j)
         for i in left_new for j in left_old),
        reverse=True,
    )
    for score, i, j in topic_pairs:
        if score < REWRITE_THRESHOLD:
            break
        if i not in match_new and j not in match_old:
            match_new[i] = (j, _jaccard(new_sh[i], old_sh[j]))
            match_old.add(j)
            rewritten.add(i)

    changes = []
    for i, n in enumerate(new):
        if i not in match_new:
            changes.append(RiskChange("new", n["title"], n["category"], body=n["body"],
                                      similarity=0.0, added=split_sentences(n["body"])))
            continue
        j, body_sim = match_new[i]
        o = old[j]
        added, removed, reworded = _sentence_diff(o["body"], n["body"])
        title_changed = " ".join(_words(n["title"])) != " ".join(_words(o["title"]))
        status = "modified" if (added or removed or reworded or title_changed) else "unchanged"
        changes.append(RiskChange(
            status, n["title"], n["category"],
            old_title=o["title"] if title_changed else None,
            similarity=body_sim, added=added, removed=removed, reworded=reworded, body=n["body"],
            rewritten=i in rewritten,
        ))
    for j, o in enumerate(old):
        if j not in match_old:
            changes.append(RiskChange("removed", o["title"], o["category"], body=o["body"],
                                      similarity=0.0, removed=split_sentences(o["body"])))
    _annotate_reuse(changes, old, new)
    return changes


def _annotate_reuse(changes, old, new):
    """For new and removed risks, measure how much of their text appears in
    the other year's risks, and name the risks it came from / went to."""
    old_sh = {r["title"]: _shingles(r["body"]) for r in old}
    new_sh = {r["title"]: _shingles(r["body"]) for r in new}
    for c in changes:
        if c.status not in ("new", "removed"):
            continue
        mine = _shingles(c.body)
        others = old_sh if c.status == "new" else new_sh
        overlap = {t: len(mine & sh) / len(mine) for t, sh in others.items()}
        all_other = set().union(*others.values()) if others else set()
        c.reused = len(mine & all_other) / len(mine) if mine else 0.0
        c.related = [t for t, v in sorted(overlap.items(), key=lambda kv: -kv[1]) if v >= RELATED_MIN][:3]


def summarize(changes: list[RiskChange]) -> dict:
    counts = {"new": 0, "removed": 0, "modified": 0, "unchanged": 0}
    for c in changes:
        counts[c.status] += 1
    return counts


if __name__ == "__main__":
    import sys
    import edgar
    import parse

    ticker = sys.argv[1] if len(sys.argv) > 1 else "MSFT"
    filings = edgar.list_10ks(ticker)
    new_f, old_f = filings[0], filings[1]
    changes = compare(parse.risks_for_filing(old_f), parse.risks_for_filing(new_f))
    print(f"{ticker}: {old_f['filing_date']} -> {new_f['filing_date']}: {summarize(changes)}")
    for c in changes:
        if c.status in ("new", "removed"):
            note = ""
            if c.reused >= RESTRUCTURE_MIN and c.related:
                verb = "restructured from" if c.status == "new" else "text moved into"
                note = f"   ({c.reused:.0%} reused; {verb}: {c.related[0][:50]})"
            print(f"  [{c.status.upper()}] {c.title[:80]}{note}")
    for c in [c for c in changes if c.rewritten]:
        print(f"  [REWRITTEN] {c.title[:60]}  <-  was: {c.old_title[:50]}")
    for c in sorted((c for c in changes if c.status == "modified" and not c.rewritten),
                    key=lambda c: -(len(c.added) + len(c.removed) + len(c.reworded)))[:5]:
        print(f"  [{'REWRITTEN' if c.rewritten else 'MODIFIED'} +{len(c.added)} -{len(c.removed)} ~{len(c.reworded)}] {c.title[:90]}")


# ---------- free, rule-based highlights (no AI needed) ----------

PHRASE_STOP = STOPWORDS | set("""our company company’s its their such these those other any each
were was has had have may might could would should can will not also more less than which
who whom whose what when where while within without upon including include includes related
certain various result results significant significantly adversely adverse affect affected
materially material business operations""".split())


def _phrases(text: str, n_values=(2, 3)) -> Counter:
    """Count 2- and 3-word phrases made of meaningful words (no stopwords at
    either end, no numbers), e.g. "agentic systems", "tariff refunds"."""
    words = _words(text)
    out = Counter()
    for n in n_values:
        for i in range(len(words) - n + 1):
            gram = words[i:i + n]
            if gram[0] in PHRASE_STOP or gram[-1] in PHRASE_STOP:
                continue
            if any(len(w) < 3 or not w.isalpha() for w in (gram[0], gram[-1])):
                continue
            out[" ".join(gram)] += 1
    return out


def phrase_changes(old_risks: list[dict], new_risks: list[dict], top: int = 12):
    """Phrases used at least twice this year but never last year (and vice versa).
    New vocabulary is often where the real news is: a new regulation, product,
    or threat the company started worrying about."""
    old_text = " ".join(r["title"] + " " + r["body"] for r in old_risks)
    new_text = " ".join(r["title"] + " " + r["body"] for r in new_risks)
    old_p, new_p = _phrases(old_text), _phrases(new_text)

    # Words used constantly in this section (the company's name, "risk", ...)
    # make phrases that sound new but aren't informative: skip them.
    word_counts = Counter(_words(old_text + " " + new_text))
    common = {w for w, c in word_counts.items() if c > 150}

    def norm(phrase):  # "data centers" and "data center" count as the same phrase
        return {w[:-1] if w.endswith("s") and len(w) > 3 else w for w in phrase.split()}

    def internal_stop(phrase):
        return sum(w in PHRASE_STOP for w in phrase.split()[1:-1])

    def distinct(counter, other):
        picked = []
        # Most frequent first; among ties, prefer phrases without filler words
        # in the middle ("super intelligence" over "intelligence and super"),
        # then longer ones ("ceo performance award" over "ceo performance").
        ordered = sorted(counter.items(),
                         key=lambda kv: (-kv[1], internal_stop(kv[0]), -len(kv[0].split())))
        for phrase, count in ordered:
            if count < 2 or phrase in other:
                continue
            words = phrase.split()
            if any(w in common for w in words):
                continue
            # Skip fragments overlapping a phrase already picked.
            if any(len(norm(phrase) & norm(p)) >= min(2, len(words)) or
                   (internal_stop(phrase) and norm(phrase) & norm(p))
                   for p, _ in picked):
                continue
            picked.append((phrase, count))
            if len(picked) >= top:
                break
        return picked

    return distinct(new_p, old_p), distinct(old_p, new_p)
