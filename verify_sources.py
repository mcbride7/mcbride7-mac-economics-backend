"""
verify_sources.py — cross-checks two-or-more providers that supply the SAME
data point, before treating the number as trustworthy.

This is exactly the "verify with another website before putting the data
in" you asked for. It runs AFTER the individual collectors (Finnhub,
Twelve Data) have already written their own rows to market_quotes — it
doesn't block ingestion (a slow/down provider shouldn't take the other one
down with it), it compares what's already there and logs the verdict.

How it decides "agree" vs "conflict":
    diff_pct = abs(value_a - value_b) / ((value_a + value_b) / 2) * 100
    Within TOLERANCE_PCT of each other -> "agree"
    Beyond it                          -> "conflict" (logged, not thrown away —
                                           see get_recent_conflicts() in db.py)

Every symbol pair gets its own tolerance, because the "normal" amount FX
quotes disagree by (a few pips) is nothing like the amount two crypto
sources can legitimately disagree by (crypto moves fast between two
providers' quote timestamps).
"""

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, get_conn, insert_data_conflict, _q  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

# Only symbols where TWO OR MORE providers actually supply the same thing
# are worth checking — this maps each shared symbol to how much disagreement
# is tolerable before it's flagged.
TOLERANCES_PCT = {
    "EUR/USD": 0.3, "GBP/USD": 0.3, "USD/JPY": 0.3, "USD/CAD": 0.3,
    "AUD/USD": 0.3, "NZD/USD": 0.3, "USD/ZAR": 0.5, "USD/MXN": 0.5, "USD/CHF": 0.3,
    "Gold": 0.5, "Silver": 0.7, "WTI Crude": 0.8,
    "S&P 500": 0.3, "Nasdaq 100": 0.3,
    "Bitcoin": 1.0,  # crypto moves fastest between two providers' timestamps
}


def get_latest_quotes_by_provider(symbol):
    """Returns {provider: (price, quote_time)} for the most recent quote
    each provider has for this symbol."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        _q("SELECT provider, price, quote_time FROM market_quotes "
           "WHERE symbol = ? ORDER BY quote_time DESC"),
        (symbol,),
    )
    rows = cur.fetchall()
    conn.close()
    latest = {}
    for provider, price, quote_time in rows:
        if provider not in latest:  # first row per provider = most recent, since we ORDER BY DESC
            latest[provider] = (price, quote_time)
    return latest


def compare(symbol, tolerance_pct):
    latest = get_latest_quotes_by_provider(symbol)
    if len(latest) < 2:
        return None  # only one provider has this symbol — nothing to cross-check yet

    providers = sorted(latest.keys())
    results = []
    for i in range(len(providers)):
        for j in range(i + 1, len(providers)):
            a, b = providers[i], providers[j]
            value_a, _ = latest[a]
            value_b, _ = latest[b]
            if value_a is None or value_b is None:
                continue
            avg = (value_a + value_b) / 2
            diff_pct = abs(value_a - value_b) / avg * 100 if avg else 0
            status = "agree" if diff_pct <= tolerance_pct else "conflict"
            results.append({
                "symbol": symbol, "provider_a": a, "value_a": value_a,
                "provider_b": b, "value_b": value_b,
                "diff_pct": round(diff_pct, 4), "tolerance_pct": tolerance_pct, "status": status,
            })
    return results


def run():
    init_db()
    checked, agreements, conflicts = 0, 0, 0

    for symbol, tolerance in TOLERANCES_PCT.items():
        results = compare(symbol, tolerance)
        if not results:
            continue
        for r in results:
            insert_data_conflict(
                r["symbol"], r["provider_a"], r["value_a"], r["provider_b"], r["value_b"],
                r["diff_pct"], r["tolerance_pct"], r["status"],
            )
            checked += 1
            if r["status"] == "agree":
                agreements += 1
                logger.info("%s: %s vs %s agree (%.3f%% apart, tolerance %.1f%%)",
                            symbol, r["provider_a"], r["provider_b"], r["diff_pct"], tolerance)
            else:
                conflicts += 1
                logger.warning("CONFLICT on %s: %s=%.5f vs %s=%.5f (%.3f%% apart, tolerance %.1f%%)",
                                symbol, r["provider_a"], r["value_a"], r["provider_b"], r["value_b"],
                                r["diff_pct"], tolerance)

    logger.info("Cross-source verification complete: %d checked, %d agreed, %d conflicts", checked, agreements, conflicts)
    return {"checked": checked, "agreements": agreements, "conflicts": conflicts}


if __name__ == "__main__":
    run()
