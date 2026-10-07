"""
bank_analysis_service.py — builds the per-central-bank analysis payload that
powers the Central Banks tab.

Everything here is derived from stored data (policy_statements,
statement_texts, release_calendar) plus the transparent rules in
statement_analysis.py. Nothing is invented: if the data isn't there, the
payload says so (status 'no_data' / 'insufficient') instead of guessing.

The "market implications" section describes how markets TYPICALLY react to a
hawkish or dovish shift and what would invalidate that read. It is
educational context, not trade advice — and it explicitly says it cannot know
how much is already priced in, because market-pricing data (futures/OIS) is
not connected to this system.
"""

import html
import re
from datetime import datetime, timezone

import db
import statement_analysis as sa

DISCLAIMER = ("Educational analysis of statement wording only — not financial advice and not a "
              "recommendation to buy or sell anything. I'm not a licensed financial advisor; "
              "check the linked source documents and consider your own risk before acting.")

# name, currency, FX pair (+ whether the pair rises when the currency strengthens),
# rate instrument, equity instrument, and the data releases that most often move this bank's view.
BANK_META = {
    "FED": dict(name="Federal Reserve", ccy="USD", pair=None, follows=None,
                rates="US 2-year Treasury yield", equity="US equities (S&P 500 / Nasdaq)", gold=True,
                watch=["US CPI and core PCE inflation", "Non-farm payrolls and the unemployment rate",
                       "Jobless claims and wage growth", "FOMC minutes and Fed speakers"]),
    "ECB": dict(name="European Central Bank", ccy="EUR", pair="EUR/USD", follows=True,
                rates="German 2-year Bund yield", equity="European equities (Euro Stoxx 50 / DAX)", gold=False,
                watch=["Euro-area HICP inflation (flash) and services inflation", "ECB wage tracker / negotiated wages",
                       "PMIs and German data", "ECB speakers and the account of the meeting"]),
    "BOE": dict(name="Bank of England", ccy="GBP", pair="GBP/USD", follows=True,
                rates="UK 2-year gilt yield", equity="UK equities (FTSE 100 / FTSE 250)", gold=False,
                watch=["UK CPI and services CPI", "Wage growth and labour-market data",
                       "MPC vote split and speakers", "UK GDP and PMIs"]),
    "BOJ": dict(name="Bank of Japan", ccy="JPY", pair="USD/JPY", follows=False,
                rates="Japanese government bond yields", equity="Japanese equities (Nikkei 225)", gold=False,
                watch=["Spring wage (shunto) outcomes", "Japan core CPI", "Tankan survey", "Yen moves and MoF comments"]),
    "SNB": dict(name="Swiss National Bank", ccy="CHF", pair="USD/CHF", follows=False,
                rates="Swiss government bond yields", equity="Swiss equities (SMI)", gold=False,
                watch=["Swiss CPI", "Franc strength and intervention comments", "Euro-area inflation and ECB path"]),
    "BOC": dict(name="Bank of Canada", ccy="CAD", pair="USD/CAD", follows=False,
                rates="Canadian 2-year government bond yield", equity="Canadian equities (S&P/TSX)", gold=False,
                watch=["Canada CPI (median and trim)", "Labour Force Survey", "Oil prices", "Canada GDP and trade data"]),
    "RBA": dict(name="Reserve Bank of Australia", ccy="AUD", pair="AUD/USD", follows=True,
                rates="Australian 3-year government bond yield", equity="Australian equities (ASX 200)", gold=False,
                watch=["Australia monthly CPI", "Wage Price Index", "Labour Force data", "China activity data and iron ore"]),
    "RBNZ": dict(name="Reserve Bank of New Zealand", ccy="NZD", pair="NZD/USD", follows=True,
                 rates="New Zealand 2-year government bond yield", equity="NZ equities (NZX 50)", gold=False,
                 watch=["New Zealand CPI", "Labour-market data", "Dairy auction prices", "Global growth and risk sentiment"]),
    "SARB": dict(name="South African Reserve Bank", ccy="ZAR", pair="USD/ZAR", follows=False,
                 rates="South African government bond yields", equity="South African equities (JSE Top 40)", gold=False,
                 watch=["South Africa CPI", "Rand moves and global risk appetite", "Fuel prices and electricity tariffs", "Fiscal and credit-rating news"]),
    "PBOC": dict(name="People's Bank of China", ccy="CNY", pair="USD/CNY", follows=False,
                 rates="China government bond yields / Loan Prime Rates", equity="Chinese equities (CSI 300 / Hang Seng)", gold=False,
                 watch=["China CPI and PPI", "Credit and TSF data", "Property-sector data", "LPR fixings"]),
    "BANXICO": dict(name="Banco de México", ccy="MXN", pair="USD/MXN", follows=False,
                    rates="Mexican government bond yields (Mbono)", equity="Mexican equities (IPC)", gold=False,
                    watch=["Mexico CPI and core inflation", "Peso moves", "The Fed's path (peso is sensitive to US rates)", "Mexican activity data"]),
}

