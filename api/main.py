"""
api/main.py — Phase 2: the missing link between the ingestion pipeline
and the dashboard. Serves what's actually in the database as JSON.

I could not install/run FastAPI in the sandbox that wrote this (no
internet access there to `pip install`), so this file is NOT verified by
execution the way db.py's functions are — I tested the read functions it
calls directly (see db.py's get_* functions, confirmed working above).
The FastAPI code itself follows the standard, well-documented pattern
exactly — run the two commands below to confirm it in your own environment
in under a minute:

    pip install fastapi uvicorn
    uvicorn api.main:app --reload --port 8000

Then open http://localhost:8000/docs — FastAPI auto-generates an
interactive test page for every endpoint below.
"""

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI, HTTPException, Query, Body, Request, Header
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import db
import bank_analysis_service
import manual_statements
import logging
from api.connection_check import check_provider, PROVIDER_URLS

# Phase 6: basic rate limiting on your own API, independent of whatever
# limits the upstream providers (Finnhub, FRED, etc.) impose on ingestion.
# This protects your server from being hammered by a client, not from the
# ingestion side — those limits are handled inside each collector instead.
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Mac Economics API", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Allow the dashboard (served from a different origin during local dev,
# e.g. file:// or a Vite/static dev server) to call this API.
# Tighten this to your real frontend domain before going live.
MAX_BODY_BYTES = 10 * 1024 * 1024   # PDFs arrive base64-encoded inside JSON


