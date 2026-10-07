"""
statement_analysis.py — compares two central-bank monetary policy statements
and explains what changed.

WHAT THIS IS (and isn't):
  A transparent, rule-based text analysis. It (1) diffs the two statements
  sentence-by-sentence and word-by-word, (2) scores each statement's
  hawkish/dovish tone from a published phrase lexicon, (3) detects whether
  the text announces a hike / cut / hold, and (4) reports which phrases were
  added, dropped or unchanged, so every conclusion shows its evidence.

  It is NOT a language model, NOT backtested, and does NOT know market
  pricing. A lexicon cannot read nuance, sarcasm or hedging the way a human
  analyst can; treat the output as a structured first read to be checked
  against the linked source documents. English-language text only.

This module is deliberately free of any database or network access so it can
be unit-tested exhaustively (see tests/test_statement_analysis.py).
"""

import difflib
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# ---------------------------------------------------------------------------
# 1. Which feed items are actually monetary-policy statements?
# ---------------------------------------------------------------------------
# Central-bank RSS feeds mix press releases, speeches, minutes, reports and
# announcements. These are matched against the item TITLE. This is a heuristic:
# the UI always shows the title and a link so the user can verify.

INCLUDE_PATTERNS = [
    "monetary policy", "fomc statement", "policy rate", "interest rate decision",
    "rate decision", "official cash rate", "bank rate", "cash rate",
    "policy interest rate", "key interest rates",
]
EXCLUDE_PATTERNS = [
    "minutes", "speech", "testimony", "implementation note", "press conference",
    "monetary policy report", "summary of opinions", "outlook for economic activity",
    "economic bulletin", "account of", "consultation", "survey",
]
# Per-bank extra exclusions (titles that match the generic include list but are
# a different, much larger document type than the decision statement).
BANK_EXTRA_EXCLUDES = {
    "RBA": ["statement on monetary policy"],   # RBA's quarterly report, not the decision
    "BOE": ["monetary policy report"],
}


# Item links that reveal a different document type even when the title looks relevant
# (e.g. a speech called "Economic Outlook and Monetary Policy").
EXCLUDE_LINK_PARTS = ["/speech", "/testimony", "/minutes", "/podcast", "/blog", "ecb.sp"]


def is_policy_statement(bank, title, link=None):
    t = (title or "").lower()
    if not t:
        return False
    lk = (link or "").lower()
    if lk and any(part in lk for part in EXCLUDE_LINK_PARTS):
        return False
    for ex in EXCLUDE_PATTERNS + BANK_EXTRA_EXCLUDES.get((bank or "").upper(), []):
        if ex in t:
            return False
    return any(p in t for p in INCLUDE_PATTERNS)


def parse_pub_date(value):
    """Parses the date formats found in central-bank feeds (RFC-822 like
    'Wed, 17 Sep 2025 14:00:00 EDT', ISO-8601, plain dates). Returns an aware
    UTC datetime, or None if it can't be parsed (so callers can sort those last)."""
    if not value:
        return None
    s = str(value).strip()
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    for candidate in (s, s.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def select_statements(bank, rows, collapse_days=2):
    """rows: dicts with title/published_at/link/summary. Returns the monetary-
    policy statements, newest first, with duplicate links removed and
    same-meeting documents collapsed (e.g. an ECB press release and its
    accompanying statement published the same day -> keep one)."""
    seen, picked = set(), []
    for r in rows:
        link = r.get("link") or ""
        manual = r.get("source_category") == "manual"
        # Entries the user added by hand are trusted as policy statements: no title/link filtering.
        if link in seen or not (manual or is_policy_statement(bank, r.get("title"), link)):
            continue
        seen.add(link)
        picked.append(dict(r, _dt=parse_pub_date(r.get("published_at"))))

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    picked.sort(key=lambda r: r["_dt"] or epoch, reverse=True)

    clusters = []
    for r in picked:
        if clusters and r["_dt"] and clusters[-1][0]["_dt"] and \
                abs((clusters[-1][0]["_dt"] - r["_dt"]).days) <= collapse_days:
            clusters[-1].append(r)
        else:
            clusters.append([r])

    def preference(r):
        t = (r.get("title") or "").lower()
        # a hand-entered statement beats a fetched one from the same meeting
        return (1 if r.get("source_category") == "manual" else 0,
                1 if "decision" in t else 0, r["_dt"] or epoch)

    result = []
    for c in clusters:
        best = max(c, key=preference)
        best.pop("_dt", None)
        result.append(best)
    return result


# ---------------------------------------------------------------------------
# 2. Sentence splitting and diffing
# ---------------------------------------------------------------------------
_ABBREVIATIONS = ["U.S.", "U.K.", "e.g.", "i.e.", "No.", "Mr.", "Mrs.", "Ms.", "Dr.",
                  "p.m.", "a.m.", "vs.", "etc.", "approx.", "Inc.", "St."]


def split_sentences(text):
    if not text:
        return []
    s = re.sub(r"\s+", " ", text).strip()
    for i, ab in enumerate(_ABBREVIATIONS):
        s = s.replace(ab, ab.replace(".", f"\u2024{i}\u2024"))
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"\u201c(])", s)
    out = []
    for p in parts:
        p = re.sub(r"\u2024\d+\u2024", ".", p).strip()
        if p:
            out.append(p)
    return out