FED_CATALYST_KEYWORDS = ["consumer price index", "employment situation", "personal income and outlays",
                         "gross domestic product", "producer price index", "fomc", "summary of economic projections",
                         "job openings", "retail", "jobless", "unemployment insurance"]

MIN_FULL_TEXT_CHARS = 400
MIN_MANUAL_CHARS = 80
HIGH_CONFIDENCE_CHARS = 1200


# ----------------------------------------------------------------- helpers
def _fmt_date(published_at):
    dt = sa.parse_pub_date(published_at)
    return dt.strftime("%d %b %Y").lstrip("0") if dt else (published_at or "date unknown")


def _clean(text):
    t = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", t).strip()


def _text_for(row, texts):
    """Preference: text the user pasted > fetched full text > feed summary."""
    t = texts.get(row.get("link"))
    if t and t["text"]:
        if t["status"] == "manual" and len(t["text"]) >= MIN_MANUAL_CHARS:
            return t["text"], "manual"
        if t["status"] == "ok" and len(t["text"]) >= MIN_FULL_TEXT_CHARS:
            return t["text"], "full"
    summary = _clean(row.get("summary"))
    title = _clean(row.get("title"))
    return (f"{title}. {summary}" if summary else title), "summary"


def _stmt_public(row, source, chars):
    return {"title": _clean(row.get("title")), "date": _fmt_date(row.get("published_at")),
            "link": row.get("link"), "text_source": source, "chars": chars,
            "entered_manually": row.get("source_category") == "manual"}


def _phrases(analysis, direction, statuses=("new", "unchanged"), n=3):
    out = [e for e in analysis["evidence"] if e["direction"] == direction and e["status"] in statuses]
    out.sort(key=lambda e: -e["weight"])
    return [e["phrase"] for e in out[:n]]


def _quoted(items):
    return ", ".join(f"\u201c{i}\u201d" for i in items)


# --------------------------------------------------------------- narrative
def build_narrative(meta, latest, previous, analysis):
    st, dec = analysis["stance"], analysis["decision"]
    paras = []

    if previous is None:
        paras.append(
            f"Only one {meta['name']} statement is available so far ({latest['date']}). Its wording reads "
            f"{st['label_latest']} (tone score {st['latest']:.0f}/100). A comparison needs at least two statements.")
    else:
        if st["shift_label"] == "little changed":
            paras.append(
                f"The latest {meta['name']} statement ({latest['date']}) reads {st['label_latest']} "
                f"({st['latest']:.0f}/100) and the tone is essentially unchanged from the previous statement "
                f"({previous['date']}, {st['label_previous']}, {st['previous']:.0f}/100).")
        else:
            paras.append(
                f"The latest {meta['name']} statement ({latest['date']}) reads {st['label_latest']} "
                f"({st['latest']:.0f}/100) \u2014 {abs(st['shift']):.0f} points {st['shift_label']} than the "
                f"previous statement ({previous['date']}, {st['label_previous']}, {st['previous']:.0f}/100).")

    dl = dec["latest"]["label"]
    dp = dec["previous"]["label"] if dec["previous"] else None
    if dl != "UNCLEAR":
        if dp and dp not in ("UNCLEAR", dl):
            paras.append(f"Policy action detected in the text: {dl} (previous statement: {dp}). A change in the "
                         f"stated action is the strongest single signal in this comparison.")
        elif dp == dl:
            paras.append(f"Policy action detected in the text: {dl}, the same as the previous statement \u2014 so the "
                         f"shift in tone comes from the surrounding language rather than from a change in action.")
        else:
            paras.append(f"Policy action detected in the text: {dl}.")
    else:
        paras.append("No explicit rate decision could be detected in the available text, so the tone read relies on "
                     "the surrounding language only.")

    if previous is not None:
        movers = [d for d in analysis["drivers"] if d["delta"]][:2]
        for d in movers:
            cat_ev = [e for e in analysis["evidence"] if e["category"] == d["category"]]
            new = [e["phrase"] for e in cat_ev if e["status"] == "new"][:2]
            gone = [e["phrase"] for e in cat_ev if e["status"] == "removed"][:2]
            way = "more hawkish" if d["delta"] > 0 else "more dovish"
            bits = []
            if new:
                bits.append(f"new: {_quoted(new)}")
            if gone:
                bits.append(f"dropped: {_quoted(gone)}")
            paras.append(f"{d['name']} wording moved {way}" + (f" ({'; '.join(bits)})." if bits else "."))

    label = st["label_latest"]
    if label == "HAWKISH":
        top = _phrases(analysis, "hawkish")
        paras.append("Why the bias is hawkish: " + (f"the strongest hawkish wording is {_quoted(top)}."
                     if top else "hawkish language outweighs dovish language."))
    elif label == "DOVISH":
        top = _phrases(analysis, "dovish")
        paras.append("Why the bias is dovish: " + (f"the strongest dovish wording is {_quoted(top)}."
                     if top else "dovish language outweighs hawkish language."))
    else:
        h, d = _phrases(analysis, "hawkish", n=2), _phrases(analysis, "dovish", n=2)
        paras.append("Why the bias is neutral: hawkish and dovish language roughly offset"
                     + (f" \u2014 hawkish: {_quoted(h)}" if h else "") + (f"; dovish: {_quoted(d)}" if d else "") + ".")
    return paras


