# backend/services/mgi.py
"""Mouse genetics service: knockout / mutant phenotypes of the mouse ortholog.

Data are curated by MGI and served by the Alliance of Genome Resources API
(MGI itself has no stable public JSON API, see scripts/explore_mgi*.py).

Feeds two assessment dimensions:
  - safety: lethality and organ phenotypes when the gene is disrupted
  - genetic validation: does disrupting the gene give a disease-relevant phenotype?

Flow:
  1. human HGNC id -> mouse ortholog (MGI id) via Alliance orthology
  2. all phenotype annotations of that mouse gene
"""

import logging

from backend.utils.http_client import ALLIANCE_BASE_URL, get_json
from backend.utils.provenance import make_provenance

logger = logging.getLogger(__name__)

MOUSE_TAXON = "NCBITaxon:10090"
MGI_GENE_URL = "https://www.informatics.jax.org/marker/{mgi_id}"
MGI_PHENOTYPE_URL = "https://www.informatics.jax.org/allele/summary?markerId={mgi_id}"

# One page is enough: the most studied genes have ~300 phenotypes (Trp53: 304)
PHENOTYPE_PAGE_SIZE = 1000
TOP_PHENOTYPES = 25  # best-documented phenotypes kept in the output
MAX_PMIDS = 5  # references kept per phenotype

# Every MP lethality term contains "lethal" (embryonic lethality, perinatal
# lethality, preweaning lethality...), so a keyword is enough here.
LETHALITY_KEYWORD = "lethal"


def _pick_mouse_ortholog(rows: list[dict]) -> dict | None:
    """Return the mouse ortholog, preferring Alliance's best-score match.

    A human gene can have several mouse orthologs (gene duplications):
    "isBestScore" marks the one most methods agree on.
    """
    mouse = [
        r["geneToGeneOrthologyGenerated"]
        for r in rows
        if r.get("geneToGeneOrthologyGenerated", {})
        .get("objectGene", {})
        .get("taxon", {})
        .get("curie")
        == MOUSE_TAXON
    ]
    if not mouse:
        return None
    # sorted() puts False before True, so reverse=True brings best scores first
    mouse.sort(key=lambda o: (o.get("isBestScore") or {}).get("name") == "Yes", reverse=True)
    best = mouse[0]
    gene = best["objectGene"]
    return {
        "mgi_id": gene.get("primaryExternalId"),
        "symbol": (gene.get("geneSymbol") or {}).get("displayText"),
        # "high" / "moderate" / "low", as computed by Alliance
        "orthology_confidence": (best.get("confidence") or {}).get("name"),
        "prediction_methods": [
            m.get("name") for m in best.get("predictionMethodsMatched", [])
        ],
        "candidates": len(mouse),
    }


def _parse_phenotypes(records: list[dict]) -> list[dict]:
    """One entry per MP term, with the PMIDs supporting it.

    The same term can be annotated several times (different alleles, papers),
    so terms are merged and their references pooled.
    """
    terms: dict[str, dict] = {}
    for record in records:
        for annotation in record.get("primaryAnnotations", []):
            pmid = (annotation.get("evidenceItem") or {}).get("referenceID")
            for term in annotation.get("phenotypeTerms", []):
                curie = term.get("curie")
                if not curie:
                    continue
                entry = terms.setdefault(
                    curie, {"mp_id": curie, "term": term.get("name"), "pmids": set()}
                )
                # referenceID can also be an MGI reference id: keep PubMed ones only
                if pmid and pmid.startswith("PMID:"):
                    entry["pmids"].add(pmid)

    phenotypes = [
        {
            "mp_id": t["mp_id"],
            "term": t["term"],
            "reference_count": len(t["pmids"]),
            # sorted() makes the output stable between runs (sets have no order)
            "pmids": sorted(t["pmids"])[:MAX_PMIDS],
        }
        for t in terms.values()
    ]
    # Best-documented phenotypes first
    phenotypes.sort(key=lambda p: p["reference_count"], reverse=True)
    return phenotypes


def _empty_result(hgnc_id: str | None, found: bool | None) -> dict:
    """found=False: no mouse ortholog. found=None: Alliance unreachable or no HGNC id."""
    zero = 0 if found is False else None
    empty = [] if found is False else None
    return {
        "hgnc_id": hgnc_id,
        "found": found,
        "mouse_gene": None,
        "phenotype_count": zero,
        "has_lethality": None if found is None else False,
        "lethality_phenotypes": empty,
        "top_phenotypes": empty,
        "truncated": False,
        "provenance": None,
    }


