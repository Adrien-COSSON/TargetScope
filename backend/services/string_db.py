# backend/services/string_db.py
"""STRING service: functional protein-protein interaction partners.

Feeds the "biological knowledge" dimension: a well-connected protein sits in a
characterised network, and its partners hint at pathway context and
potential on-target liabilities (shared complexes, signalling hubs).
"""

import logging

from backend.utils.http_client import STRING_BASE_URL, get_json
from backend.utils.provenance import make_provenance

logger = logging.getLogger(__name__)

PARTNERS_URL = f"{STRING_BASE_URL}/json/interaction_partners"
NETWORK_PAGE_URL = "https://string-db.org/network/9606.{string_id}"
HUMAN_TAXON = 9606
MAX_PARTNERS = 10

# STRING asks API users to identify themselves
CALLER_IDENTITY = "TargetScope"


def _parse_partners(raw: list[dict]) -> list[dict]:
    """Return partner symbol and combined score (0-1), strongest first.

    interaction_partners always puts the queried protein in "_A" and the
    partner in "_B", so there is no need to match names (the previous
    'network' endpoint mixed in partner-partner edges and required it).
    """
    partners = [
        {
            "partner": item.get("preferredName_B"),
            "string_id": item.get("stringId_B"),
            "score": item.get("score"),
        }
        for item in raw
        if item.get("preferredName_B")
    ]
    partners.sort(key=lambda p: p["score"] or 0.0, reverse=True)
    return partners


async def fetch_string(uniprot_id: str) -> dict:
    """Fetch the top functional interaction partners of a protein from STRING.

    Args:
        uniprot_id: UniProt accession, e.g. "P04637".

    Returns:
        A dict with keys:
            - uniprot_id (str)
            - interactions (list[dict] | None): partner, string_id, score (0-1),
              strongest first; None if STRING could not be reached,
              [] if the protein has no known partner
            - provenance (Provenance | None)
        STRING scores combine experimental, database, co-expression and
        text-mining evidence: a high score is functional association,
        not necessarily direct physical binding.
    """
    logger.info("STRING: fetch_string started for '%s'", uniprot_id)

    raw = await get_json(
        PARTNERS_URL,
        params={
            "identifiers": uniprot_id,
            "species": HUMAN_TAXON,
            "limit": MAX_PARTNERS,
            "caller_identity": CALLER_IDENTITY,
        },
        source="STRING",
    )

    if raw is None:
        return {"uniprot_id": uniprot_id, "interactions": None, "provenance": None}

    interactions = _parse_partners(raw)
    # STRING id of the query protein, e.g. "9606.ENSP00000269305", for the link
    query_id = raw[0].get("stringId_A") if raw else None

    logger.info(
        "STRING: fetch_string ended for '%s' (%d partners)", uniprot_id, len(interactions)
    )
    return {
        "uniprot_id": uniprot_id,
        "interactions": interactions,
        "provenance": make_provenance(
            source="STRING",
            source_id=query_id,
            url=NETWORK_PAGE_URL.format(string_id=query_id.split(".", 1)[1])
            if query_id
            else None,
            confidence="medium",  # functional association, partly text-mined
        ),
    }