def _words(sentence):
    return re.findall(r"\S+", sentence)


def _norm(word):
    return re.sub(r"[^\w%/\-]", "", word.lower())


def _word_diff(old_sentence, new_sentence):
    """Marks which words of new_sentence are not in old_sentence."""
    old_w = [_norm(w) for w in _words(old_sentence)]
    new_raw = _words(new_sentence)
    new_w = [_norm(w) for w in new_raw]
    sm = difflib.SequenceMatcher(None, old_w, new_w, autojunk=False)
    marks = ["new"] * len(new_raw)
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for j in range(j1, j2):
                marks[j] = "same"
    return [{"t": w, "k": k} for w, k in zip(new_raw, marks)]


def _similarity(a, b):
    return difflib.SequenceMatcher(
        None, [_norm(w) for w in _words(a)], [_norm(w) for w in _words(b)], autojunk=False
    ).ratio()


def diff_statements(prev_text, latest_text, pair_threshold=0.45):
    """Returns the LATEST statement as a list of sentence segments flagged
    same / changed / new (changed ones carry word-level marks), plus the
    sentences that were present in the previous statement and dropped."""
    prev_s, new_s = split_sentences(prev_text), split_sentences(latest_text)
    sm = difflib.SequenceMatcher(None, [x.lower() for x in prev_s],
                                 [x.lower() for x in new_s], autojunk=False)
    segments, removed = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            segments += [{"k": "same", "t": s} for s in new_s[j1:j2]]
        elif tag == "insert":
            segments += [{"k": "new", "t": s} for s in new_s[j1:j2]]
        elif tag == "delete":
            removed += prev_s[i1:i2]
        else:  # replace: pair each new sentence with its most similar old one
            old_block, new_block = list(prev_s[i1:i2]), new_s[j1:j2]
            used = set()
            for ns in new_block:
                best, best_r = None, 0.0
                for oi, os_ in enumerate(old_block):
                    if oi in used:
                        continue
                    r = _similarity(os_, ns)
                    if r > best_r:
                        best, best_r = oi, r
                if best is not None and best_r >= pair_threshold:
                    used.add(best)
                    segments.append({"k": "changed", "t": ns,
                                     "words": _word_diff(old_block[best], ns)})
                else:
                    segments.append({"k": "new", "t": ns})
            removed += [o for oi, o in enumerate(old_block) if oi not in used]

    stats = {k: sum(1 for s in segments if s["k"] == k) for k in ("same", "changed", "new")}
    stats["removed"] = len(removed)
    return {"segments": segments, "removed": removed, "stats": stats}


# ---------------------------------------------------------------------------
# 3. Hawkish / dovish lexicon
# ---------------------------------------------------------------------------
# (regex, weight, category, human-readable label). Each entry counts ONCE per
# statement however often it appears, so repeated boilerplate can't inflate a
# score. Weights: 1 = mild signal, 2 = clear, 3-4 = explicit policy direction.

