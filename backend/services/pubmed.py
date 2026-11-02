# backend/services/pubmed.py
"""PubMed service: publication counts used as a research-maturity indicator.

Only esearch with retmax=0 is used: we need counts, not article content.
"""

import asyncio
import logging
from datetime import date

from backend.utils.ncbi import ESEARCH_URL, extract_count, ncbi_get, ncbi_params

logger = logging.getLogger(__name__)

RECENT_YEARS = 5  # size of the "recent activity" window, current year included


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
    start_year = current_year - RECENT_YEARS + 1  # +1: the current year counts as one

    # [Title/Abstract] avoids matches on MeSH terms or affiliations only
    term = f"{gene_name}[Title/Abstract]"

    total_params = ncbi_params("pubmed", term=term, retmax=0)
    # datetype=pdat: filter on publication date (not entry date in PubMed)
    recent_params = ncbi_params(
        "pubmed",
        term=term,
        retmax=0,
        datetype="pdat",
        mindate=str(start_year),
        maxdate=str(current_year),
    )

    # Both calls go through the shared NCBI throttle, so running them
    # in parallel cannot exceed the rate limit.
    total_data, recent_data = await asyncio.gather(
        ncbi_get(ESEARCH_URL, total_params, source="PubMed"),
        ncbi_get(ESEARCH_URL, recent_params, source="PubMed"),
    )

    result = {
        "gene_name": gene_name,
        "total_count": extract_count(total_data),
        "recent_count": extract_count(recent_data),
        "recent_window": f"{start_year}-{current_year}",
    }

    logger.info(
        "PubMed: fetch_pubmed ended for '%s' (total=%s, recent=%s)",
        gene_name,
        result["total_count"],
        result["recent_count"],
    )
    return result
