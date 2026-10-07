"""
manual_statements.py — lets you paste a statement's text (or upload its PDF)
when the automatic fetch fails, so the analysis still works.

Typical reasons to use it:
  * the bank's site blocks automated access (robots.txt) so only a short feed
    summary was available -> the tab shows LOW confidence;
  * the bank has no usable RSS feed (RBNZ, PBOC, Banxico right now);
  * a new statement was released and you don't want to wait for the next run.

Two ways to use it:
  attach  -> put the full text on a statement that already exists in the list
  add     -> add a brand-new statement (needs a date; a title/URL are optional)

Pasted text goes through exactly the same analysis as fetched text. It is
stored with status 'manual' and shown as "Pasted by you", never passed off as
fetched. Anything added by hand can be removed again.

This module has no web-framework code, so it is fully unit-testable. The HTTP
layer (api/main.py) adds the admin-token check on top.
"""

import base64
import binascii
import hashlib
import hmac
import io
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import db
import statement_analysis as sa

MIN_CHARS = 80
SHORT_WARNING_CHARS = 400
MAX_TEXT_CHARS = 300_000
MAX_PDF_BYTES = 6 * 1024 * 1024
MAX_PDF_PAGES = 80
MIN_TOKEN_LEN = 16


def admin_token_state(expected, provided):
    """'disabled' -> no (or a too-weak) ADMIN_TOKEN is configured on the server,
    so manual updates are switched off; 'denied' -> wrong/missing token; 'ok'.
    Uses a constant-time comparison so the token can't be guessed by timing."""
    if not expected or len(expected) < MIN_TOKEN_LEN:
        return "disabled"
    if not provided or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return "denied"
    return "ok"


# ------------------------------------------------------------ text cleaning
def clean_pdf_text(raw):
    """PDF text comes out with a line break at the end of every printed line,
    hyphenated words split across lines, and page numbers. Rebuild readable
    paragraphs so sentences stay intact."""
    t = (raw or "").replace("\x00", "")
    t = re.sub(r"(\w)-\n([a-z])", r"\1\2", t)          # infla-\ntion -> inflation
    paragraphs = []
    for block in re.split(r"\n\s*\n", t):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        lines = [ln for ln in lines if not re.fullmatch(r"(page\s*)?\d{1,3}(\s*(of|/)\s*\d{1,3})?", ln, re.I)]
        if lines:
            paragraphs.append(" ".join(lines))
    return "\n".join(paragraphs)


def clean_pasted_text(raw):
    """Pasted web/Word text: normalise line endings and spacing, keep paragraphs."""
    t = (raw or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_pdf_text(pdf_base64):
    """base64 (optionally a data: URL) -> cleaned text. Raises ValueError with a
    message that is safe and useful to show the user."""
    if not pdf_base64:
        raise ValueError("No PDF data received.")
    data = pdf_base64.split(",", 1)[1] if pdf_base64.startswith("data:") else pdf_base64
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("The PDF upload was corrupted — please try again.")
    if len(raw) > MAX_PDF_BYTES:
        raise ValueError(f"That PDF is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB. Copy and paste the statement text instead.")
    if not raw.startswith(b"%PDF-"):
        raise ValueError("That file doesn't look like a PDF.")
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("PDF reading isn't available on the server (the 'pypdf' package is missing). "
                         "Copy and paste the statement text instead.")
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            # pypdf returns 0 (NOT_DECRYPTED) rather than raising when the empty password fails
            try:
                unlocked = int(reader.decrypt("")) != 0
            except Exception:  # noqa: BLE001
                unlocked = False
            if not unlocked:
                raise ValueError("That PDF is password-protected. Copy and paste the statement text instead.")
        pages = reader.pages[:MAX_PDF_PAGES]
        text = "\n\n".join((p.extract_text() or "") for p in pages)
    except ValueError:
        raise
    except Exception:  # noqa: BLE001
        raise ValueError("Couldn't read that PDF. Copy and paste the statement text instead.")
    text = clean_pdf_text(text)
    if len(text) < MIN_CHARS:
        raise ValueError("That PDF has no selectable text (it is probably a scan or image). "
                         "Copy the text from the bank's web page and paste it instead.")
    return text


# --------------------------------------------------------------- validation
def _validate_text(text):
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"That text is too long ({len(text):,} characters; the limit is {MAX_TEXT_CHARS:,}). "
                         f"Paste just the policy statement.")
    if len(text) < MIN_CHARS:
        raise ValueError(f"That text is too short ({len(text)} characters). Paste the full statement.")
    if len(re.findall(r"[A-Za-z]{3,}", text)) < 12:
        raise ValueError("That doesn't look like statement text. Paste the full statement.")


