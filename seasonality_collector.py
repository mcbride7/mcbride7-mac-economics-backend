"""
seasonality_collector.py — REAL, working seasonality computation from two
free historical data sources, replacing EquityClock (which sells seasonality
charts as its product and isn't scrapable).

Rather than scrape a chart, this computes seasonality directly: pull N years
of daily closes, group by calendar month, and calculate each month's average
return and the percentage of years it finished positive. This is arguably
better than scraping EquityClock — the lookback window and asset list are
fully under your control, and there's no ToS question since you're computing
from raw price data you're allowed to hold.

Two sources, matching the "two per category" brief:
  1. Stooq  (recommended)  — free daily CSV downloads, no API key, covers
     FX, indices, commodities, crypto. Used for spot/futures-style assets.
  2. FRED   (backup)       — reuses the data already ingested by
     fred_collector.py (US Treasury yields) to compute yield seasonality
     from the same local database, so it's a second source with zero extra
     network calls.

Setup:
    python seasonality_collector.py
(No API key needed for either source.)
"""

import csv
import io
import os
import sys
from collections import defaultdict
from datetime import datetime

import requests

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, upsert_source, insert_seasonality_stat, get_conn  # noqa: E402
from http_utils import get_with_retry  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

STOOQ_SYMBOLS = {
    "Gold": "xauusd",
    "WTI Crude": "cl.f",
    "EUR/USD": "eurusd",
    "S&P 500": "spx",
}


def fetch_stooq_history(stooq_symbol):
    """Stooq's free CSV export — daily OHLC, no key required."""
    url = f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"
    resp = get_with_retry(url, timeout=20, headers={"User-Agent": "MacEconomicsBot/1.0"})
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    if not rows or "Close" not in (rows[0].keys() if rows else []):
        return []
    return rows


def compute_monthly_seasonality(rows, years_back=15):
    """
    rows: list of dicts with 'Date' (YYYY-MM-DD) and 'Close' (str).
    Returns {month: {"avg_return_pct": x, "positive_rate_pct": y, "years": n}}
    computed from each month's first-close-to-last-close return per year.
    """
    by_year_month = defaultdict(list)
    for r in rows:
        try:
            d = datetime.strptime(r["Date"], "%Y-%m-%d")
            close = float(r["Close"])
        except (ValueError, KeyError):
            continue
        cutoff_year = datetime.now().year - years_back
        if d.year < cutoff_year:
            continue
        by_year_month[(d.year, d.month)].append((d, close))

    month_returns = defaultdict(list)
    for (year, month), points in by_year_month.items():
        points.sort(key=lambda p: p[0])
        if len(points) < 2:
            continue
        start_close, end_close = points[0][1], points[-1][1]
        if start_close == 0:
            continue
        ret_pct = ((end_close - start_close) / start_close) * 100
        month_returns[month].append(ret_pct)

    result = {}
    for month, returns in month_returns.items():
        avg = sum(returns) / len(returns)
        positive_rate = (sum(1 for r in returns if r > 0) / len(returns)) * 100
        result[month] = {
            "avg_return_pct": round(avg, 2),
            "positive_rate_pct": round(positive_rate, 1),
            "years": len(returns),
        }
    return result


def run_stooq():
    source_id = upsert_source(
        publisher="Stooq",
        url="https://stooq.com/",
        category="markets",
        reliability_tier=2,
        country="Global",
    )
    for name, symbol in STOOQ_SYMBOLS.items():
        try:
            rows = fetch_stooq_history(symbol)
        except requests.RequestException as e:
            logger.warning("Skipping %s: request failed after retries — %s", name, e)
            continue

        if not rows:
            logger.warning("Skipping %s: no data returned for symbol '%s' — verify it on stooq.com", name, symbol)
            continue

        stats = compute_monthly_seasonality(rows)
        for month, s in stats.items():
            insert_seasonality_stat(
                provider="stooq",
                symbol=name,
                month=month,
                avg_return_pct=s["avg_return_pct"],
                positive_rate_pct=s["positive_rate_pct"],
                years_sampled=s["years"],
                source_id=source_id,
            )
        logger.info("%s: seasonality computed for %d months (%s → %s)",
                    name, len(stats), rows[-1]['Date'] if rows else '?', rows[0]['Date'] if rows else '?')


def run_fred_yields():
    """
    Backup source: reuse whatever fred_collector.py already stored locally
    (run that collector first) to compute yield-change seasonality —
    a second, independent source with no additional network calls.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT reference_date, value FROM economic_releases "
        "WHERE indicator = 'US_10Y_YIELD' ORDER BY reference_date"
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        logger.warning("FRED backup skipped: no US_10Y_YIELD data found locally — run fred_collector.py first")
        return

    formatted = [{"Date": d, "Close": str(v)} for d, v in rows]
    stats = compute_monthly_seasonality(formatted, years_back=10)

    source_id = upsert_source(
        publisher="Federal Reserve Bank of St. Louis (FRED)",
        url="https://api.stlouisfed.org/fred/series/observations",
        category="statistics_agency",
        reliability_tier=1,
        country="United States",
    )
    for month, s in stats.items():
        insert_seasonality_stat(
            provider="fred",
            symbol="US 10Y Yield",
            month=month,
            avg_return_pct=s["avg_return_pct"],
            positive_rate_pct=s["positive_rate_pct"],
            years_sampled=s["years"],
            source_id=source_id,
        )
    logger.info("US 10Y Yield: seasonality computed for %d months from local FRED data", len(stats))


def run():
    init_db()
    logger.info("-- Stooq (primary) --")
    run_stooq()
    logger.info("-- FRED local data (backup source) --")
    run_fred_yields()
    logger.info("Seasonality computation complete.")


if __name__ == "__main__":
    run()
