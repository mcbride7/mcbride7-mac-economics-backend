"""
statement_text_collector.py — fetches the FULL TEXT of monetary-policy
statements so the Central Banks tab can compare them properly.

Why this exists: the RSS feeds only provide a title, link and (often very
short) summary. Comparing two one-line summaries tells you almost nothing;
comparing the real statements does. This reads each statement from the
central bank's own public page.

How it stays well-behaved (per the project's ingestion policy):
  * robots.txt is checked for every host and honoured. If a site disallows
    automated access, the statement is recorded as 'robots_disallowed' and
    never fetched — the analysis then falls back to the summary and says so.
  * Polite pacing (DELAY_SECONDS between requests) and a hard cap per run, so
    history back-fills gradually over successive scheduled runs.
  * Only public pages; no logins, no paywalls, no CAPTCHA handling.
  * Every attempt is recorded (statement_texts.status), so failures are not
    retried every run.

Statuses: ok | too_short | robots_disallowed | robots_unreachable |
          not_html | fetch_error

Setup: needs only DATABASE_URL (no API key). Schedule it after the RSS
collector, e.g. hourly at :15  ->  15 * * * *
"""

import os
import sys
import time
from collections import defaultdict
from html.parser import HTMLParser
from urllib import robotparser
from urllib.parse import urlparse

import requests

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import (  # noqa: E402
    init_db, get_statement_links_without_text, upsert_statement_text,
)
from http_utils import get_with_retry  # noqa: E402
from logging_config import setup_logging  # noqa: E402
from statement_analysis import select_statements  # noqa: E402

logger = setup_logging()

USER_AGENT = "MacEconomicsBot/1.0 (macro research; respects robots.txt)"
MAX_PER_BANK_PER_RUN = 8
MAX_FETCHES_PER_RUN = 30
DELAY_SECONDS = 1.5
MIN_CHARS = 400
MAX_CHARS = 40000


class _TextExtractor(HTMLParser):
    """Collects paragraph / list-item text, skipping page chrome."""

    SKIP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"}
    BLOCKS = {"p", "li", "h1", "h2", "h3", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.block_depth = 0
        self.current = []
        self.paragraphs = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif tag in self.BLOCKS and not self.skip_depth:
            self.block_depth += 1
        elif tag == "br" and self.block_depth:
            self.current.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCKS and self.block_depth:
            self.block_depth -= 1
            if self.block_depth == 0:
                text = " ".join("".join(self.current).split())
                self.current = []
                if text:
                    self.paragraphs.append(text)

    def handle_data(self, data):
        if self.block_depth and not self.skip_depth:
            self.current.append(data)


def extract_text(html):
    """HTML -> plain text of the main body. Drops very short fragments
    (menu items, 'Share', timestamps) unless they read like a sentence."""
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # malformed HTML must never crash a run
        pass
    kept, seen = [], set()
    for p in parser.paragraphs:
        if len(p) < 30 and not p.endswith("."):
            continue
        if p in seen:
            continue
        seen.add(p)
        kept.append(p)
    # Postgres rejects NUL bytes in text columns; one bad page must not crash a run.
    return "\n".join(kept).replace("\x00", "")[:MAX_CHARS]


class RobotsCache:
    """One robots.txt fetch per host per run. Follows the standard convention:
    404/other 4xx -> everything allowed; 5xx or unreachable -> not allowed this
    run (we don't guess when we can't read the rules)."""

    def __init__(self):
        self._cache = {}

    def allowed(self, url):
        parts = urlparse(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._cache:
            self._cache[host] = self._load(host)
        rp = self._cache[host]
        if rp is None:
            return False, "robots_unreachable"
        if rp == "allow_all":
            return True, None
        ok = rp.can_fetch(USER_AGENT, url)
        return ok, (None if ok else "robots_disallowed")

    @staticmethod
    def _load(host):
        try:
            resp = requests.get(f"{host}/robots.txt", timeout=10, headers={"User-Agent": USER_AGENT})
        except requests.RequestException:
            return None
        if resp.status_code == 200:
            rp = robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            return rp
        if 400 <= resp.status_code < 500:
            return "allow_all"
        return None


def fetch_statement(url, robots):
    """Returns (status, text_or_None)."""
    allowed, reason = robots.allowed(url)
    if not allowed:
        return reason, None
    try:
        resp = get_with_retry(url, timeout=20, headers={"User-Agent": USER_AGENT})
    except requests.RequestException as e:
        logger.warning("Fetch failed for %s: %s", url, e)
        return "fetch_error", None
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if ctype and "html" not in ctype:
        return "not_html", None
    text = extract_text(resp.text)
    if len(text) < MIN_CHARS:
        return "too_short", text or None
    return "ok", text


def pick_candidates(candidates):
    """candidates: untried policy_statements rows. Keeps only genuine monetary-
    policy statements, newest first, capped per bank."""
    by_bank = defaultdict(list)
    for c in candidates:
        by_bank[c["central_bank"]].append(c)
    picked = []
    for bank, rows in by_bank.items():
        picked += select_statements(bank, rows)[:MAX_PER_BANK_PER_RUN]
    return picked[:MAX_FETCHES_PER_RUN]


def run():
    init_db()
    candidates = get_statement_links_without_text(limit=600)
    todo = pick_candidates(candidates)
    if not todo:
        logger.info("Statement text: nothing new to fetch.")
        return

    robots = RobotsCache()
    counts = defaultdict(int)
    for i, row in enumerate(todo):
        status, text = fetch_statement(row["link"], robots)
        upsert_statement_text(row["link"], text if status in ("ok", "too_short") else None, status)
        counts[status] += 1
        logger.info("%s | %s | %s", row["central_bank"], status, row["link"])
        if i < len(todo) - 1:
            time.sleep(DELAY_SECONDS)
    logger.info("Statement text run complete: %s", dict(counts))


if __name__ == "__main__":
    run()
