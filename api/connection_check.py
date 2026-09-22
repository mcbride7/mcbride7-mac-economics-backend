"""
connection_check.py — server-side version of the provider status check.

This is the piece that actually solves the CORS problem: it runs on your
server (via requests, not the browser's fetch), so there's no CORS policy
involved at all — CORS only governs browser-to-third-party calls, never
server-to-third-party calls. The dashboard's "Test" button now calls this
through your own API instead of hitting Finnhub/FRED/etc. directly.
"""

import requests

PROVIDER_URLS = {
    "fred": lambda key: f"https://api.stlouisfed.org/fred/series?series_id=DGS10&api_key={key}&file_type=json",
    "finnhub": lambda key: f"https://finnhub.io/api/v1/quote?symbol=OANDA:EUR_USD&token={key}",
    "twelvedata": lambda key: f"https://api.twelvedata.com/price?symbol=EUR/USD&apikey={key}",
    "newsdata": lambda key: f"https://newsdata.io/api/1/latest?apikey={key}&q=test&language=en",
}


def _validate(provider, data):
    if provider == "fred":
        return isinstance(data.get("seriess"), list) and len(data["seriess"]) > 0
    if provider == "finnhub":
        return isinstance(data.get("c"), (int, float)) and data["c"] > 0
    if provider == "twelvedata":
        return "price" in data and "code" not in data
    if provider == "newsdata":
        return data.get("status") == "success"
    return False


def check_provider(provider, key):
    """Returns the same {status, message} shape the frontend already expects."""
    if provider not in PROVIDER_URLS:
        return {"status": "error", "message": f"Unknown provider '{provider}'"}
    if not key or not key.strip():
        return {"status": "unconfigured", "message": "No key entered"}

    try:
        resp = requests.get(PROVIDER_URLS[provider](key), timeout=10)
    except requests.RequestException as e:
        return {"status": "error", "message": f"Request failed: {e}"}

    if resp.status_code != 200:
        return {"status": "error", "message": f"HTTP {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError:
        return {"status": "error", "message": "Provider returned non-JSON response"}

    if _validate(provider, data):
        return {"status": "connected", "message": "Live"}
    return {
        "status": "error",
        "message": data.get("error_message") or data.get("message") or "Unexpected response shape",
    }
