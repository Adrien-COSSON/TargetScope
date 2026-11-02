# backend/services/open_targets.py
"""Open Targets service: target-disease associations, tractability, constraint, safety.

Open Targets Platform integrates ~20 sources (GWAS, ClinVar, somatic data,
ChEMBL, literature, animal models...) into one association score per
target-disease pair, broken down by evidence type ("datatype").

Feeds:
  - disease association: which diseases, how strongly, on which kind of evidence
  - tractability: modality-specific druggability assessment (small molecule,
    antibody, PROTAC, other modalities)
  - safety: genetic constraint (LoF intolerance) and curated safety liabilities

One GraphQL query returns everything (GraphQL = we choose the exact fields).
"""

import logging
from collections import defaultdict

from backend.utils.http_client import OPENTARGETS_GRAPHQL_URL, post_json
from backend.utils.provenance import make_provenance

logger = logging.getLogger(__name__)

TARGET_URL = "https://platform.opentargets.org/target/{ensembl_id}"
TOP_ASSOCIATIONS = 25  # diseases returned, sorted by overall score by the API

# Tractability modalities, as coded by Open Targets
MODALITY_LABELS = {
    "SM": "Small molecule",
    "AB": "Antibody",
    "PR": "PROTAC",
    "OC": "Other modalities",
}

# A single GraphQL query: field names are checked by the server,
# so a typo here returns an explicit error instead of silent wrong data.
QUERY = """
query TargetAssessment($ensemblId: String!, $size: Int!) {
  target(ensemblId: $ensemblId) {
    id
    approvedSymbol
    associatedDiseases(page: {index: 0, size: $size}) {
      count
      rows {
        score
        disease {
          id
          name
          therapeuticAreas { id name }
        }
        datatypeScores { id score }
      }
    }
    tractability { label modality value }
    geneticConstraint { constraintType oe oeLower oeUpper score }
    safetyLiabilities {
      event
      eventId
      datasource
      effects { direction dosing }
    }
  }
}
"""


def _parse_associations(rows: list[dict]) -> list[dict]:
    """Flatten association rows; datatype scores become a {datatype: score} dict."""
    associations = []
    for row in rows:
        disease = row.get("disease") or {}
        associations.append(
            {
                "disease_id": disease.get("id"),  # EFO / MONDO id
                "disease_name": disease.get("name"),
                "score": round(row.get("score") or 0.0, 3),  # overall, 0 to 1
                "therapeutic_areas": [
                    ta.get("name") for ta in disease.get("therapeuticAreas") or []
                ],
                # e.g. {"genetic_association": 0.81, "clinical": 0.99, ...}
                # Current datatype ids: genetic_association, genetic_literature,
                # somatic_mutation, clinical (drugs), affected_pathway,
                # rna_expression, literature, animal_model
                # A missing datatype means "no evidence of that type", not 0 on a test
                "datatype_scores": {
                    d["id"]: round(d["score"], 3)
                    for d in row.get("datatypeScores") or []
                    if d.get("id") is not None and d.get("score") is not None
                },
            }
        )
    return associations


def _parse_tractability(items: list[dict]) -> dict[str, list[str]]:
    """Keep the tractability buckets that are true, grouped by modality.

    Open Targets returns every bucket with value true/false; only the true ones
    are evidence (e.g. "Approved Drug", "High-Quality Ligand", "UniProt loc high conf").
    """
    by_modality: dict[str, list[str]] = defaultdict(list)
    for item in items or []:
        if item.get("value"):
            label = MODALITY_LABELS.get(item.get("modality"), item.get("modality"))
            by_modality[label].append(item.get("label"))
    return dict(by_modality)


def _parse_constraint(items: list[dict]) -> dict | None:
    """Return the loss-of-function constraint (gnomAD), the one used for safety.

    oe = observed / expected LoF variants in the human population.
    oeUpper is LOEUF: below ~0.35, the gene does not tolerate losing one copy,
    a classic warning sign for strong inhibition.
    """
    for item in items or []:
        if item.get("constraintType") == "lof":
            return {
                "oe": item.get("oe"),
                "loeuf": item.get("oeUpper"),
                "oe_lower": item.get("oeLower"),
            }
    return None


