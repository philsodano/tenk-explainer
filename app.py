"""
Step 5: The web app.

Run it with:
    .venv/bin/streamlit run app.py
"""

import html
import re
from difflib import SequenceMatcher

import streamlit as st

import ask
import diff
import edgar
import parse

st.set_page_config(page_title="10-K Explainer", page_icon="📄", layout="wide")

st.markdown("""
<style>
.added   { background: rgba(46,160,67,.15); border-left: 3px solid #2ea043; padding: 6px 10px; margin: 4px 0; border-radius: 4px; }
.removed { background: rgba(248,81,73,.12); border-left: 3px solid #f85149; padding: 6px 10px; margin: 4px 0; border-radius: 4px; }
.reword  { border-left: 3px solid #d29922; padding: 6px 10px; margin: 4px 0; border-radius: 4px; }
.reword del { background: rgba(248,81,73,.25); text-decoration: line-through; }
.reword ins { background: rgba(46,160,67,.3); text-decoration: none; }
.quote   { border-left: 3px solid #888; padding: 4px 10px; margin: 4px 0; font-size: .92em; opacity: .9; }
</style>
""", unsafe_allow_html=True)


# ---------- cached data loading (so switching tabs doesn't re-download) ----------

@st.cache_data(show_spinner=False, ttl=6 * 3600)
def load_filings(ticker: str) -> list[dict]:
    return edgar.list_10ks(ticker)


@st.cache_data(show_spinner=False)
def load_paragraphs(url: str, _filing: dict):
    return parse.html_to_paragraphs(edgar.download_filing(_filing))


@st.cache_data(show_spinner=False)
def load_risks(url: str, _filing: dict):
    return parse.group_risks(parse.extract_risk_factors(load_paragraphs(url, _filing)))


@st.cache_data(show_spinner=False)
def load_phrase_changes(old_url: str, _old: dict, new_url: str, _new: dict):
    return diff.phrase_changes(load_risks(old_url, _old), load_risks(new_url, _new))


@st.cache_data(show_spinner=False)
def load_chunks(url: str, _filing: dict):
    return ask.chunk_filing(load_paragraphs(url, _filing))