def _normalise_title(title, bank_name):
    t = re.sub(r"\s+", " ", (title or "")).strip()[:200]
    if not t:
        return f"{bank_name} monetary policy statement (added manually)"
    # keep it recognisable as a policy statement everywhere in the UI
    if not any(p in t.lower() for p in sa.INCLUDE_PATTERNS):
        t = f"Monetary policy statement — {t}"
    return t


def _validate_date(date_str):
    if not date_str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_str).strip()):
        raise ValueError("Enter the statement's date as YYYY-MM-DD (for example 2025-09-17).")
    dt = sa.parse_pub_date(str(date_str).strip())
    if dt is None:
        raise ValueError("That date isn't valid. Use YYYY-MM-DD.")
    now = datetime.now(timezone.utc)
    if dt > now + timedelta(days=2):
        raise ValueError("That date is in the future.")
    if dt.year < 2000:
        raise ValueError("That date is too far in the past.")
    return str(date_str).strip()


def _validate_url(url):
    u = (url or "").strip()
    if not u:
        return None
    p = urlparse(u)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("The source link must start with http:// or https://")
    return u[:500]


def preview(text):
    """What the analysis makes of this text — shown right after saving so a
    wrong paste (an article, a speech) is obvious immediately."""
    s = sa.score_text(text)
    return {"tone": s["label"], "score": s["score"], "phrases_found": len(s["phrases"]),
            "decision": sa.detect_decision(text)["label"]}


# --------------------------------------------------------------- operations
def save_manual_statement(code, bank_name, text=None, pdf_base64=None, attach_to_link=None,
                          title=None, date=None, url=None):
    """Saves pasted/PDF text. Returns a dict describing what happened. Raises
    ValueError (safe to show the user) on any problem."""
    if pdf_base64:
        body, via = extract_pdf_text(pdf_base64), "pdf"
    else:
        body, via = clean_pasted_text(text), "pasted"
    _validate_text(body)

    warnings = []
    if len(body) < SHORT_WARNING_CHARS:
        warnings.append("That text is quite short — a full statement is usually over 1,000 characters, "
                        "so the analysis may be unreliable.")
    pv = preview(body)
    if pv["phrases_found"] == 0:
        warnings.append("No hawkish or dovish wording was found in that text. Check it is the policy statement "
                        "and not an article, speech or press-conference Q&A.")

    # ---- attach to an existing statement
    link = (attach_to_link or "").strip() or None
    if link:
        if not db.statement_belongs_to_bank(code, link):
            raise ValueError("That statement isn't in this bank's list. Refresh the page and try again.")
        db.upsert_statement_text(link, body, "manual")
        return {"mode": "attached", "link": link, "chars": len(body), "source": via,
                "warnings": warnings, "preview": pv}

    # ---- add as a new statement (a source link that already exists is treated as 'attach')
    date_s = _validate_date(date)
    url_s = _validate_url(url)
    if url_s and db.statement_belongs_to_bank(code, url_s):
        db.upsert_statement_text(url_s, body, "manual")
        return {"mode": "attached", "link": url_s, "chars": len(body), "source": via,
                "warnings": warnings, "preview": pv}

    link = url_s or f"manual://{code}/{hashlib.sha1(body.encode('utf-8')).hexdigest()[:16]}"
    db.insert_manual_statement(code, _normalise_title(title, bank_name), date_s, link)
    db.upsert_statement_text(link, body, "manual")
    return {"mode": "added", "link": link, "chars": len(body), "source": via,
            "warnings": warnings, "preview": pv}


def remove_manual(code, link):
    """Removes something the user added: a hand-entered statement (deleted
    entirely) or pasted text on a fetched statement (reverted to automatic).
    Never deletes ingested statements. Returns 'deleted' | 'reverted' | None."""
    link = (link or "").strip()
    if not link:
        return None
    if db.delete_manual_statement(code, link):
        return "deleted"
    if db.statement_belongs_to_bank(code, link) and db.delete_manual_text(link):
        return "reverted"
    return None