async def limit_request_size(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request body too large (limit 10 MB)."}, status_code=413)
    return await call_next(request)


# Registered before CORSMiddleware on purpose: the last-added middleware is the outermost.
app.middleware("http")(limit_request_size)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # TODO: restrict to your deployed frontend URL
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/economic-data/{indicator}")
@limiter.limit("60/minute")
def economic_data(request: Request, indicator: str, limit: int = Query(50, le=500)):
    rows = db.get_economic_releases(indicator=indicator, limit=limit)
    if not rows:
        raise HTTPException(status_code=404, detail=f"No data for indicator '{indicator}'")
    return rows


@app.get("/api/economic-data/recent/all")
@limiter.limit("60/minute")
def economic_data_recent(request: Request):
    """Latest value per distinct indicator — powers the Calendar page's
    'Recent Releases' view without the frontend needing to know indicator
    names ahead of time."""
    return db.get_recent_releases_all()


@app.get("/api/calendar/upcoming")
@limiter.limit("60/minute")
def calendar_upcoming(request: Request, limit: int = Query(50, le=200)):
    """Real forward-looking release dates from fred_release_calendar_collector.py.
    from_date is today, computed server-side so the frontend never has to
    reason about timezones."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    return db.get_upcoming_releases(from_date=today, limit=limit)


@app.get("/api/sentiment/latest")
@limiter.limit("60/minute")
def sentiment_latest(request: Request):
    """Real risk-regime score from sentiment_engine.py. Returns null fields
    if no score has been computed yet (e.g. inputs still missing) rather
    than a fake default."""
    result = db.get_latest_risk_regime_score()
    if not result:
        raise HTTPException(status_code=404, detail="No risk regime score computed yet")
    return result


@app.get("/api/data-quality/conflicts")
@limiter.limit("60/minute")
def data_conflicts(request: Request, limit: int = Query(30, le=100)):
    """Cross-source verification log from verify_sources.py — every time
    two providers were compared for the same symbol, agree or conflict."""
    return db.get_recent_conflicts(limit=limit)


@app.get("/api/central-banks/{code}/analysis")
@limiter.limit("30/minute")
def central_bank_analysis(request: Request, code: str, compare: int = Query(1, ge=1, le=12)):
    """Latest-vs-previous monetary policy statement analysis for one bank:
    sentence/word diff, hawkish-dovish tone, detected decision, evidence,
    market implications, near-term expectation and a confidence rating.

    Any failure is converted to an HTTPException on purpose: an *unhandled*
    exception bypasses CORSMiddleware, so the browser would report a
    misleading 'blocked by CORS policy' instead of the real problem."""
    try:
        result = bank_analysis_service.build_bank_analysis(code, compare=compare)
        result["manual_updates_enabled"] = (
            manual_statements.admin_token_state(os.environ.get("ADMIN_TOKEN", ""), "x") != "disabled")
        return result
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown central bank '{code}'")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("mac_economics").exception("analysis failed for %s", code)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {type(exc).__name__}")


def _require_admin(token):
    state = manual_statements.admin_token_state(os.environ.get("ADMIN_TOKEN", ""), token)
    if state == "disabled":
        raise HTTPException(status_code=503, detail=(
            "Manual updates are switched off. Set an ADMIN_TOKEN (at least 16 characters) on your backend "
            "service in Railway, then redeploy."))
    if state == "denied":
        raise HTTPException(status_code=401, detail="Wrong admin token.")


def _opt_str(payload, key):
    v = payload.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"'{key}' must be text.")
    return v


@app.post("/api/central-banks/{code}/manual-statement")
@limiter.limit("10/minute")
def manual_statement(request: Request, code: str, payload: dict = Body(...),
                     x_admin_token: str | None = Header(None)):
    """Paste statement text (or upload a PDF, base64-encoded) when automatic
    fetching failed. Requires the X-Admin-Token header. Writes to the database,
    which is why it is locked: the API address is public."""
    _require_admin(x_admin_token)
    code = code.upper()
    meta = bank_analysis_service.BANK_META.get(code)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Unknown central bank '{code}'")
    try:
        return manual_statements.save_manual_statement(
            code, meta["name"],
            text=_opt_str(payload, "text"), pdf_base64=_opt_str(payload, "pdf_base64"),
            attach_to_link=_opt_str(payload, "attach_to_link"),
            title=_opt_str(payload, "title"), date=_opt_str(payload, "date"), url=_opt_str(payload, "url"))
    except ValueError as exc:               # our own messages, safe to show
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("mac_economics").exception("manual statement failed for %s", code)
        raise HTTPException(status_code=500, detail=f"Saving failed: {type(exc).__name__}")


@app.post("/api/central-banks/{code}/manual-statement/remove")
@limiter.limit("10/minute")
def manual_statement_remove(request: Request, code: str, payload: dict = Body(...),
                            x_admin_token: str | None = Header(None)):
    """Removes something you added by hand. Never deletes fetched statements."""
    _require_admin(x_admin_token)
    code = code.upper()
    if code not in bank_analysis_service.BANK_META:
        raise HTTPException(status_code=404, detail=f"Unknown central bank '{code}'")
    try:
        outcome = manual_statements.remove_manual(code, _opt_str(payload, "link"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("mac_economics").exception("manual remove failed for %s", code)
        raise HTTPException(status_code=500, detail=f"Removing failed: {type(exc).__name__}")
    if outcome is None:
        raise HTTPException(status_code=404, detail="Nothing you added was found for that statement.")
    return {"result": outcome}


@app.get("/api/central-banks/{code}/statements")
@limiter.limit("60/minute")
def central_bank_statements(request: Request, code: str, limit: int = Query(20, le=100)):
    rows = db.get_policy_statements(central_bank=code.upper(), limit=limit)
    return rows  # empty list is a valid response — bank just has no ingested statements yet


@app.get("/api/markets")
@limiter.limit("60/minute")
def markets(request: Request, asset_class: str | None = Query(None)):
    return db.get_market_quotes(asset_class=asset_class)


@app.get("/api/news")
@limiter.limit("60/minute")
def news(request: Request, limit: int = Query(20, le=100)):
    return db.get_news(limit=limit)


@app.get("/api/positioning/cot")
@limiter.limit("60/minute")
def cot(request: Request, market: str | None = Query(None, description="e.g. 'GOLD - COMMODITY EXCHANGE INC.'")):
    return db.get_cot_reports(market_and_exchange=market)


@app.get("/api/seasonality")
@limiter.limit("60/minute")
def seasonality(request: Request, symbol: str | None = Query(None)):
    return db.get_seasonality(symbol=symbol)


@app.get("/api/health")
def health():
    """Simple liveness check — point your host's health-check config at this."""
    return {"status": "ok"}


@app.post("/api/test-connection")
@limiter.limit("30/minute")
def test_connection(request: Request, payload: dict = Body(...)):
    """
    THE FIX FOR THE 'Blocked' CORS PROBLEM.

    The dashboard's API Settings page calls this endpoint instead of
    calling Finnhub/FRED/Twelve Data/NewsData.io directly from the
    browser. Because this request runs server-side (via `requests`,
    not the browser's `fetch`), CORS never enters the picture — CORS is
    a browser-only restriction on cross-origin `fetch`/`XMLHttpRequest`
    calls; it does not apply to server-to-server HTTP requests at all.

    Body: {"provider": "fred" | "finnhub" | "twelvedata" | "newsdata", "key": "..."}
    Returns the same {status, message} shape the frontend already renders,
    so no other UI code needs to change — only which URL it calls.
    """
    provider = payload.get("provider", "")
    key = payload.get("key", "")
    if provider not in PROVIDER_URLS:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider}'")
    return check_provider(provider, key)