HAWKISH = [
    (r"inflation (remains|remain|is|are|continues to be|stays) (somewhat |still |too |very )?(elevated|high|above)", 2, "inflation", "inflation remains elevated/high"),
    (r"\b(elevated|persistent|stubborn(ly)?( high)?) inflation", 2, "inflation", "elevated/persistent inflation"),
    (r"(inflationary|price|inflation) pressures? (remain|persist|are persistent|are elevated|are strong)", 2, "inflation", "inflation pressures persist"),
    (r"\bbut (still )?remains? (somewhat |still |well |significantly |above |too )*(elevated|above|high)", 1, "inflation", "inflation still elevated/above target despite easing"),
    (r"upside risks? to (the )?(inflation|price)|risks? to (the )?inflation (outlook )?(are|remain) (tilted |skewed )?(to the )?upside", 3, "risks", "upside risks to inflation"),
    (r"inflation expectations (have )?(risen|increased|moved up|become unanchored)", 3, "inflation", "inflation expectations rising"),
    (r"labou?r market (remains|is|remain|stays) (very |still |relatively )?(tight|strong|robust)|tight labou?r market", 2, "labour", "tight/strong labour market"),
    (r"job gains (have )?(remained|remain|been|continue to be) (strong|robust|solid)", 1, "labour", "strong job gains"),
    (r"unemployment rate (has )?(remained|remains|stayed|stays) low", 1, "labour", "unemployment remains low"),
    (r"wage (growth|pressures?|increases) (remain|remains|is|are|continue to be) (strong|elevated|robust|high)", 2, "labour", "strong wage growth"),
    (r"(solid|strong|robust|resilient) (pace|growth|expansion|activity)|expanding at a solid pace|economy (remains|is) resilient", 1, "growth", "solid/resilient growth"),
    (r"further (policy )?(tightening|firming)|additional (policy )?(tightening|firming)|(additional|further) (rate )?(increases|hikes)", 3, "guidance", "signals further tightening"),
    (r"(?<!less )(?<!less-)restrictive", 2, "guidance", "policy described as restrictive"),
    (r"higher for longer|higher for a longer|restrictive for (some time|longer)", 3, "guidance", "'higher for longer' guidance"),
    (r"(remain|remains|stay|stays) (highly |very )?(vigilant|attentive)|highly attentive to inflation", 1, "guidance", "vigilance on inflation"),
    (r"premature to (ease|cut|lower|reduce)|not (yet )?(appropriate|time) to (ease|cut|lower|reduce)", 3, "guidance", "too early to ease"),
    (r"\b(decided|voted|agreed|decide)\b[^.]{0,80}?\b(to )?(raise|increase|hike|lift)\b[^.]{0,80}?(rate|target range|bank rate|cash rate)", 4, "guidance", "DECISION: raise rates"),
    (r"\b(raised|increased|hiked|lifted)\b[^.]{0,40}\b(rate|target range|bank rate|cash rate)\b[^.]{0,40}\b(by|to)\s+\d", 4, "guidance", "DECISION: raised rates"),
    (r"continu\w+ (to )?(reduc\w+|run[- ]?off|shrink\w*)[^.]{0,60}(holdings|balance sheet|securities)|quantitative tightening", 1, "balance_sheet", "balance-sheet reduction (QT) continues"),
]

