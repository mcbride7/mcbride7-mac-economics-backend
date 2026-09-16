"""
http_utils.py — shared retry/backoff/timeout wrapper for every collector.

Replaces the bare `requests.get(...)` calls that had no retry logic. On a
transient failure (timeout, connection reset, 429, 5xx), retries with
exponential backoff before giving up — instead of a single scheduled run
just silently failing because a provider had a one-second blip.

No new dependency (no `tenacity`) — this is intentionally dependency-light
so it works with exactly what's already in requirements.txt.
"""

import logging
import time

import requests

logger = logging.getLogger("mac_economics")

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def get_with_retry(url, params=None, headers=None, timeout=20, max_retries=3, backoff_base=2):
    """
    Drop-in replacement for requests.get(...) with:
      - an explicit timeout (the original collectors already had this per-call;
        centralizing it here means every collector gets the same default)
      - retry with exponential backoff (1x, 2x, 4x backoff_base seconds) on
        timeouts, connection errors, and retryable HTTP status codes
      - a 429 response's Retry-After header is honored if present, instead of
        guessing the backoff

    Raises requests.RequestException on final failure, same as a plain
    requests.get(...).raise_for_status() would — callers' existing
    try/except requests.RequestException blocks need no changes.
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)

            if resp.status_code in RETRYABLE_STATUS_CODES:
                wait = backoff_base ** attempt
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = float(retry_after)
                    except ValueError:
                        pass
                if attempt < max_retries:
                    logger.warning(
                        "Retryable HTTP %s from %s (attempt %d/%d) — waiting %.1fs",
                        resp.status_code, url, attempt + 1, max_retries + 1, wait,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()  # exhausted retries — raise the final error

            resp.raise_for_status()
            return resp

        except (requests.Timeout, requests.ConnectionError) as e:
            last_exception = e
            wait = backoff_base ** attempt
            if attempt < max_retries:
                logger.warning(
                    "%s on %s (attempt %d/%d) — waiting %.1fs",
                    type(e).__name__, url, attempt + 1, max_retries + 1, wait,
                )
                time.sleep(wait)
                continue
            raise

    # Should be unreachable, but keeps the function's contract explicit.
    if last_exception:
        raise last_exception