def _parse_safety(items: list[dict]) -> list[dict]:
    """One entry per safety event, with its sources and effects merged."""
    events: dict[str, dict] = {}
    for item in items or []:
        name = item.get("event")
        if not name:
            continue
        entry = events.setdefault(
            name,
            {"event": name, "event_id": item.get("eventId"), "datasources": set(), "effects": []},
        )
        if item.get("datasource"):
            entry["datasources"].add(item["datasource"])
        for effect in item.get("effects") or []:
            # direction is e.g. "Inhibition/Decrease/Downregulation" (slashes included),
            # dosing ("acute", "chronic"...) is often missing: keep it only when present
            parsed = {"direction": effect.get("direction")}
            if effect.get("dosing"):
                parsed["dosing"] = effect["dosing"]
            if parsed["direction"] and parsed not in entry["effects"]:
                entry["effects"].append(parsed)
    # set -> sorted list: JSON-serialisable and stable between runs
    return [{**e, "datasources": sorted(e["datasources"])} for e in events.values()]


def _empty_result(ensembl_id: str | None, found: bool | None) -> dict:
    """found=False: target unknown to Open Targets. found=None: API unreachable."""
    zero = 0 if found is False else None
    empty = [] if found is False else None
    return {
        "ensembl_id": ensembl_id,
        "found": found,
        "association_count": zero,
        "top_associations": empty,
        "tractability": None if found is None else {},
        "genetic_constraint": None,
        "safety_liabilities": empty,
        "provenance": None,
    }


async def fetch_open_targets(ensembl_id: str | None) -> dict:
    """Fetch associations, tractability, constraint and safety from Open Targets.

    Args:
        ensembl_id: Ensembl gene id, e.g. "ENSG00000146648" (EGFR),
            as set by the resolver on Target.ensembl_id.

    Returns:
        A dict with keys:
            - ensembl_id (str | None)
            - found (bool | None): False if Open Targets does not know the target,
              None if the API could not be reached or ensembl_id is missing
            - association_count (int | None): all associated diseases
            - top_associations (list[dict] | None): TOP_ASSOCIATIONS diseases,
              highest overall score first (disease_id, disease_name, score,
              therapeutic_areas, datatype_scores)
            - tractability (dict[str, list[str]] | None): true buckets per modality
            - genetic_constraint (dict | None): gnomAD LoF oe and LOEUF
            - safety_liabilities (list[dict] | None): curated safety events
            - provenance (Provenance | None)
        Association scores are relative (0-1) and depend on the amount of
        evidence: a high score is not proof of causality.
    """
    logger.info("Open Targets: fetch_open_targets started for '%s'", ensembl_id)

    if not ensembl_id:
        logger.warning("Open Targets: no Ensembl id, cannot query")
        return _empty_result(ensembl_id, found=None)

    data = await post_json(
        OPENTARGETS_GRAPHQL_URL,
        json_body={
            "query": QUERY,
            "variables": {"ensemblId": ensembl_id, "size": TOP_ASSOCIATIONS},
        },
        source="OpenTargets",
    )
    if data is None:
        return _empty_result(ensembl_id, found=None)

    # GraphQL reports query problems in an "errors" list, often with HTTP 200:
    # they must be checked explicitly, get_json/post_json cannot see them.
    if data.get("errors"):
        for error in data["errors"]:
            logger.error("Open Targets: GraphQL error: %s", error.get("message"))
        return _empty_result(ensembl_id, found=None)

    target = (data.get("data") or {}).get("target")
    if target is None:
        logger.info("Open Targets: unknown target '%s'", ensembl_id)
        return _empty_result(ensembl_id, found=False)

    assoc = target.get("associatedDiseases") or {}
    associations = _parse_associations(assoc.get("rows") or [])

    result = {
        "ensembl_id": ensembl_id,
        "found": True,
        "association_count": assoc.get("count", 0),
        "top_associations": associations,
        "tractability": _parse_tractability(target.get("tractability")),
        "genetic_constraint": _parse_constraint(target.get("geneticConstraint")),
        "safety_liabilities": _parse_safety(target.get("safetyLiabilities")),
        "provenance": make_provenance(
            source="Open Targets Platform",
            source_id=ensembl_id,
            url=TARGET_URL.format(ensembl_id=ensembl_id),
            # aggregated, scored evidence from curated sources
            confidence="high",
        ),
    }

    logger.info(
        "Open Targets: fetch_open_targets ended for '%s' (associations=%s, safety events=%d)",
        ensembl_id,
        result["association_count"],
        len(result["safety_liabilities"]),
    )
    return result
