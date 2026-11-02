# backend/services/reactome.py
"""Reactome service: curated pathways containing the protein.

Endpoint: /data/mapping/UniProt/{uniprot_id}/pathways?species=9606
Returns low-level pathways (leaf nodes of the Reactome hierarchy).

Feeds the "biological knowledge" dimension: how well the protein's role
is mapped into curated biology.
"""

import logging

from backend.utils.http_client import REACTOME_BASE_URL, get_json
from backend.utils.provenance import make_provenance

logger = logging.getLogger(__name__)

PATHWAY_PAGE_URL = "https://reactome.org/content/detail/{pathway_id}"
HUMAN_TAXON = "9606"


def _parse_pathways(raw: list[dict]) -> list[dict]:
    """Extract pathway id, name and link; skip items without an id.

    stId is the stable Reactome id (e.g. "R-HSA-177929").
    """
    pathways = []
    for item in raw:
        pathway_id = (item.get("stId") or "").strip()
        if not pathway_id:
            continue
        pathways.append(
            {
                "pathway_id": pathway_id,
                "name": (item.get("displayName") or "").strip(),
                "url": PATHWAY_PAGE_URL.format(pathway_id=pathway_id),
            }
        )
    return pathways


async def fetch_reactome(uniprot_id: str) -> dict:
    """Fetch the Reactome pathways that contain a protein.

    The UniProt id comes from the resolver (Swiss-Prot accession), so no
    format check is done here. The previous regex only accepted 6-character
    accessions and would have rejected valid 10-character ones (A0A...).

    Args:
        uniprot_id: UniProt accession, e.g. "P04637".

    Returns:
        A dict with keys:
            - uniprot_id (str)
            - pathways (list[dict] | None): pathway_id, name, url;
              None if Reactome could not be reached,
              [] if the protein is in no pathway (Reactome answers 404)
            - provenance (Provenance | None)
    """
    logger.info("Reactome: fetch_reactome started for '%s'", uniprot_id)

    raw = await get_json(
        f"{REACTOME_BASE_URL}/data/mapping/UniProt/{uniprot_id}/pathways",
        params={"species": HUMAN_TAXON},
        source="Reactome",
        # Reactome answers 404 when the protein has no annotated pathway:
        # that is a real answer ("none"), not a failure
        not_found=[],
    )

    if raw is None:
        return {"uniprot_id": uniprot_id, "pathways": None, "provenance": None}

    pathways = _parse_pathways(raw)

    logger.info(
        "Reactome: fetch_reactome ended for '%s' (%d pathways)", uniprot_id, len(pathways)
    )
    return {
        "uniprot_id": uniprot_id,
        "pathways": pathways,
        "provenance": make_provenance(
            source="Reactome",
            source_id=uniprot_id,
            url=f"https://reactome.org/content/query?q={uniprot_id}&species=Homo+sapiens",
            confidence="high",  # manually curated pathways
        ),
    }
