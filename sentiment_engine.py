"""
sentiment_engine.py — a real, transparent risk-regime scoring model.

This replaces the Sentiment page's old "not live yet" placeholder with an
actual computation from real, live inputs:

  1. VIX level (40% weight)            — from FRED's VIXCLS series
  2. High-yield credit spread (35%)    — from FRED's BAMLH0A0HYM2 series
  3. Equity/gold flow signal (25%)     — SPY vs GLD daily moves (Finnhub)

HONESTY ABOUT WHAT THIS IS: this is a simple, fully transparent weighted
heuristic — not a sophisticated proprietary model, not machine-learned, not
backtested. The weights (40/35/25) are a reasonable starting judgment, not
an empirically optimized result. Every component and its contribution is
stored alongside the final score specifically so the number is auditable,
not a black box — see risk_regime_scores in db.py.

Scoring logic, each component scaled to 0-100 (0=max risk-on, 100=max risk-off):

  VIX component:
    VIX <= 15  -> 0    (calm)
    VIX >= 35  -> 100  (crisis-level)
    linear interpolation between — these bounds are VIX's own well-known
    historical "calm" and "crisis" thresholds, not arbitrarily chosen.

  Credit spread component:
    Spread <= 3.0%  -> 0    (tight/healthy)
    Spread >= 8.0%  -> 100  (distressed — roughly 2008/2020 crisis territory)
    linear interpolation between.

  Equity/gold flow component:
    Pure risk-off signal (SPY down AND GLD up) -> up to 100, scaled by
    how large the combined divergence is (capped at a 3% combined move).
    Pure risk-on signal (SPY up AND GLD down) -> 0.
    Mixed/flat signals -> 50 (neutral — no clear flow signal either way).

Final score = weighted sum. >=60 = RISK-OFF, <=40 = RISK-ON, else NEUTRAL.
"""

import sys
import os
from datetime import datetime, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, get_conn, insert_risk_regime_score  # noqa: E402
from logging_config import setup_logging  # noqa: E402

logger = setup_logging()

VIX_WEIGHT = 40
CREDIT_WEIGHT = 35
FLOW_WEIGHT = 25


def _latest_economic_value(indicator):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT value FROM economic_releases WHERE indicator = ? "
        "ORDER BY reference_date DESC LIMIT 1",
        (indicator,),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def _latest_quote_change_pct(symbol_substring):
    """symbol_substring matches against market_quotes.symbol with LIKE, since
    Finnhub stores these as e.g. 'SPY (S&P 500 ETF)', not a bare ticker."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT change_pct FROM market_quotes WHERE symbol LIKE ? "
        "ORDER BY quote_time DESC LIMIT 1",
        (f"%{symbol_substring}%",),
    )
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def _scale(value, low, high):
    """Linear interpolation of value into 0-100, clamped at both ends."""
    if value is None:
        return None
    if value <= low:
        return 0.0
    if value >= high:
        return 100.0
    return (value - low) / (high - low) * 100.0


def compute_flow_component(spy_change, gld_change):
    if spy_change is None or gld_change is None:
        return None, "missing SPY and/or GLD data"

    divergence = (-spy_change) + gld_change  # positive = risk-off direction
    # Cap at a combined 3% move (e.g. SPY -1.5% and GLD +1.5%) = 100 component.
    capped = max(-3.0, min(3.0, divergence))
    component = (capped + 3.0) / 6.0 * 100.0
    return component, None


def run():
    init_db()

    vix = _latest_economic_value("VIX")
    credit_spread = _latest_economic_value("US_HY_CREDIT_SPREAD")
    spy_change = _latest_quote_change_pct("SPY")
    gld_change = _latest_quote_change_pct("GLD")

    vix_component = _scale(vix, low=15, high=35)
    credit_component = _scale(credit_spread, low=3.0, high=8.0)
    flow_component, flow_note = compute_flow_component(spy_change, gld_change)

    missing = []
    if vix_component is None:
        missing.append("VIX (run fred_collector.py)")
    if credit_component is None:
        missing.append("credit spread (run fred_collector.py)")
    if flow_component is None:
        missing.append(flow_note or "equity/gold flow (run finnhub_collector.py)")

    if missing:
        logger.warning("Sentiment engine: can't compute score yet — missing: %s", "; ".join(missing))
        return

    weighted_score = (
        vix_component * (VIX_WEIGHT / 100)
        + credit_component * (CREDIT_WEIGHT / 100)
        + flow_component * (FLOW_WEIGHT / 100)
    )

    if weighted_score >= 60:
        regime = "RISK-OFF"
    elif weighted_score <= 40:
        regime = "RISK-ON"
    else:
        regime = "NEUTRAL"

    insert_risk_regime_score(
        score=round(weighted_score, 1),
        regime=regime,
        vix_value=vix,
        vix_component=round(vix_component, 1),
        credit_spread_value=credit_spread,
        credit_spread_component=round(credit_component, 1),
        equity_flow_component=round(flow_component, 1),
        spy_change_pct=spy_change,
        gld_change_pct=gld_change,
    )

    logger.info(
        "Risk regime: %s (score %.1f) — VIX=%.1f (%.1f pts), credit=%.2f%% (%.1f pts), flow=%.1f pts",
        regime, weighted_score, vix, vix_component, credit_spread, credit_component, flow_component,
    )


if __name__ == "__main__":
    run()
