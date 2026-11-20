"""
Step 2: Turning raw 10-K HTML into clean paragraphs, and extracting
"Item 1A. Risk Factors".

Why this is harder than it sounds:
  * "Item 1A" appears several times: in the table of contents, in
    cross-references ("see Item 1A..."), and once as the real heading.
  * Some filings split words across HTML tags ("RIS" + "K FACTORS"), so we
    rebuild text block by block instead of using one big get_text().
  * Pages carry running headers and page numbers that we strip out.

Our strategy: find every line that LOOKS like an Item 1A heading, measure how
far it is to the next "Item 1B / 1C / 2" heading, and keep the longest span.
The table of contents entry is only a few lines long, so the real section wins.
"""

import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BLOCK_TAGS = {"p", "div", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table"}

# A line that starts a section heading. Allows "Item 1A.", "ITEM 1A:", "Item 1A —".
ITEM_1A = re.compile(r"^item\s*1a\b", re.I)
NEXT_ITEM = re.compile(r"^item\s*(1b|1c|2)\b", re.I)

# Lines that are page furniture rather than content.
JUNK_LINE = re.compile(
    r"^(\d{1,3}|page \d+|parts? i+( and i+)?|table of contents"
    r"|.{0,60}\|\s*\d{4} form 10-k\s*\|\s*\d+)$",
    re.I,
)


@dataclass
class Paragraph:
    text: str
    is_heading: bool  # True when the whole paragraph is bold/italic (a risk title)
    style: str = ""   # emphasis style of a heading, e.g. "b", "i", "bi", "u"


def _emphasis(el) -> str:
    """Return the emphasis on this text: "" (none) or letters b/i/u for
    bold / italic / underline, collected from the element and its inline parents."""
    flags = set()
    while el is not None and getattr(el, "name", None):
        flags |= {"b"} if el.name in ("b", "strong") else set()
        flags |= {"i"} if el.name in ("i", "em") else set()
        flags |= {"u"} if el.name == "u" else set()
        style = (el.get("style") or "").lower().replace(" ", "")
        if re.search(r"font-weight:(bold|[6-9]00)", style):
            flags.add("b")
        if "font-style:italic" in style:
            flags.add("i")
        if "text-decoration:underline" in style:
            flags.add("u")
        if el.name in BLOCK_TAGS:
            break
        el = el.parent
    return "".join(sorted(flags))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def html_to_paragraphs(html: str) -> list[Paragraph]:
    """Convert filing HTML into an ordered list of clean paragraphs."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    # Inline XBRL filings hide a big block of machine-readable data; drop it.
    for tag in soup.find_all(style=re.compile(r"display:\s*none", re.I)):
        tag.decompose()

    paragraphs = []
    for block in soup.find_all(BLOCK_TAGS):
        # Only "leaf" blocks: skip a <div> that contains other blocks,
        # because its children will be visited on their own.
        if block.find(BLOCK_TAGS):
            continue
        pieces, emphasized_chars, total_chars = [], 0, 0
        lead, lead_open = [], True  # the leading run of bold/italic text
        styles = []
        for s in block.find_all(string=True):
            piece = str(s)
            pieces.append(piece)
            n = len(piece.strip())
            total_chars += n
            emph = _emphasis(s.parent) if n else ""
            emphasized = bool(emph)
            if emphasized:
                emphasized_chars += n
                styles.append(emph)
            if lead_open:
                if emphasized or n == 0:
                    lead.append(piece)
                else:
                    lead_open = False
        text = _clean("".join(pieces))
        if not text or JUNK_LINE.match(text):
            continue
        is_heading = total_chars > 0 and emphasized_chars / total_chars > 0.9 and len(text) < 600
        lead_text = _clean("".join(lead))
        rest = text[len(lead_text):].strip() if text.startswith(lead_text) else None
        if not is_heading and rest and len(lead_text) >= 30:
            # "Run-in" heading: bold title and normal body share one paragraph.
            # Some filers put the closing period outside the bold span.
            if rest[0] in ".:" and not lead_text.endswith((".", ":")):
                lead_text, rest = lead_text + rest[0], rest[1:].strip()
            if lead_text.endswith((".", ":")):
                paragraphs.append(Paragraph(lead_text, True, styles[0] if styles else ""))
                text = rest
        style = max(set(styles), key=styles.count) if (is_heading and styles) else ""
        paragraphs.append(Paragraph(text, is_heading, style))
    return paragraphs


def extract_risk_factors(paragraphs: list[Paragraph]) -> list[Paragraph]:
    """Return just the paragraphs inside Item 1A (heading line excluded)."""
    best_start, best_len = None, 0
    for i, p in enumerate(paragraphs):
        # A heading is short; long lines starting "Item 1A of this report..." are prose.
        if ITEM_1A.match(p.text) and len(p.text) < 120:
            for j in range(i + 1, len(paragraphs)):
                if NEXT_ITEM.match(paragraphs[j].text) and len(paragraphs[j].text) < 150:
                    break
            else:
                j = len(paragraphs)
            if j - i > best_len:
                best_start, best_len = i, j - i
    if best_start is None:
        raise ValueError("Could not find an 'Item 1A. Risk Factors' section")

    section = paragraphs[best_start + 1 : best_start + best_len]
    # Drop repeated running page headers like "Item 1A" inside the section.
    section = [p for p in section if not re.fullmatch(r"(part i\s*)?item 1a\.?", p.text, re.I)]
    # Short lines repeated 3+ times are running page headers (e.g. "Alphabet Inc.").
    counts = {}
    for p in section:
        if len(p.text) < 60:
            counts[p.text] = counts.get(p.text, 0) + 1
    section = [p for p in section if counts.get(p.text, 0) < 3]
    # Some filings put "Risk Factors" on its own line right after "Item 1A".
    # Also skip a leading duplicate like "ITEM 1A. RISK FACTORS".
    while section and (section[0].text.lower().strip(" .:") == "risk factors"
                       or (ITEM_1A.match(section[0].text) and len(section[0].text) < 120)):
        section = section[1:]
    return section


def group_risks(section: list[Paragraph]) -> list[dict]:
    """Group paragraphs under their risk headings.

    Returns [{"title": "...", "body": "...", "category": "..."}].

    Filings use up to three heading levels, and every company formats them
    differently:
      * category    - e.g. "Macroeconomic and Industry Risks"
      * risk title  - e.g. "We face intense competition..." (Apple, Microsoft)
                      or "We Face Intense Competition" (Amazon, Title Case)
      * sub-heading - inside a risk, e.g. Microsoft's italic "Business model competition"

    So we adapt per filing: find the style (bold / italic / ...) used by
    sentence-style titles (those ending in a period). Then:
      * a short heading directly followed by another heading -> category
      * a short heading in the title style (or any style, if the filing has no
        sentence-style titles at all) -> risk title
      * a short heading in a different style -> sub-heading, kept as body text
    """
    def is_sentence_title(p):
        return p.is_heading and (len(p.text) >= 80 or p.text.rstrip().endswith("."))

    def is_category_label(p):
        # "Risks Related to Our Industry", "Risk Factors Summary", "General Risks".
        # Not "We Have Foreign Exchange Risk" (a real Amazon risk title).
        if not p.is_heading or len(p.text) >= 100:
            return False
        words = p.text.split()
        if words[0].lower() in {"we", "our", "if", "any", "the", "a", "an", "there", "changes"}:
            return False
        return bool(re.match(r"^(risks?\b|risk factors summary|summary)", p.text, re.I)
                    or (len(words) <= 7 and re.search(r"\brisks$", p.text.strip(), re.I)))

    # Intro sentences sometimes appear in italics; they're not risk titles.
    # Also, a "heading" starting in lowercase is a sentence continued across a page break.
    INTRO = re.compile(r"^(the following|you should|in addition to the other|please carefully|the above summary)", re.I)
    section = [Paragraph(p.text, False, "") if (INTRO.match(p.text) or p.text[:1].islower()) else p
               for p in section]

    sentence_styles = [p.style for p in section if is_sentence_title(p)]
    title_style = max(set(sentence_styles), key=sentence_styles.count) if sentence_styles else None

    risks, category, current = [], None, None
    for i, p in enumerate(section):
        nxt = section[i + 1] if i + 1 < len(section) else None
        if p.is_heading and not is_sentence_title(p):
            if (nxt is not None and nxt.is_heading) or is_category_label(p):
                category = p.text
                # Text under a summary heading is overview, not a risk.
                if re.search(r"summary", p.text, re.I) or re.search(r"summary", category or "", re.I):
                    current = None
                continue
            if p.text.rstrip().endswith(":"):
                is_title = False  # a label like "Competition laws and new market regulation:"
            elif title_style is None or p.style == title_style:
                is_title = True
            else:
                is_title = False  # sub-heading inside the current risk
        else:
            is_title = p.is_heading

        if is_title:
            current = {"title": p.text, "body": "", "category": category}
            risks.append(current)
            continue
        if current is None:  # intro/summary text before the first risk title
            if risks and risks[-1]["title"] == "(Introduction)":
                current = risks[-1]
            else:
                current = {"title": "(Introduction)", "body": "", "category": None}
                risks.append(current)
        current["body"] += (" " if current["body"] else "") + p.text
    return risks


def risks_for_filing(filing: dict) -> list[dict]:
    """Convenience: download a filing and return its grouped risks."""
    import edgar
    return group_risks(extract_risk_factors(html_to_paragraphs(edgar.download_filing(filing))))


if __name__ == "__main__":
    import edgar

    for ticker in ["AAPL", "MSFT", "JPM", "TSLA", "KO"]:
        filing = edgar.list_10ks(ticker)[0]
        paras = html_to_paragraphs(edgar.download_filing(filing))
        section = extract_risk_factors(paras)
        risks = group_risks(section)
        words = sum(len(p.text.split()) for p in section)
        print(f"{ticker} {filing['filing_date']}: {len(section)} paragraphs, "
              f"{words:,} words, {len(risks)} risks")
        print(f"   first: {section[0].text[:90]}")
        print(f"   last:  {section[-1].text[:90]}")
        print(f"   risk #2: {risks[1]['title'][:90]}")