def word_diff_html(old: str, new: str) -> str:
    """Show a reworded sentence with deletions struck out and insertions highlighted."""
    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape(" ".join(a[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f"<del>{html.escape(' '.join(a[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins>{html.escape(' '.join(b[j1:j2]))}</ins>")
    return " ".join(out)


# ---------- sidebar: choose company and years ----------

st.sidebar.title("📄 10-K Explainer")
ticker = st.sidebar.text_input("Ticker", value="AAPL", max_chars=10).strip().upper()

try:
    with st.spinner(f"Looking up {ticker} on SEC EDGAR..."):
        filings = load_filings(ticker)
except ValueError:
    st.error(f"Ticker **{ticker}** isn't in the SEC's company list.")
    st.stop()
except Exception as e:
    st.error(f"Couldn't reach SEC EDGAR: {e}")
    st.stop()

if not filings:
    st.warning(f"No 10-K filings found for **{ticker}** on EDGAR. "
               "(Foreign companies file 20-F instead, and new registrants may not have one yet.)")
    st.stop()

label = lambda f: f"FY ending {f['period']}  (filed {f['filing_date']})"
new_idx = st.sidebar.selectbox("Filing", range(len(filings)), format_func=lambda i: label(filings[i]))
older = list(range(new_idx + 1, len(filings)))
old_idx = (st.sidebar.selectbox("Compare against", older, format_func=lambda i: label(filings[i]))
           if older else None)
new_f = filings[new_idx]
old_f = filings[old_idx] if old_idx is not None else None

st.sidebar.markdown(f"[Open this filing on SEC.gov ↗]({new_f['url']})")
if old_f:
    st.sidebar.markdown(f"[Open comparison filing ↗]({old_f['url']})")
st.sidebar.caption("Data: SEC EDGAR. Parsing is automatic, so check important details "
                   "against the original filing.")

st.title(new_f["company"])
st.caption(f"Form 10-K · fiscal year ending {new_f['period']} · filed {new_f['filing_date']}")

tab_diff, tab_risks, tab_ask = st.tabs(["🔀 What changed", "⚠️ Risk factors", "💬 Ask a question"])


# ---------- tab 1: what changed ----------

with tab_diff:
    if not old_f:
        st.info("This is the oldest 10-K available, so there's nothing to compare it with.")
    else:
        try:
            with st.spinner("Downloading and comparing both filings..."):
                new_r = load_risks(new_f["url"], new_f)
                old_r = load_risks(old_f["url"], old_f)
                changes = diff.compare(old_r, new_r)
        except ValueError as e:
            st.error(f"Couldn't extract risk factors: {e}")
            st.stop()

        counts = diff.summarize(changes)
        n_rewritten = sum(c.rewritten for c in changes)
        st.subheader(f"Risk factors: FY{old_f['period'][:4]} → FY{new_f['period'][:4]}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("New risks", counts["new"])
        c2.metric("Removed risks", counts["removed"])
        c3.metric("Modified", counts["modified"], help=f"Includes {n_rewritten} rewritten from scratch")
        c4.metric("Unchanged", counts["unchanged"])

        # Free, rule-based highlights: no AI service needed.
        added_phr, dropped_phr = load_phrase_changes(old_f["url"], old_f, new_f["url"], new_f)
        with st.container(border=True):
            st.markdown("**Highlights**")
            new_titles = [c.title for c in changes if c.status == "new" and c.reused < diff.RESTRUCTURE_MIN]
            gone_titles = [c.title for c in changes if c.status == "removed" and c.reused < diff.RESTRUCTURE_MIN]
            edited = sorted((c for c in changes if c.status == "modified" and not c.rewritten),
                            key=lambda c: -(len(c.added) + len(c.removed) + len(c.reworded)))[:3]
            lines = []
            if new_titles:
                lines.append("**Brand-new risks:** " + " · ".join(f"*{t.rstrip('.')}*" for t in new_titles))
            if gone_titles:
                lines.append("**Dropped risks:** " + " · ".join(f"*{t.rstrip('.')}*" for t in gone_titles))
            if n_rewritten:
                lines.append(f"**{n_rewritten} risks were rewritten** in new language (marked 🟠 below).")
            if edited:
                lines.append("**Most heavily edited:** " + "; ".join(
                    f"*{c.title[:90]}{'…' if len(c.title) > 90 else ''}* "
                    f"({len(c.added)} added, {len(c.removed)} removed, {len(c.reworded)} reworded sentences)"
                    for c in edited))
            for line in lines:
                st.markdown("- " + line)
            if added_phr or dropped_phr:
                col_a, col_b = st.columns(2)
                col_a.markdown("**Phrases new this year**")
                col_a.markdown(" ".join(f"`{p}`" for p, _ in added_phr) or "–")
                col_b.markdown("**Phrases that disappeared**")
                col_b.markdown(" ".join(f"`{p}`" for p, _ in dropped_phr) or "–")
                st.caption("Phrases used at least twice in one year's risk factors and never in the other's. "
                           "Paste one into the filter on the Risk factors tab to see it in context.")

        show = st.multiselect("Show", ["new", "removed", "modified", "unchanged"],
                              default=["new", "removed", "modified"])
        order = {"new": 0, "removed": 1, "modified": 2, "unchanged": 3}
        size = lambda c: len(c.added) + len(c.removed) + len(c.reworded)
        for c in sorted(changes, key=lambda c: (order[c.status], -size(c))):
            if c.status not in show:
                continue
            icon = {"new": "🟢 NEW", "removed": "🔴 REMOVED", "modified": "🟡", "unchanged": "⚪"}[c.status]
            if c.status == "modified":
                icon = "🟠 REWRITTEN" if c.rewritten else f"🟡 +{len(c.added)} −{len(c.removed)} ~{len(c.reworded)}"
            with st.expander(f"{icon}  {c.title}"):
                if c.category:
                    st.caption(f"Category: {c.category}")
                if c.old_title:
                    st.markdown(f"**Previous title:** {c.old_title}")
                if c.status in ("new", "removed") and c.reused >= diff.RESTRUCTURE_MIN and c.related:
                    verb = "Built largely from text previously in" if c.status == "new" else "Much of this text moved into"
                    st.info(f"{c.reused:.0%} of this text appears in the other year. {verb}: "
                            + "; ".join(f"*{t}*" for t in c.related))
                if c.status == "new":
                    st.markdown(f"<div class='added'>{html.escape(c.body)}</div>", unsafe_allow_html=True)
                elif c.status == "removed":
                    st.markdown(f"<div class='removed'>{html.escape(c.body)}</div>", unsafe_allow_html=True)
                elif c.status == "unchanged":
                    st.write(c.body)
                else:
                    if c.rewritten:
                        st.caption("Same topic, mostly new wording, so the sentence-level comparison below is long.")
                    for s in c.added:
                        st.markdown(f"<div class='added'>+ {html.escape(s)}</div>", unsafe_allow_html=True)
                    for s in c.removed:
                        st.markdown(f"<div class='removed'>− {html.escape(s)}</div>", unsafe_allow_html=True)
                    for o, n in c.reworded:
                        st.markdown(f"<div class='reword'>{word_diff_html(o, n)}</div>", unsafe_allow_html=True)


# ---------- tab 2: browse risk factors ----------

with tab_risks:
    try:
        risks = load_risks(new_f["url"], new_f)
    except ValueError as e:
        st.error(f"Couldn't extract risk factors: {e}")
        risks = []
    real = [r for r in risks if r["title"] != "(Introduction)"]
    if real:
        words = sum(len(r["body"].split()) for r in risks)
        st.caption(f"{len(real)} risk factors · {words:,} words")
        query = st.text_input("Filter risks", placeholder="e.g. China, AI, tariffs, interest rates")
        current_cat = object()
        for r in real:
            if query and query.lower() not in (r["title"] + " " + r["body"]).lower():
                continue
            if r["category"] != current_cat:
                current_cat = r["category"]
                if current_cat:
                    st.markdown(f"#### {current_cat}")
            with st.expander(r["title"]):
                st.write(r["body"])


# ---------- tab 3: search the filing ----------

def highlight(text: str, terms: list[str]) -> str:
    """HTML-escape text and bold every word that starts with a search term."""
    out = html.escape(text)
    for t in terms:
        if len(t) < 3:
            continue
        stem = re.escape(html.escape(t[:-1] if t.endswith("s") and len(t) > 4 else t))
        out = re.sub(rf"(?i)\b({stem}\w*)", r"<mark>\1</mark>", out)
    return out


with tab_ask:
    st.caption("Ask a question in plain English. You'll get the filing's own sentences that best "
               "answer it, word for word, with the section each one comes from.")
    examples = ["How much did the company spend on buybacks?",
                "How many employees does the company have?",
                "What does the company say about artificial intelligence?",
                "What lawsuits is the company involved in?"]
    cols = st.columns(len(examples))
    for col, ex in zip(cols, examples):
        if col.button(ex, use_container_width=True):
            st.session_state["question"] = ex
    question = st.text_input("Your question", key="question",
                             placeholder="e.g. What was total revenue? How much debt does it have?")
    if question:
        with st.spinner("Searching the filing..."):
            chunks = load_chunks(new_f["url"], new_f)
            ans = ask.ask(question, chunks, max_passages=5, company=new_f["company"])
        if not ans.passages:
            st.info("Nothing in this filing matched. Try different words "
                    "(filings say \"repurchase\" rather than \"buyback\", \"net sales\" rather than \"revenue\").")
        for i, p in enumerate(ans.passages, 1):
            context = (f"<span style='opacity:.6'>{html.escape(p.before)}</span> " if p.before else "")
            context += f"<b>{highlight(p.sentence, ans.query_terms)}</b>"
            context += (f" <span style='opacity:.6'>{html.escape(p.after)}</span>" if p.after else "")
            st.markdown(f"<div class='quote'><b>{i}.</b> <i>{html.escape(p.section)}</i><br>{context}</div>",
                        unsafe_allow_html=True)
        if ans.passages:
            st.caption("Results are ranked by keyword relevance, not understood by an AI, so read them in "
                       "context. Numbers in tables can appear as long runs of figures. Open the filing on "
                       "SEC.gov (sidebar) for the full table.")
