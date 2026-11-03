# backend/utils/http_client.py
"""Shared async HTTP client and JSON helper for all TargetScope data-source services.

Every service calls `await get_json()` instead of using httpx directly, so retry,
timeout, rate-limit and logging behaviour is identical across sources.
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

import httpx

logger = logging.getLogger(__name__)

# APIs URLs
UNIPROT_BASE_URL = "https://rest.uniprot.org"
MYGENE_BASE_URL = "https://mygene.info/v3"
PUBMED_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OPENALEX_BASE_URL = "https://api.openalex.org"
CHEMBL_BASE_URL = "https://www.ebi.ac.uk/chembl/api/data"
HPA_BASE_URL = "https://www.proteinatlas.org"
CLINVAR_BASE_URL = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"  # same host as PubMed
)
COSMIC_BASE_URL = (
    "https://cancer.sanger.ac.uk/cosmic/api"  # TODO: verify when writing the service
)
# MGI has no stable public JSON API: its curated mouse data is served
# by the Alliance of Genome Resources (checked with scripts/explore_mgi*.py)
ALPHAFOLD_BASE_URL = "https://alphafold.ebi.ac.uk"
ALLIANCE_BASE_URL = "https://www.alliancegenome.org/api"
CLINICALTRIALS_BASE_URL = "https://clinicaltrials.gov/api/v2"
REACTOME_BASE_URL = "https://reactome.org/ContentService"
STRING_BASE_URL = "https://string-db.org/api"
EUROPEPMC_BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
LENS_BASE_URL = "https://api.lens.org"
OPENTARGETS_GRAPHQL_URL = "https://api.platform.opentargets.org/api/v4/graphql"

# Retry / timeout settings
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=5.0)  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds, multiplied by the attempt number
MAX_RETRY_AFTER = 30  # seconds, upper bound on a server-requested wait (429)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Identification (polite pool for OpenAlex, recommended by NCBI)
CONTACT_EMAIL = os.getenv("TARGETSCOPE_CONTACT_EMAIL", "")
USER_AGENT = "TargetScope/1.0 (https://github.com/Adrien-COSSON/TargetScope" + (
    f"; mailto:{CONTACT_EMAIL})" if CONTACT_EMAIL else ")"
)

# Shared client
_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    """Return the shared AsyncClient, creating it on first use."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,  # httpx does NOT follow redirects by default
        )
        logger.info("HTTP client initialized")
    return _client


async def close_client() -> None:
    """Close the shared AsyncClient. Call it on application shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("HTTP client closed")


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Return how long to wait before the next attempt.

    Honours the `Retry-After` header on HTTP 429 (capped at MAX_RETRY_AFTER),
    otherwise applies a linear backoff.
    """
    if response is not None and response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return min(float(retry_after), MAX_RETRY_AFTER)
    return RETRY_BACKOFF * attempt


async def _request_json(
    method: str,
    url: str,
    params: dict | None = None,
    json_body: dict | None = None,
    source: str = "HTTP",
    not_found=None,
    raw: bool = False,
) -> dict | list | bytes | None:
    """Send a request and return its decoded JSON body, retrying only recoverable errors.

    Shared by get_json() and post_json() so every source gets the same behaviour.

    Retried: timeouts, network/transport errors, HTTP 429 and 5xx.
    Not retried: other 4xx (e.g. 400 malformed ID, 404 unknown ID), invalid JSON,
    and any other HTTP error.

    Only the base URL is logged, never the query string, so API keys passed in
    `params` (e.g. NCBI api_key) cannot leak into the logs.

    Args:
        method (str): "GET" or "POST".
        url (str): Endpoint URL, without query string.
        params (dict | None): Query parameters.
        json_body (dict | None): JSON body (POST only, e.g. a GraphQL query).
        source (str): Source name used as log prefix (e.g. "UniProt").
        not_found: Value returned on HTTP 404, for APIs where 404 means
            "no data" rather than an error (e.g. Reactome: no pathway).
            Default None keeps 404 = failure.
        raw (bool): return the body as bytes instead of decoding JSON
            (e.g. the HPA per-gene XML).

    Returns:
        dict | list | None: Decoded JSON on success, None on failure.
    """
    client = await get_client()

    for attempt in range(1, MAX_RETRIES + 1):
        response = None
        try:
            response = await client.request(method, url, params=params, json=json_body)
        except httpx.TransportError as e:  # timeouts, connection and protocol errors
            logger.warning(
                "%s: network error on attempt %d/%d for %s: %s",
                source,
                attempt,
                MAX_RETRIES,
                url,
                type(e).__name__,
            )
        except httpx.HTTPError as e:
            logger.error(
                "%s: request error for %s, not retried: %s",
                source,
                url,
                type(e).__name__,
            )
            return None
        else:
            if response.is_success:
                if raw:
                    return response.content
                try:
                    return response.json()
                except ValueError:
                    logger.error("%s: invalid JSON response from %s", source, url)
                    return None

            if response.status_code == 404 and not_found is not None:
                logger.info("%s: 404 for %s, treated as 'no data'", source, url)
                return not_found

            if response.status_code not in RETRYABLE_STATUS:
                logger.warning(
                    "%s: HTTP %d for %s, not retried", source, response.status_code, url
                )
                return None

            logger.warning(
                "%s: HTTP %d on attempt %d/%d for %s",
                source,
                response.status_code,
                attempt,
                MAX_RETRIES,
                url,
            )

        if attempt < MAX_RETRIES:
            await asyncio.sleep(_retry_delay(response, attempt))

    logger.error(
        "%s: definitive failure after %d attempts for %s", source, MAX_RETRIES, url
    )
    return None


async def get_json(
    url: str,
    params: dict | None = None,
    source: str = "HTTP",
    not_found=None,
) -> dict | list | None:
    """GET a URL and return its decoded JSON body (None on failure).

    Existing calls are unchanged; `not_found` is optional (see _request_json).
    """
    return await _request_json(
        "GET", url, params=params, source=source, not_found=not_found
    )


async def get_bytes(url: str, params: dict | None = None, source: str = "HTTP") -> bytes | None:
    """GET a URL and return the raw body (None on failure), with the same retry rules.

    For non-JSON sources, e.g. the Human Protein Atlas per-gene XML.
    """
    return await _request_json("GET", url, params=params, source=source, raw=True)


async def post_json(
    url: str,
    json_body: dict,
    source: str = "HTTP",
) -> dict | list | None:
    """POST a JSON body and return the decoded JSON response (None on failure).

    Used for GraphQL APIs (Open Targets), where the query travels in the body.
    """
    return await _request_json("POST", url, json_body=json_body, source=source)
