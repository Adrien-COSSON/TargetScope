# backend/services/pubmed.py

import asyncio
import logging
from datetime import date

from backend.config import settings
from backend.utils.http_client import get_json, PUBMED_BASE_URL

logger = logging.getLogger(__name__)

ESEARCH_URL = f"{PUBMED_BASE_URL}/esearch.fcgi"
RECENT_YEARS = 5
NCBI_TOOL = "TargetScope"


def _base_params(gene_name: str) -> dict:
    """Build the esearch parameters shared by both calls."""
    params = {
        "db": "pubmed",
        "term": f"{gene_name}[Title/Abstract]",
        "retmax": 0,
        "retmode": "json",
        "tool": NCBI_TOOL,
    }
    if settings.contact_email:
        params["email"] = settings.contact_email
    if settings.pubmed_api_key:
        params["api_key"] = settings.pubmed_api_key
    return params


def _extract_count(data: dict | None) -> int | None:
    """Return the hit count from an esearch response, None if the request failed."""
    if data is None:
        return None
    return int(data.get("esearchresult", {}).get("count", 0))


async def fetch_pubmed(gene_name: str) -> dict:
    """Fetch publication maturity metrics from PubMed for a given gene.

    Runs two esearch calls in parallel (retmax=0, counts only):
      1. all-time publication count
      2. publication count over the last RECENT_YEARS years

    Args:
        gene_name: HGNC gene symbol, e.g. "TP53".

    Returns:
        A dict with keys:
            - gene_name (str)
            - total_count (int | None)
            - recent_count (int | None)
            - recent_window (str): e.g. "2022-2026"
        Counts are None when PubMed could not be reached, 0 when the gene has no publication.
    """
    logger.info("PubMed: fetch_pubmed started for '%s'", gene_name)

    current_year = date.today().year
    start_year = current_year - RECENT_YEARS + 1

    total_params = _base_params(gene_name)
    recent_params = {
        **_base_params(gene_name),
        "datetype": "pdat",
        "mindate": str(start_year),
        "maxdate": str(current_year),
    }

    total_data, recent_data = await asyncio.gather(
        get_json(ESEARCH_URL, params=total_params, source="PubMed"),
        get_json(ESEARCH_URL, params=recent_params, source="PubMed"),
    )

    result = {
        "gene_name": gene_name,
        "total_count": _extract_count(total_data),
        "recent_count": _extract_count(recent_data),
        "recent_window": f"{start_year}-{current_year}",
    }

    logger.info("PubMed: fetch_pubmed ended for '%s'", gene_name)
    return result