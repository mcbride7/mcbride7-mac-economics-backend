"""
central_bank_rss_collector.py — REAL, working RSS ingestion for central-bank
statements and speeches.

Design: one generic collector function + a config dict of feeds, so adding
a bank is a one-line addition once you have its real feed URL — no new
scraping logic needed. This is deliberately "RSS-first": every entry below
is the bank's own official syndication feed, so there's no robots.txt /
ToS question and no HTML parsing to break when a site redesigns.

STATUS OF EACH FEED:
  ✅ CONFIRMED  — I fetched this URL directly and verified it returns
                  ECB/Fed content during this session.
  ⚠️  TO VERIFY — a real feed almost certainly exists at each of these
                  institutions (most central banks publish RSS), but I have
                  not fetched and confirmed the exact URL. Visit the bank's
                  own "RSS" / "Subscribe" / "Newsroom" page, copy the real
                  link, and swap it in before relying on it. Running this
                  script with an unverified URL will simply fail loudly
                  (feedparser returns zero entries / a bozo flag) rather
                  than silently ingesting garbage — see run() below.

Central banks with confirmed working feeds ship immediately usable;
the rest are wired up and waiting for a URL swap.
"""

import os
import sys
from datetime import datetime, timezone

import feedparser
import requests

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, upsert_source, insert_policy_statement  # noqa: E402
from http_utils import get_with_retry  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

FEEDS = {
    "FED": {
        "status": "confirmed",
        "publisher": "Federal Reserve Board",
        "country": "United States",
        "feeds": {
            "monetary_policy_press": "https://www.federalreserve.gov/feeds/press_monetary.xml",
            "speeches_testimony": "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml",
        },
    },
    "ECB": {
        "status": "confirmed",
        "publisher": "European Central Bank",
        "country": "Euro Area",
        "feeds": {
            "press_speeches": "https://www.ecb.europa.eu/rss/press.html",
        },
    },
    # --- TO VERIFY before relying on these in production ---------------
    "BOE": {
        "status": "to_verify",
        "publisher": "Bank of England",
        "country": "United Kingdom",
        "feeds": {
            "news": "https://www.bankofengland.co.uk/rss/news",  # verify on bankofengland.co.uk
        },
    },
    "BOJ": {
        "status": "to_verify",
        "publisher": "Bank of Japan",
        "country": "Japan",
        "feeds": {
            "announcements": "https://www.boj.or.jp/en/rss/whatsnew.xml",  # verify on boj.or.jp/en
        },
    },
    "RBA": {
        "status": "to_verify",
        "publisher": "Reserve Bank of Australia",
        "country": "Australia",
        "feeds": {
            "media_releases": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",  # verify on rba.gov.au
        },
    },
    "BOC": {
        "status": "to_verify",
        "publisher": "Bank of Canada",
        "country": "Canada",
        "feeds": {
            "press": "https://www.bankofcanada.ca/feed/",  # verify on bankofcanada.ca
        },
    },
    "SNB": {
        "status": "to_verify",
        "publisher": "Swiss National Bank",
        "country": "Switzerland",
        "feeds": {
            "press": "https://www.snb.ch/public/en/rss/news",  # verify on snb.ch/en
        },
    },
    "RBNZ": {
        "status": "to_verify",
        "publisher": "Reserve Bank of New Zealand",
        "country": "New Zealand",
        "feeds": {
            # NOTE: the user-supplied bnz.co.nz is "Bank of New Zealand," a commercial
            # bank — NOT the central bank. The real central bank is rbnz.govt.nz.
            "media_releases": "https://www.rbnz.govt.nz/-/media/rss/media-releases",  # verify on rbnz.govt.nz
        },
    },
    "SARB": {
        "status": "to_verify",
        "publisher": "South African Reserve Bank",
        "country": "South Africa",
        "feeds": {
            "press": "https://www.resbank.co.za/en/home/rss",  # verify on resbank.co.za
        },
    },
    "PBOC": {
        "status": "to_verify",
        "publisher": "People's Bank of China",
        "country": "China",
        "feeds": {
            # PBOC's English site has historically been HTML-only with no public RSS —
            # confirm on pbc.gov.cn/en; if none exists, treat statements as a
            # document_collector target (PDF/HTML extraction) instead of RSS.
            "news": "https://www.pbc.gov.cn/en/3688006/index.html",
        },
    },
    "BANXICO": {
        "status": "to_verify",
        "publisher": "Banco de México",
        "country": "Mexico",
        "feeds": {
            "press": "https://www.banxico.org.mx/rss/rss-boletines.html",  # verify on banxico.org.mx
        },
    },
}


def run(only_confirmed=True):
    """
    only_confirmed=True (default): only pulls feeds marked "confirmed" above,
    so out-of-the-box this script does something real and correct.
    Set to False once you've verified and corrected the "to_verify" URLs.
    """
    init_db()

    for bank_code, cfg in FEEDS.items():
        if only_confirmed and cfg["status"] != "confirmed":
            logger.info("Skipping %s: marked to_verify — see comments in this file", bank_code)
            continue

        for feed_name, url in cfg["feeds"].items():
            source_id = upsert_source(
                publisher=cfg["publisher"],
                url=url,
                category="central_bank",
                reliability_tier=1,
                country=cfg["country"],
            )
            try:
                # Use requests (with retry/backoff) first so we get real HTTP errors
                # instead of feedparser silently swallowing a 403/404 into an empty feed.
                resp = get_with_retry(url, timeout=20, headers={"User-Agent": "MacEconomicsBot/1.0"})
                parsed = feedparser.parse(resp.content)
            except requests.RequestException as e:
                logger.error("FAILED %s/%s: %s", bank_code, feed_name, e)
                continue

            if parsed.bozo and not parsed.entries:
                logger.error("FAILED %s/%s: not a valid feed at this URL — verify it", bank_code, feed_name)
                continue

            new_count = 0
            for entry in parsed.entries[:50]:
                published = entry.get("published", "") or entry.get("updated", "")
                inserted = insert_policy_statement(
                    central_bank=bank_code,
                    title=entry.get("title", "").strip(),
                    published_at=published,
                    link=entry.get("link", ""),
                    summary=entry.get("summary", "")[:2000],
                    source_id=source_id,
                )
                if inserted:
                    new_count += 1
            logger.info("%s/%s: %d new statements ingested (%d in feed)",
                        bank_code, feed_name, new_count, len(parsed.entries))

    logger.info("Central bank RSS ingestion complete.")


if __name__ == "__main__":
    run(only_confirmed=True)
