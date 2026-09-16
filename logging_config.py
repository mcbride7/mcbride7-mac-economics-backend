"""
logging_config.py — one place to configure logging for the whole pipeline.

Every collector and run_all.py call setup_logging() once at import time.
Writes to both console (for interactive runs / cron log redirection) and a
rotating log file (so a VPS doesn't fill its disk with unbounded logs).
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("mac_economics")

    if logger.handlers:  # already configured (e.g. run_all.py imported a collector that also calls this)
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "ingestion.log"), maxBytes=5_000_000, backupCount=3
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def ping_healthcheck(suffix=""):
    """
    Dead-man's switch: pings Healthchecks.io (or any similar service —
    cron-job.org, Better Uptime, etc. all use the same "hit this URL on
    success" pattern) so you get alerted if a scheduled run stops
    happening entirely, not just if it errors while running.

    Set HEALTHCHECK_URL to your check's ping URL (free at healthchecks.io).
    No-ops silently if it's not configured — this is optional, not required.
    suffix: "" for success, "/fail" for an explicit failure ping,
            "/start" to signal the run has begun (lets Healthchecks.io flag
            runs that start but never finish, e.g. a crashed process).
    """
    import requests
    url = os.environ.get("HEALTHCHECK_URL", "").strip()
    if not url:
        return
    try:
        requests.get(f"{url}{suffix}", timeout=10)
    except requests.RequestException:
        pass  # never let monitoring itself break the pipeline
