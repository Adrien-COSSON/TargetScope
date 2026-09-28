# backend\services\reactome.py

"""
reactome.py — Reactome pathway fetcher for TargetScope

Endpoint used:
    GET https://reactome.org/ContentService/data/mapping/UniProt/{uniprot_id}/pathways?species=9606

Returns low-level pathways (leaf nodes) containing the entity.
"""
# Import libraries
import logging
import time
import requests
import re

logger = logging.getLogger(__name__)

REACTOME_BASE_URL = "https://reactome.org/ContentService"
REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds
UNIPROT_RE = re.compile(r"^[A-Z][0-9][A-Z0-9]{3}[0-9]$")  # format standard 6 cars


def fetch_reactome(uniprot_id: str) -> dict:
    """Fetch Reactome pathways associated with an Ensembl gene ID.

    Queries the Reactome ContentService for all low-level pathways
    (leaf nodes in the hierarchy) that contain the given entity,
    filtered to Homo sapiens (taxon 9606).

    Args:
        uniprot_id: UniProt accession, e.g. "P04637"..

    Returns:
        A dict with the following keys:
            - ensembl_id (str): the queried ID
            - pathways (list[dict]): each item contains:
                  - pathway_id (str): Reactome stable ID, e.g. "R-HSA-5633007"
                  - name (str): human-readable pathway name
            - error (str | None): error message if the request failed,
              None otherwise

    Notes:
        - Returns an empty pathway list (not an error) when the API
          responds with 404 — this means the entity has no annotated
          pathways in Reactome, which is valid biological information.
        - Retries up to MAX_RETRIES times on transient network errors
          or 5xx responses before marking the result as an error.
    """
    if not uniprot_id or not UNIPROT_RE.match(uniprot_id):
        logger.warning("fetch_reactome called with unexpected ID: %s", uniprot_id)
        return {
            "uniprot_id": uniprot_id,
            "pathways": [],
            "error": f"Invalid UniProt accession: '{uniprot_id}'.",
        }

    url = f"{REACTOME_BASE_URL}/data/mapping/UniProt/{uniprot_id}/pathways"
    params = {"species": "9606"}

    last_error: str | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)

            if response.status_code == 200:
                raw: list[dict] = response.json()
                pathways = _parse_pathways(raw)
                logger.debug(
                    "Reactome: %d pathway(s) found for %s", len(pathways), uniprot_id
                )
                return {"uniprot_id": uniprot_id, "pathways": pathways, "error": None}

            if response.status_code == 404:
                logger.info("Reactome: no pathways found for %s (404)", uniprot_id)
                return {"uniprot_id": uniprot_id, "pathways": [], "error": None}

            # Any other HTTP error — retry on 5xx, fail immediately on 4xx
            if 400 <= response.status_code < 500:
                msg = f"Reactome HTTP {response.status_code} for {uniprot_id}."
                logger.error(msg)
                return {"uniprot_id": uniprot_id, "pathways": [], "error": msg}

            # 5xx — transient, worth retrying
            last_error = f"Reactome HTTP {response.status_code} (attempt {attempt}/{MAX_RETRIES})."
            logger.warning(last_error)

        except requests.exceptions.Timeout:
            last_error = f"Reactome request timed out (attempt {attempt}/{MAX_RETRIES})."
            logger.warning(last_error)

        except requests.exceptions.ConnectionError as exc:
            last_error = f"Reactome connection error (attempt {attempt}/{MAX_RETRIES}): {exc}."
            logger.warning(last_error)

        except requests.exceptions.RequestException as exc:
            last_error = f"Reactome unexpected request error: {exc}."
            logger.error(last_error)
            break  # non-retryable

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)

    return {"uniprot_id": uniprot_id, "pathways": [], "error": last_error}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_pathways(raw: list[dict]) -> list[dict]:
    """Extract pathway_id and name from raw Reactome response items.

    Reactome returns a list of dicts, each representing a pathway object.
    Relevant fields:
        - stId  : stable Reactome identifier (e.g. "R-HSA-5633007")
        - displayName : human-readable pathway name

    Items missing both fields are silently skipped to stay robust against
    any future API schema changes.
    """
    pathways = []
    for item in raw:
        pathway_id = item.get("stId", "").strip()
        name = item.get("displayName", "").strip()
        if not pathway_id and not name:
            continue
        pathways.append({"pathway_id": pathway_id, "name": name})
    return pathways