# backend/utils/ncbi.py
"""Shared helpers for NCBI E-utilities (PubMed, ClinVar, and any future NCBI db).

Why this module exists:
  - PubMed and ClinVar used to duplicate the same params, URLs and parsing helpers.
  - Both services fire several esearch calls in parallel. When evidence_engine.py
    runs them together, the burst exceeds NCBI's rate limit and triggers HTTP 429.
    Every NCBI call now goes through ncbi_get(), which spaces requests out.
"""

import asyncio
import logging
import time

from backend.config import settings
from backend.utils.http_client import PUBMED_BASE_URL, get_json

logger = logging.getLogger(__name__)

# E-utilities endpoints (same host for every NCBI database)
ESEARCH_URL = f"{PUBMED_BASE_URL}/esearch.fcgi"
ESUMMARY_URL = f"{PUBMED_BASE_URL}/esummary.fcgi"

# Sent with every request so NCBI can identify us (recommended in their usage policy)
NCBI_TOOL = "TargetScope"

# NCBI limits requests PER SECOND: 3/s without an API key, 10/s with one.
# We keep a small safety margin under each limit.
_MAX_RATE = 9 if settings.pubmed_api_key else 2.5  # requests per second
_MIN_INTERVAL = 1.0 / _MAX_RATE  # seconds between two request starts

# Throttle state, shared by every coroutine in the process.
# The lock makes "read last start time -> wait -> update it" atomic,
# so two coroutines can never compute the same slot.
_throttle_lock = asyncio.Lock()
_last_request_start = 0.0


async def _throttle() -> None:
    """Wait until at least _MIN_INTERVAL has passed since the previous NCBI request.

    A Semaphore would only cap concurrent calls, not calls per second: with fast
    responses, 8 concurrent slots can still produce more than 10 requests/s.
    Spacing out request starts is what actually respects NCBI's limit.
    """
    global _last_request_start
    async with _throttle_lock:
        # monotonic(): immune to system clock changes, the right clock for intervals
        wait = _last_request_start + _MIN_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_start = time.monotonic()


def ncbi_params(db: str, **extra) -> dict:
    """Build E-utilities query parameters with identification fields.

    Args:
        db: NCBI database name, e.g. "pubmed" or "clinvar".
        **extra: Call-specific parameters (term, retmax, id, datetype...).

    Returns:
        dict: Parameters ready to pass to ncbi_get().
    """
    params = {"db": db, "retmode": "json", "tool": NCBI_TOOL, **extra}
    # email and api_key are optional: only sent when configured in .env
    if settings.contact_email:
        params["email"] = settings.contact_email
    if settings.pubmed_api_key:
        params["api_key"] = settings.pubmed_api_key
    return params


async def ncbi_get(url: str, params: dict, source: str) -> dict | None:
    """Rate-limited GET on an E-utilities endpoint.

    Retry, timeout and logging stay in http_client.get_json(); this function only
    adds the throttle in front of it.

    Args:
        url: ESEARCH_URL or ESUMMARY_URL.
        params: Output of ncbi_params().
        source: Log prefix, e.g. "PubMed" or "ClinVar".

    Returns:
        dict | None: Decoded JSON, or None if the request failed.
    """
    await _throttle()
    return await get_json(url, params=params, source=source)


def extract_count(data: dict | None) -> int | None:
    """Return the hit count of an esearch response.

    None means the request failed; 0 means NCBI answered "no result".
    evidence_engine.py relies on this difference: a failed call must not be
    reported as an evidence gap.
    """
    if data is None:
        return None
    return int(data.get("esearchresult", {}).get("count", 0))


def extract_ids(data: dict | None) -> list[str] | None:
    """Return the id list of an esearch response (None on failure, [] if empty)."""
    if data is None:
        return None
    return data.get("esearchresult", {}).get("idlist", [])