# ------------------------------------------------------------ implications
def _direction(stance):
    s = stance.get("shift")
    if s is None:
        return "none"
    return "hawkish" if s >= sa.SHIFT_THRESHOLD else "dovish" if s <= -sa.SHIFT_THRESHOLD else "none"


def build_implications(meta, analysis):
    d = _direction(analysis["stance"])
    ccy, pair = meta["ccy"], meta["pair"]
    rows = [{"asset": f"{ccy} (the currency)", "hawkish": "Typically supported", "dovish": "Typically pressured"}]
    if pair:
        up, down = ("rise", "fall") if meta["follows"] else ("fall", "rise")
        rows.append({"asset": pair, "hawkish": f"Tends to {up}", "dovish": f"Tends to {down}"})
    else:
        rows.append({"asset": "EUR/USD, GBP/USD, AUD/USD", "hawkish": "Tend to fall (USD stronger)", "dovish": "Tend to rise (USD weaker)"})
        rows.append({"asset": "USD/JPY, USD/CAD", "hawkish": "Tend to rise (USD stronger)", "dovish": "Tend to fall (USD weaker)"})
    rows.append({"asset": meta["rates"], "hawkish": "Tends to rise", "dovish": "Tends to fall"})
    rows.append({"asset": "Yield curve (2s10s)", "hawkish": "Front end rises faster \u2192 flatter", "dovish": "Front end falls faster \u2192 steeper"})
    if meta.get("gold"):
        rows.append({"asset": "Gold (USD-priced)", "hawkish": "Pressure from a stronger USD / higher real yields", "dovish": "Supported by a weaker USD / lower real yields"})
    rows.append({"asset": meta["equity"], "hawkish": "Rate-sensitive / long-duration stocks pressured", "dovish": "Supportive, unless the easing reflects growth fears"})

    shift = abs(analysis["stance"].get("shift") or 0)
    if d == "hawkish":
        summary = (f"The statement moved {shift:.0f} points more hawkish. Historically, a hawkish shift tends to "
                   f"support {ccy} and push short-term yields up \u2014 but only to the extent it surprises relative to what was already priced.")
        scenarios = [
            f"Tone-shift view: if the market had not priced this, the typical response is a firmer {ccy} and higher short-term yields. Check recent price action first \u2014 if {ccy} already rallied into the release, the surprise may be smaller than the text suggests.",
            "Relative view: tone matters most relative to other banks. A hawkish shift is most meaningful against a currency whose central bank is neutral or dovish \u2014 compare the other tabs.",
            "Confirmation: tone backed by firm inflation or jobs data tends to persist; tone contradicted by soft data often fades. See the watch-list under 'What to expect next'.",
        ]
        invalid = ["Softer-than-expected inflation prints", "Rising unemployment or weaker jobs data",
                   "Officials pushing back in speeches or in the minutes", "The move already being fully priced (the reaction can reverse)"]
    elif d == "dovish":
        summary = (f"The statement moved {shift:.0f} points more dovish. Historically, a dovish shift tends to "
                   f"weigh on {ccy} and pull short-term yields down \u2014 but only to the extent it surprises relative to what was already priced.")
        scenarios = [
            f"Tone-shift view: if the market had not priced this, the typical response is a softer {ccy} and lower short-term yields. Check recent price action first \u2014 if {ccy} already sold off into the release, the surprise may be smaller than the text suggests.",
            "Relative view: tone matters most relative to other banks. A dovish shift is most meaningful against a currency whose central bank is neutral or hawkish \u2014 compare the other tabs.",
            "Confirmation: tone backed by weakening data tends to persist; tone contradicted by hot inflation or strong jobs data often fades. See the watch-list under 'What to expect next'.",
        ]
        invalid = ["Re-acceleration in inflation or wages", "Stronger-than-expected jobs or activity data",
                   "Officials signalling caution about easing too fast", "The move already being fully priced (the reaction can reverse)"]
    else:
        summary = ("No meaningful change in tone versus the compared statement, so a statement-driven move is less "
                   "likely. Attention shifts to incoming data and the next meeting." if analysis["stance"]["shift"] is not None
                   else "A single statement can't show a change in tone, so no shift-based implication is drawn.")
        scenarios = ["With no clear tone change, there is little statement-driven edge; data releases and the next meeting matter more.",
                     "Tone can still matter in relative terms \u2014 compare this bank's level against the other tabs."]
        invalid = ["A change in the stated decision or guidance at the next meeting"]
    return {
        "direction": d, "summary": summary, "rows": rows, "scenarios": scenarios, "invalidation": invalid,
        "risk_notes": [
            "Market pricing (futures / OIS) is not connected to this dashboard, so I can't tell how much of this shift is already priced in \u2014 and that often decides the reaction.",
            "First reactions frequently reverse once the press conference, speeches or minutes change the message.",
            "Spreads and volatility are unusually high around statements and key data \u2014 size risk accordingly.",
        ],
        "disclaimer": DISCLAIMER,
    }


