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

Public functions:
  - fetch_disease_options(): therapeutic areas + diseases associated with the
    target, to fill the Streamlit indication menus
  - search_diseases(): free-text disease search ("search another disease...")
  - fetch_open_targets(): full assessment data, optionally focused on an indication
"""

import asyncio
import logging
from collections import defaultdict

from backend.utils.http_client import OPENTARGETS_GRAPHQL_URL, post_json
from backend.utils.provenance import make_provenance

logger = logging.getLogger(__name__)

TARGET_URL = "https://platform.opentargets.org/target/{ensembl_id}"
TOP_ASSOCIATIONS = 25  # diseases returned, sorted by overall score by the API
MENU_SIZE = 500  # associations used to fill the indication menus (best scores first)
SEARCH_SIZE = 15  # hits returned by the free-text disease search
# "cancer" has ~3700 descendant diseases. They are NOT truncated (a first
# version capped them at 3000 and silently lost CLL and DLBCL for XPO1):
# they are split into chunks queried in parallel, then merged.
FOCUS_CHUNK = 1500

# Tractability modalities, as coded by Open Targets
MODALITY_LABELS = {
    "SM": "Small molecule",
    "AB": "Antibody",
    "PR": "PROTAC",
    "OC": "Other modalities",
}

# Field names are checked by the server: a typo returns an explicit error
# instead of silent wrong data.
#
# @include(if: $hasFocus) is standard GraphQL: the focus blocks are only
# computed when the user chose an indication.
#
# enableIndirect is ALWAYS false (direct evidence only). Checked with
# scripts/diagnose_ot_scores.py: with enableIndirect true, a Bs-filtered query
# returned XPO1 - cystic fibrosis = 0.925 although XPO1 has zero evidence for
# it (the score was essentially CFTR's: the target filter is not applied).
# The disease hierarchy is therefore handled here, explicitly: an indication
# is queried together with all its descendant diseases (FOCUS_QUERY, in chunks).
QUERY = """
query TargetAssessment(
  $ensemblId: String!, $size: Int!,
  $hasFocus: Boolean!, $indicationIds: [String!]
) {
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
    indication: associatedDiseases(
      Bs: $indicationIds, enableIndirect: false, page: {index: 0, size: 1}
    ) @include(if: $hasFocus) {
      rows {
        score
        disease { id name therapeuticAreas { id name } }
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

# Diseases associated with the target, light fields only, for the menus
OPTIONS_QUERY = """
query DiseaseOptions($ensemblId: String!, $size: Int!) {
  target(ensemblId: $ensemblId) {
    associatedDiseases(enableIndirect: false, page: {index: 0, size: $size}) {
      count
      rows {
        score
        disease { id name therapeuticAreas { id name } }
      }
    }
  }
}
"""

# Target associations restricted to a list of diseases (one chunk of the focus)
FOCUS_QUERY = """
query Focus($ensemblId: String!, $ids: [String!], $size: Int!) {
  target(ensemblId: $ensemblId) {
    associatedDiseases(Bs: $ids, enableIndirect: false, page: {index: 0, size: $size}) {
      count
      rows {
        score
        disease { id name therapeuticAreas { id name } }
        datatypeScores { id score }
      }
    }
  }
}
"""

# An indication and all the diseases below it in the ontology
DISEASE_QUERY = """
query Disease($diseaseId: String!) {
  disease(efoId: $diseaseId) { id name descendants }
}
"""

SEARCH_QUERY = """
query SearchDiseases($text: String!, $size: Int!) {
  search(queryString: $text, entityNames: ["disease"], page: {index: 0, size: $size}) {
    hits { id name entity }
  }
}
"""


async def _graphql(query: str, variables: dict) -> dict | None:
    """Run a GraphQL query; return its "data" block, None on any failure.

    GraphQL reports query problems in an "errors" list, often with HTTP 200:
    post_json cannot see them, so they are checked here for every query.
    """
    response = await post_json(
        OPENTARGETS_GRAPHQL_URL,
        json_body={"query": query, "variables": variables},
        source="OpenTargets",
    )
    if response is None:
        return None
    if response.get("errors"):
        for error in response["errors"]:
            logger.error("Open Targets: GraphQL error: %s", error.get("message"))
        return None
    return response.get("data") or {}


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
        "indication": None,
        "focus_associations": None,
        "provenance": None,
    }


async def fetch_disease_options(ensembl_id: str | None) -> dict | None:
    """Therapeutic areas and diseases associated with a target, for the menus.

    Direct associations only: an intermediate term ("leukemia") appears only if
    it has direct evidence; search_diseases() covers everything else.

    Streamlit shows the areas in a first menu, then the diseases of the chosen
    area (already sorted by score) in a searchable second menu.

    Args:
        ensembl_id: Ensembl gene id of the target.

    Returns:
        None if Open Targets could not be reached, else a dict with keys:
            - therapeutic_areas (list[dict]): id, name, disease_count;
              most populated first
            - diseases (list[dict]): id, name, score, therapeutic_area_ids;
              best score first
            - total_associations (int): all associations, beyond MENU_SIZE
    """
    if not ensembl_id:
        return None
    data = await _graphql(OPTIONS_QUERY, {"ensemblId": ensembl_id, "size": MENU_SIZE})
    if data is None:
        return None
    target = data.get("target")
    if target is None:
        return {"therapeutic_areas": [], "diseases": [], "total_associations": 0}

    assoc = target.get("associatedDiseases") or {}
    diseases = []
    areas: dict[str, dict] = {}
    for row in assoc.get("rows") or []:
        disease = row.get("disease") or {}
        tas = disease.get("therapeuticAreas") or []
        diseases.append(
            {
                "id": disease.get("id"),
                "name": disease.get("name"),
                "score": round(row.get("score") or 0.0, 3),
                "therapeutic_area_ids": [ta.get("id") for ta in tas],
            }
        )
        # A disease can belong to several areas (e.g. a cancer that is also
        # a genetic disorder): it is counted in each of them
        for ta in tas:
            area = areas.setdefault(
                ta.get("id"), {"id": ta.get("id"), "name": ta.get("name"), "disease_count": 0}
            )
            area["disease_count"] += 1

    return {
        "therapeutic_areas": sorted(areas.values(), key=lambda a: a["disease_count"], reverse=True),
        "diseases": diseases,
        "total_associations": assoc.get("count", 0),
    }


async def search_diseases(text: str) -> list[dict] | None:
    """Free-text disease search, for indications absent from the target's menu.

    Evaluating a target on a disease where it has no evidence is a legitimate
    use case: it is what reveals a large evidence gap.

    Returns:
        list of {"id", "name"} (None if Open Targets could not be reached).
    """
    if not text or not text.strip():
        return []
    data = await _graphql(SEARCH_QUERY, {"text": text.strip(), "size": SEARCH_SIZE})
    if data is None:
        return None
    hits = (data.get("search") or {}).get("hits") or []
    return [{"id": h.get("id"), "name": h.get("name")} for h in hits if h.get("entity") == "disease"]


async def _focus_ids(indication_id: str) -> tuple[dict | None, list[str]]:
    """Return the indication (id, name) and the ids of the diseases below it."""
    data = await _graphql(DISEASE_QUERY, {"diseaseId": indication_id})
    disease = (data or {}).get("disease")
    if not disease:
        return None, []
    return {"id": disease.get("id"), "name": disease.get("name")}, disease.get("descendants") or []


async def _fetch_focus(ensembl_id: str, ids: list[str]) -> dict | None:
    """Direct associations of the target with any disease in `ids`.

    Each chunk returns its own top rows; merging them and re-sorting gives
    the global top, because a chunk's best rows are a superset of its share
    of the global top TOP_ASSOCIATIONS.

    Returns {"count", "rows"} (None if any chunk failed: a partial focus
    could hide the best sub-disease, which is worse than "unknown").
    """
    chunks = [ids[i : i + FOCUS_CHUNK] for i in range(0, len(ids), FOCUS_CHUNK)]
    results = await asyncio.gather(
        *(
            _graphql(FOCUS_QUERY, {"ensemblId": ensembl_id, "ids": c, "size": TOP_ASSOCIATIONS})
            for c in chunks
        )
    )
    if any(r is None for r in results):
        return None
    count, rows = 0, []
    for r in results:
        assoc = (r.get("target") or {}).get("associatedDiseases") or {}
        count += assoc.get("count") or 0
        rows.extend(assoc.get("rows") or [])
    rows.sort(key=lambda row: row.get("score") or 0.0, reverse=True)
    return {"count": count, "rows": rows[:TOP_ASSOCIATIONS]}


def _parse_indication(indication: dict, target: dict, focus: dict) -> dict:
    """Summarise the target's evidence on the chosen indication.

    Two levels, both from direct evidence only:
      - direct: the target - indication pair itself (e.g. XPO1 - CLL)
      - descendants: the diseases below the indication (e.g. the cancers
        under "cancer or benign tumor"); for a broad indication this is
        where the evidence actually is

    has_evidence False (no direct row, no associated descendant) is an
    answer, not a failure: it is a major evidence gap.
    focus None means the descendant query failed: has_evidence is then None
    unless a direct row already proves evidence.
    """
    direct_rows = _parse_associations((target.get("indication") or {}).get("rows") or [])
    direct = direct_rows[0] if direct_rows else None

    focus_rows = _parse_associations((focus or {}).get("rows") or [])
    # focus includes the indication itself: exclude it to count sub-diseases only
    descendants = [r for r in focus_rows if r["disease_id"] != indication["id"]]
    descendant_count = ((focus or {}).get("count") or 0) - (1 if direct else 0)

    if direct is not None or descendant_count > 0:
        has_evidence = True
    elif focus is None:
        has_evidence = None  # unknown: the sub-disease query failed
    else:
        has_evidence = False

    # Therapeutic areas of the indication: from its own row, else from its
    # sub-diseases (a broad term like "cancer" has no direct row)
    areas = direct["therapeutic_areas"] if direct else sorted(
        {ta for row in descendants for ta in row.get("therapeutic_areas", [])}
    )

    return {
        "disease_id": indication["id"],
        "disease_name": indication["name"],
        "therapeutic_areas": areas,  # e.g. ["cancer or benign tumor"]
        "direct_score": direct["score"] if direct else 0.0,
        "direct_datatype_scores": direct["datatype_scores"] if direct else {},
        "associated_descendant_count": max(descendant_count, 0),
        # best-scored sub-disease, e.g. "acute myeloid leukemia" under "cancer"
        "best_descendant": descendants[0] if descendants else None,
        "has_evidence": has_evidence,
    }


async def fetch_open_targets(
    ensembl_id: str | None, indication_id: str | None = None
) -> dict:
    """Fetch associations, tractability, constraint and safety from Open Targets.

    Args:
        ensembl_id: Ensembl gene id, e.g. "ENSG00000146648" (EGFR),
            as set by the resolver on Target.ensembl_id.
        indication_id: optional EFO / MONDO id chosen by the user, at any level
            ("MONDO_0004992" cancer ... "EFO_0000095" CLL).

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
            - indication (dict | None): only with indication_id; direct score on
              the indication, number of associated sub-diseases, best sub-disease,
              has_evidence (False = no evidence at all on the indication or below)
            - focus_associations (list[dict] | None): only with indication_id;
              best-scored diseases among the indication and its sub-diseases
            - provenance (Provenance | None)
        Association scores are relative (0-1) and depend on the amount of
        evidence: a high score is not proof of causality.
    """
    logger.info("Open Targets: fetch_open_targets started for '%s'", ensembl_id)

    if not ensembl_id:
        logger.warning("Open Targets: no Ensembl id, cannot query")
        return _empty_result(ensembl_id, found=None)

    # The focus filter needs the indication's descendants first (sequential)
    indication = None
    focus_ids: list[str] = []
    if indication_id:
        indication, descendants = await _focus_ids(indication_id)
        if indication is None:
            logger.warning("Open Targets: unknown indication '%s', ignored", indication_id)
        else:
            focus_ids = [indication["id"], *descendants]

    main_call = _graphql(
        QUERY,
        {
            "ensemblId": ensembl_id,
            "size": TOP_ASSOCIATIONS,
            "hasFocus": indication is not None,
            "indicationIds": [indication["id"]] if indication else [],
        },
    )

    async def _no_focus():
        return None

    # Main query and focus chunks are independent -> parallel
    data, focus = await asyncio.gather(
        main_call, _fetch_focus(ensembl_id, focus_ids) if focus_ids else _no_focus()
    )
    if data is None:
        return _empty_result(ensembl_id, found=None)
    if indication is not None and focus is None:
        # Indication known but focus failed: has_evidence becomes None (unknown),
        # never a false "no evidence"
        logger.error("Open Targets: focus query failed for '%s'", indication["id"])

    target = data.get("target")
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
        "indication": _parse_indication(indication, target, focus) if indication else None,
        "focus_associations": _parse_associations(focus["rows"]) if indication and focus else None,
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