DOVISH = [
    (r"inflation (has|have) (eased|moderated|declined|fallen|slowed|come down|cooled)|inflation (continues to|is) (ease|easing|moderat\w+|declin\w+|cool\w+)|disinflation", 2, "inflation", "inflation easing/moderating"),
    (r"progress (on|toward|towards|in) (bringing |reducing )?inflation|inflation is (moving|converging|returning|heading) (sustainably |durably |back )*(toward|towards|to) ", 2, "inflation", "progress toward inflation target"),
    (r"(greater|growing|increased|more) confidence that inflation", 2, "inflation", "greater confidence inflation is returning to target"),
    (r"downside risks? to (the )?(inflation|price)|inflation (is|are) (expected to be |likely to be |running )?below (the |its )?target", 3, "inflation", "downside risks / inflation below target"),
    (r"labou?r market (has |have )?(softened|cooled|loosened|weakened|eased)|(softening|cooling|loosening|weakening) (of )?(the )?labou?r market", 2, "labour", "softening labour market"),
    (r"job gains (have )?(moderated|slowed|weakened|declined)", 2, "labour", "job gains slowing"),
    (r"unemployment rate (has )?(moved up|risen|increased|edged up|ticked up)", 2, "labour", "unemployment rate rising"),
    (r"\bslack\b|spare capacity", 1, "labour", "economic slack"),
    (r"(economic activity|growth|output|gdp|the economy|activity) (has |have )?(slowed|weakened|moderated|softened|contracted|stagnated|decelerated)|subdued (growth|activity|demand)|weak (growth|activity|demand)|\bslowdown\b", 2, "growth", "slowing/weak growth"),
    (r"downside risks?\b(?! to (the )?(inflation|price))", 3, "risks", "downside risks to growth/outlook"),
    (r"uncertainty (has |have )?(increased|risen)|uncertainty (remains|is) (elevated|high|heightened)", 1, "risks", "elevated uncertainty"),
    (r"(tighter|tightening of) financial conditions", 1, "risks", "tighter financial conditions"),
    (r"further (policy )?(easing|loosening|accommodation)|additional (policy )?(easing|accommodation)|(additional|further) (rate )?(cuts|reductions)", 3, "guidance", "signals further easing"),
    (r"less restrictive|reduc\w* (the degree of |the amount of )?(policy )?restraint|recalibrat\w+|(become|becoming|more) accommodative|\baccommodative\b", 2, "guidance", "moving toward less restrictive policy"),
    (r"(appropriate|room|scope) to (ease|reduce|lower|cut)", 3, "guidance", "room to ease"),
    (r"\b(decided|voted|agreed|decide)\b[^.]{0,80}?\b(to )?(lower|reduce|cut)\b[^.]{0,80}?(rate|target range|bank rate|cash rate)", 4, "guidance", "DECISION: cut rates"),
    (r"\b(lowered|reduced|cut)\b[^.]{0,40}\b(rate|target range|bank rate|cash rate)\b[^.]{0,40}\b(by|to)\s+\d", 4, "guidance", "DECISION: cut rates (past tense)"),
    (r"(slow|slowing|reduce|reducing) the pace of (decline|runoff|run-off|reduction)|quantitative easing|resum\w+ (asset )?purchases|expand\w* (its )?asset purchases", 2, "balance_sheet", "balance-sheet support (slower QT / QE)"),
]

_HAWK_RX = [(re.compile(p, re.I), w, c, l) for p, w, c, l in HAWKISH]
_DOVE_RX = [(re.compile(p, re.I), w, c, l) for p, w, c, l in DOVISH]

CATEGORY_NAMES = {
    "inflation": "Inflation", "labour": "Labour market", "growth": "Growth",
    "guidance": "Policy guidance / decision", "balance_sheet": "Balance sheet", "risks": "Risks & uncertainty",
}

_SCALE = 10.0          # tanh scale: raw +-10 maps to roughly +-38 points from neutral
HAWKISH_AT, DOVISH_AT = 60, 40
SHIFT_THRESHOLD = 8    # points of change we call a real shift


def _find_phrases(text):
    """label -> {direction, weight, category, example}"""
    found = {}
    flat = re.sub(r"\s+", " ", text or "")
    for direction, entries in (("hawkish", _HAWK_RX), ("dovish", _DOVE_RX)):
        for rx, weight, category, label in entries:
            m = rx.search(flat)
            if m:
                start = max(0, m.start() - 40)
                found[label] = {
                    "direction": direction, "weight": weight, "category": category,
                    "example": flat[start:m.end() + 40].strip(),
                }
    return found


def tone_label(score):
    if score >= HAWKISH_AT:
        return "HAWKISH"
    if score <= DOVISH_AT:
        return "DOVISH"
    return "NEUTRAL"


def score_text(text):
    """Returns the tone score (0-100, 50 = neutral, higher = more hawkish) and
    the phrase evidence it was built from."""
    found = _find_phrases(text)
    raw = sum(f["weight"] if f["direction"] == "hawkish" else -f["weight"] for f in found.values())
    score = round(50 + 50 * math.tanh(raw / _SCALE), 1)
    return {"score": score, "label": tone_label(score), "raw": raw, "phrases": found}


_DECISION_VERBS = re.compile(
    r"\b(decided|voted|agreed|decide|announced|resolved|maintain|maintains|maintained|hold|holds|held|"
    r"keep|keeps|kept|leave|leaves|left|retain|retains|retained|lower|lowers|lowered|cut|cuts|raise|raises|"
    r"raised|increase|increases|increased|reduce|reduces|reduced|hike|hikes|hiked)\b", re.I)
