# backend/services/europe_pmc.py
"""Europe PMC service: yearly publication timeline for a gene.

Feeds the "biological knowledge" dimension with a trend: is the target
emerging, growing, stable or declining in the literature?
(PubMed gives the totals; Europe PMC gives the year-by-year shape.)
"""

import asyncio
import logging
from datetime import date

from backend.utils.http_client import EUROPEPMC_BASE_URL, get_json

logger = logging.getLogger(__name__)

SEARCH_URL = f"{EUROPEPMC_BASE_URL}/search"
START_YEAR = 1990

# Europe PMC has no per-year facet in its REST search, so we need one call per
# year. They run in parallel, capped to stay polite with EBI servers.
MAX_CONCURRENT = 5


async def _count_for_year(term: str, year: int, semaphore: asyncio.Semaphore) -> int | None:
    """Number of publications matching `term` in a given year (None on failure)."""
    async with semaphore:  # at most MAX_CONCURRENT calls in flight
        data = await get_json(
            SEARCH_URL,
            params={
                "query": f"{term} AND PUB_YEAR:{year}",
                "resultType": "lite",
                "pageSize": 1,  # we only read hitCount
                "format": "json",
            },
            source="EuropePMC",
        )
    if data is None:
        return None
    return int(data.get("hitCount", 0))


async def publication_timeline(gene_name: str) -> dict:
    """Return the yearly publication counts of a gene since START_YEAR.

    The search is restricted to titles and abstracts, as in pubmed.py:
    full-text matches on short symbols (e.g. "KIT", "MET") are very noisy.

    Args:
        gene_name: HGNC gene symbol, e.g. "TP53".

    Returns:
        A dict with keys:
            - gene_name (str)
            - timeline (dict[str, int | None] | None): {"1990": 12, ...},
              oldest first; a year is None if its request failed
            - failed_years (int): number of years that could not be fetched
        timeline is None only if every year failed (Europe PMC unreachable).
        The current year is partial: only publications indexed so far.
    """
    logger.info("EuropePMC: publication_timeline started for '%s'", gene_name)

    # Quotes: exact symbol match; TITLE/ABSTRACT: same scope as PubMed
    term = f'(TITLE:"{gene_name}" OR ABSTRACT:"{gene_name}")'
    years = list(range(START_YEAR, date.today().year + 1))

    # The semaphore is created here, inside the running event loop
    # (creating it at import time could bind it to another loop in tests)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    counts = await asyncio.gather(*(_count_for_year(term, y, semaphore) for y in years))

    timeline = {str(year): count for year, count in zip(years, counts)}
    failed = sum(1 for c in counts if c is None)

    if failed == len(years):
        logger.error("EuropePMC: every request failed for '%s'", gene_name)
        return {"gene_name": gene_name, "timeline": None, "failed_years": failed}

    if failed:
        logger.warning("EuropePMC: %d/%d years failed for '%s'", failed, len(years), gene_name)

    logger.info("EuropePMC: publication_timeline ended for '%s'", gene_name)
    return {"gene_name": gene_name, "timeline": timeline, "failed_years": failed}