# ------------------------------------------------------------- expectation
def build_expectation(meta, analysis, catalysts):
    st = analysis["stance"]
    label, shift = st["label_latest"], st.get("shift")
    if label == "NEUTRAL" and shift is not None and shift >= sa.SHIFT_THRESHOLD:
        bias = "LEANING HAWKISH"
    elif label == "NEUTRAL" and shift is not None and shift <= -sa.SHIFT_THRESHOLD:
        bias = "LEANING DOVISH"
    else:
        bias = label

    if "HAWKISH" in bias:
        headline = (f"Near-term tone to expect from the {meta['name']}: {bias}. The wording leans toward keeping policy "
                    f"tight, or tightening further, until inflation risks clearly recede.")
        reasons = _phrases(analysis, "hawkish", statuses=("new", "unchanged"), n=4)
        change = ["Inflation prints coming in clearly below expectations", "A visible rise in unemployment or drop in activity",
                  "Language softening in the next statement (e.g. dropping 'restrictive' / 'elevated' wording)"]
    elif "DOVISH" in bias:
        headline = (f"Near-term tone to expect from the {meta['name']}: {bias}. The wording leans toward easing, or toward "
                    f"giving weight to slowing growth and a softer labour market.")
        reasons = _phrases(analysis, "dovish", statuses=("new", "unchanged"), n=4)
        change = ["Inflation or wage data re-accelerating", "Stronger-than-expected jobs or activity data",
                  "Language turning cautious about easing too quickly in the next statement"]
    else:
        headline = (f"Near-term tone to expect from the {meta['name']}: NEUTRAL. Hawkish and dovish language roughly "
                    f"offset, which usually means decisions stay data-dependent.")
        reasons = _phrases(analysis, "hawkish", n=2) + _phrases(analysis, "dovish", n=2)
        change = ["A clear run of inflation or labour-market data in one direction", "A change in the stated decision or forward guidance"]

    return {
        "bias": bias, "headline": headline, "reasons": reasons,
        "what_would_change_it": change, "watch_items": meta["watch"], "catalysts": catalysts,
        "caveat": ("This is a read of the statement's wording \u2014 not a forecast of the next rate decision. Meeting dates "
                   "and market pricing aren't connected here, so check the bank's own calendar and rate-futures pricing."),
    }