_CUT = re.compile(r"\b(lower|lowers|lowered|reduce|reduces|reduced|cut|cuts|cutting|reduction)\b", re.I)
_HIKE = re.compile(r"\b(raise|raises|raised|increase|increases|increased|hike|hikes|hiked|lift|lifted|raising)\b", re.I)
_HOLD = re.compile(r"\b(maintain|maintains|maintained|keep|keeps|kept|hold|holds|held|leave|leaves|left|retain|retains|retained|unchanged|steady|no change)\b", re.I)
# Policy-rate wording only — deliberately excludes "unemployment rate", "inflation rate", etc.
_RATE_WORDS = re.compile(
    r"(policy rate|policy interest rate|target range|bank rate|cash rate|key (ecb )?interest rates|interest rates?|"
    r"funds rate|overnight rate|reference rate|repo rate|refinancing rate|deposit facility|official cash rate)", re.I)


def detect_decision(text):
    """Finds the sentence announcing the rate decision. Returns
    {"label": "CUT"|"HIKE"|"HOLD"|"UNCLEAR", "evidence": sentence|None}."""
    for sentence in split_sentences(text):
        if not (_DECISION_VERBS.search(sentence) and _RATE_WORDS.search(sentence)):
            continue
        if _CUT.search(sentence) and not re.search(r"holdings|securities|balance sheet", sentence, re.I):
            return {"label": "CUT", "evidence": sentence}
        if _HIKE.search(sentence) and not re.search(r"holdings|securities|balance sheet", sentence, re.I):
            return {"label": "HIKE", "evidence": sentence}
        if _HOLD.search(sentence):
            return {"label": "HOLD", "evidence": sentence}
    return {"label": "UNCLEAR", "evidence": None}


def _category_sums(found):
    sums = {c: 0 for c in CATEGORY_NAMES}
    for f in found.values():
        sums[f["category"]] += f["weight"] if f["direction"] == "hawkish" else -f["weight"]
    return sums


def analyze_pair(prev_text, latest_text):
    prev, latest = score_text(prev_text), score_text(latest_text)
    shift = round(latest["score"] - prev["score"], 1)
    shift_label = ("more hawkish" if shift >= SHIFT_THRESHOLD
                   else "more dovish" if shift <= -SHIFT_THRESHOLD else "little changed")

    pc, lc = _category_sums(prev["phrases"]), _category_sums(latest["phrases"])
    drivers = [{"category": c, "name": CATEGORY_NAMES[c], "latest": lc[c],
                "previous": pc[c], "delta": lc[c] - pc[c]} for c in CATEGORY_NAMES]
    drivers.sort(key=lambda d: (abs(d["delta"]), abs(d["latest"])), reverse=True)

    evidence = []
    for label in sorted(set(prev["phrases"]) | set(latest["phrases"])):
        info = latest["phrases"].get(label) or prev["phrases"].get(label)
        status = ("unchanged" if label in prev["phrases"] and label in latest["phrases"]
                  else "new" if label in latest["phrases"] else "removed")
        evidence.append({"phrase": label, "category": info["category"], "direction": info["direction"],
                         "weight": info["weight"], "status": status,
                         "example": (latest["phrases"].get(label) or prev["phrases"].get(label))["example"]})
    evidence.sort(key=lambda e: (e["status"] == "unchanged", -e["weight"]))

    return {
        "stance": {"latest": latest["score"], "previous": prev["score"], "shift": shift,
                   "label_latest": latest["label"], "label_previous": prev["label"],
                   "shift_label": shift_label},
        "decision": {"latest": detect_decision(latest_text), "previous": detect_decision(prev_text)},
        "drivers": drivers,
        "evidence": evidence,
        "diff": diff_statements(prev_text, latest_text),
    }


def analyze_single(text):
    s = score_text(text)
    evidence = [{"phrase": l, "category": i["category"], "direction": i["direction"],
                 "weight": i["weight"], "status": "unchanged", "example": i["example"]}
                for l, i in s["phrases"].items()]
    evidence.sort(key=lambda e: -e["weight"])
    sums = _category_sums(s["phrases"])
    return {
        "stance": {"latest": s["score"], "previous": None, "shift": None,
                   "label_latest": s["label"], "label_previous": None, "shift_label": None},
        "decision": {"latest": detect_decision(text), "previous": None},
        "drivers": sorted([{"category": c, "name": CATEGORY_NAMES[c], "latest": v, "previous": None, "delta": None}
                           for c, v in sums.items()], key=lambda d: -abs(d["latest"])),
        "evidence": evidence,
        "diff": None,
    }