async def fetch_mgi(hgnc_id: str | None) -> dict:
    """Fetch mouse phenotypes of the ortholog of a human gene.

    Args:
        hgnc_id: HGNC CURIE of the human gene, e.g. "HGNC:6407" (KRAS),
            as set by the resolver on Target.hgnc_id.

    Returns:
        A dict with keys:
            - hgnc_id (str | None)
            - found (bool | None): False if no mouse ortholog exists,
              None if Alliance could not be reached or hgnc_id is missing
            - mouse_gene (dict | None): mgi_id, symbol, orthology_confidence,
              prediction_methods, candidates, url
            - phenotype_count (int | None): distinct MP terms
            - has_lethality (bool | None)
            - lethality_phenotypes (list[dict] | None): MP terms containing "lethal"
            - top_phenotypes (list[dict] | None): TOP_PHENOTYPES best-documented
              terms (mp_id, term, reference_count, pmids)
            - truncated (bool): True if Alliance had more records than one page
            - provenance (Provenance | None)
        Phenotypes pool every MGI allele (null knockouts, conditional alleles,
        knock-in point mutations such as Kras G12D...), not only loss of function.
        A lethal phenotype is a safety flag to check, not a verdict: a germline
        knockout is not a pharmacological inhibition in an adult.
    """
    logger.info("MGI: fetch_mgi started for '%s'", hgnc_id)

    if not hgnc_id:
        logger.warning("MGI: no HGNC id, mouse ortholog cannot be looked up")
        return _empty_result(hgnc_id, found=None)

    # Step 1: human gene -> mouse ortholog
    # "stringent" keeps orthologs supported by several prediction methods
    orth = await get_json(
        f"{ALLIANCE_BASE_URL}/gene/{hgnc_id}/orthologs",
        params={"filter.stringency": "stringent"},
        source="Alliance",
    )
    if orth is None:
        return _empty_result(hgnc_id, found=None)

    mouse_gene = _pick_mouse_ortholog(orth.get("results", []))
    if mouse_gene is None:
        logger.info("MGI: no mouse ortholog for '%s'", hgnc_id)
        return _empty_result(hgnc_id, found=False)

    mgi_id = mouse_gene["mgi_id"]
    mouse_gene["url"] = MGI_GENE_URL.format(mgi_id=mgi_id)

    # Step 2: phenotypes of the mouse gene (depends on step 1 -> sequential)
    data = await get_json(
        f"{ALLIANCE_BASE_URL}/gene/{mgi_id}/phenotypes",
        params={"limit": PHENOTYPE_PAGE_SIZE},
        source="Alliance",
    )

    if data is None:
        # Ortholog known but phenotypes unknown: keep what we have
        result = _empty_result(hgnc_id, found=None)
        result["mouse_gene"] = mouse_gene
        return result

    records = data.get("results", [])
    truncated = data.get("total", 0) > len(records)
    if truncated:
        logger.warning(
            "MGI: %s has %s phenotype records, only %d read",
            mgi_id,
            data.get("total"),
            len(records),
        )

    phenotypes = _parse_phenotypes(records)
    lethality = [p for p in phenotypes if LETHALITY_KEYWORD in (p["term"] or "").lower()]

    result = {
        "hgnc_id": hgnc_id,
        "found": True,
        "mouse_gene": mouse_gene,
        "phenotype_count": len(phenotypes),
        "has_lethality": bool(lethality),
        "lethality_phenotypes": lethality,
        "top_phenotypes": phenotypes[:TOP_PHENOTYPES],
        "truncated": truncated,
        "provenance": make_provenance(
            source="MGI (via Alliance of Genome Resources)",
            source_id=mgi_id,
            url=MGI_PHENOTYPE_URL.format(mgi_id=mgi_id),
            # MGI phenotypes are manually curated from the literature
            confidence="high" if mouse_gene["orthology_confidence"] == "high" else "medium",
        ),
    }

    logger.info(
        "MGI: fetch_mgi ended for '%s' (mouse=%s, phenotypes=%d, lethality=%d)",
        hgnc_id,
        mouse_gene["symbol"],
        len(phenotypes),
        len(lethality),
    )
    return result