# -------------------------------------------------------------- confidence
def build_confidence(latest, previous):
    reasons = []
    real = ("full", "manual")            # text of the actual statement (fetched or pasted)
    good = [s for s in (latest, previous) if s and s["text_source"] in real and s["chars"] >= MIN_FULL_TEXT_CHARS]
    if previous is None:
        level = "MEDIUM" if latest["text_source"] in real else "LOW"
        reasons.append("Only one statement available \u2014 no comparison possible.")
    elif len(good) == 2 and min(latest["chars"], previous["chars"]) >= HIGH_CONFIDENCE_CHARS:
        level = "HIGH"
        reasons.append("Both statements were analysed from their full text.")
    elif good:
        level = "MEDIUM"
    else:
        level = "LOW"
    for name, s in (("Latest", latest), ("Previous", previous)):
        if not s:
            continue
        if s["text_source"] == "manual":
            reasons.append(f"{name} statement was analysed from text you pasted or uploaded.")
            if s["chars"] < MIN_FULL_TEXT_CHARS:
                reasons.append(f"The {name.lower()} pasted text is short, so treat the result as indicative.")
        elif s["text_source"] == "summary":
            reasons.append(f"{name} statement analysed from its feed summary only (full text not available \u2014 "
                           f"blocked by the site's robots.txt, not fetched yet, or the page layout wasn't readable). "
                           f"Treat the diff and tone score as indicative.")
    if level != "HIGH":
        reasons.append("To raise this: paste the full statement text, or upload its PDF, in the "
                       "\u201cManual update\u201d box at the bottom of this tab.")
    reasons.append("Rule-based wording analysis: transparent and repeatable, but it can't read nuance the way a human analyst can.")
    return {"level": level, "reasons": reasons}


# ----------------------------------------------------------------- catalysts
def _catalysts(code, today_iso):
    if code != "FED":
        return []
    try:
        rows = db.get_upcoming_releases(today_iso, limit=120)
    except Exception:
        return []
    out = [{"date": r["release_date"], "release": r["release_name"]} for r in rows
           if any(k in (r["release_name"] or "").lower() for k in FED_CATALYST_KEYWORDS)]
    return out[:8]


# --------------------------------------------------------------------- main
def build_bank_analysis(code, compare=1, today=None):
    code = (code or "").upper()
    if code not in BANK_META:
        raise KeyError(code)
    meta = BANK_META[code]
    bank_public = {"code": code, "name": meta["name"], "ccy": meta["ccy"], "pair": meta["pair"]}
    today_iso = today or datetime.now(timezone.utc).date().isoformat()

    stmts = sa.select_statements(code, db.get_policy_statements_raw(code, 300))
    base = {"bank": bank_public, "statement_count": len(stmts), "disclaimer": DISCLAIMER}

    if not stmts:
        return dict(base, status="no_data", message=(
            f"No monetary-policy statements for {meta['name']} have been ingested yet. Either the RSS collector hasn't "
            f"run, this bank's feed isn't verified/available, or its feed titles don't look like policy statements."))

    compare = max(1, min(int(compare or 1), len(stmts) - 1)) if len(stmts) > 1 else 0
    wanted = [stmts[0]] + ([stmts[compare]] if compare else [])
    texts = db.get_statement_texts([s["link"] for s in wanted])

    latest_text, latest_src = _text_for(stmts[0], texts)
    latest = _stmt_public(stmts[0], latest_src, len(latest_text))
    history = [{"index": i, "title": _clean(s["title"]), "date": _fmt_date(s["published_at"]), "link": s["link"],
                "entered_manually": s.get("source_category") == "manual"}
               for i, s in enumerate(stmts[1:13], start=1)]
    catalysts = _catalysts(code, today_iso)

    if not compare:
        analysis = sa.analyze_single(latest_text)
        return dict(base, status="insufficient", latest=latest, previous=None, history=history, compare=0,
                    analysis=analysis, narrative=build_narrative(meta, latest, None, analysis),
                    implications=build_implications(meta, analysis),
                    expectation=build_expectation(meta, analysis, catalysts),
                    confidence=build_confidence(latest, None),
                    message="Only one statement is available, so no comparison can be shown yet.")

    prev_text, prev_src = _text_for(stmts[compare], texts)
    previous = _stmt_public(stmts[compare], prev_src, len(prev_text))
    analysis = sa.analyze_pair(prev_text, latest_text)
    return dict(base, status="ok", latest=latest, previous=previous, history=history, compare=compare,
                analysis=analysis, narrative=build_narrative(meta, latest, previous, analysis),
                implications=build_implications(meta, analysis),
                expectation=build_expectation(meta, analysis, catalysts),
                confidence=build_confidence(latest, previous))
