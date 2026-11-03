# backend/services/clinvar.py
"""ClinVar service: germline variant evidence for a gene.

Feeds two assessment dimensions:
  - genetic evidence (how many pathogenic variants, which diseases)
  - safety (a gene with many germline pathogenic variants is functionally constrained)
"""

import asyncio
import logging
import re
from collections import Counter

from backend.utils.ncbi import (
    ESEARCH_URL,
    ESUMMARY_URL,
    extract_count,
    extract_ids,
    ncbi_get,
    ncbi_params,
)

logger = logging.getLogger(__name__)

VARIANT_URL = "https://www.ncbi.nlm.nih.gov/clinvar/variation/{uid}/"

PATHOGENIC_SAMPLE_SIZE = 200  # variants used to aggregate conditions
TOP_CONDITIONS = 10
TOP_VARIANTS = 10

# Filters validated against querytranslation (scripts/explore_clinvar.py).
# Careful: invalid ClinVar filters are silently ignored, not rejected,
# which returns the unfiltered count without any error.
CLASSIFICATION_FILTERS = {
    "pathogenic": "clinsig_pathogenic[prop]",
    "likely_pathogenic": "clinsig_likely_pathogenic[prop]",
    "uncertain_significance": "clinsig_vus[prop]",
    "benign": "clinsig_benign[prop]",
}

# ClinVar review status, from strongest to weakest evidence.
# There is no revstat_* search filter, so ranking happens after esummary.
REVIEW_STATUS_RANK = {
    "practice guideline": 4,
    "reviewed by expert panel": 3,
    "criteria provided, multiple submitters, no conflicts": 2,
    "criteria provided, single submitter": 1,
}

# Placeholder condition names that carry no information
UNINFORMATIVE_CONDITIONS = {"not provided", "not specified", "see cases"}

# Preferred ontology for the condition identifier, in order
CONDITION_ID_SOURCES = ("MONDO", "OMIM", "MedGen")


def _params(**extra) -> dict:
    """Shortcut: E-utilities params for the clinvar database."""
    return ncbi_params("clinvar", **extra)


def _condition_id(trait: dict) -> str | None:
    """Return the preferred ontology id of a trait (MONDO > OMIM > MedGen)."""
    # Build {database: id} once, then pick the first preferred database present
    xrefs = {x.get("db_source"): x.get("db_id") for x in trait.get("trait_xrefs", [])}
    for db in CONDITION_ID_SOURCES:
        if xrefs.get(db):
            return xrefs[db]
    return None


AA3 = {
    "Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C", "Gln": "Q", "Glu": "E",
    "Gly": "G", "His": "H", "Ile": "I", "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F",
    "Pro": "P", "Ser": "S", "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V", "Ter": "*",
}  # fmt: skip
P_DOT = re.compile(r"\(p\.([A-Z][a-z]{2})(\d+)([A-Z][a-z]{2}|=|fs|\*)")


def _protein_change(record: dict) -> str | None:
    """Short protein notation as used in papers (E571K), or None (non-coding...).

    esummary gives "protein_change" ("E571K, E554K": one per transcript, the
    first is the canonical one); the HGVS title "(p.Glu571Lys)" is the fallback.
    """
    listed = (record.get("protein_change") or "").split(",")[0].strip()
    if listed:
        return listed
    match = P_DOT.search(record.get("title") or "")
    if not match:
        return None
    ref, pos, alt = match.groups()
    return f"{AA3.get(ref, ref)}{pos}{AA3.get(alt, alt)}"


def _parse_variant(uid: str, record: dict) -> dict:
    """Extract the fields TargetScope needs from one esummary record."""
    classification = record.get("germline_classification", {})
    # Keep only named, informative conditions
    conditions = [
        {"name": trait.get("trait_name"), "id": _condition_id(trait)}
        for trait in classification.get("trait_set", [])
        if trait.get("trait_name")
        and trait.get("trait_name").lower() not in UNINFORMATIVE_CONDITIONS
    ]
    return {
        "accession": record.get("accession"),
        "title": record.get("title"),
        "protein_change": _protein_change(record),
        "classification": classification.get("description"),
        "review_status": classification.get("review_status"),
        "conditions": conditions,
        "molecular_consequences": record.get("molecular_consequence_list", []),
        "url": VARIANT_URL.format(uid=uid),
    }


async def _fetch_variant_details(ids: list[str]) -> list[dict] | None:
    """Fetch and parse esummary records for a list of ClinVar ids."""
    if not ids:
        return []  # no pathogenic variant: nothing to fetch, not a failure
    # One esummary call for all ids (comma-separated), instead of one call per variant
    data = await ncbi_get(ESUMMARY_URL, _params(id=",".join(ids)), source="ClinVar")
    if data is None:
        return None
    result = data.get("result", {})
    # "uids" gives the record order; each record is keyed by its uid in the same dict
    return [
        _parse_variant(uid, result[uid])
        for uid in result.get("uids", [])
        if uid in result
    ]


def _aggregate_conditions(variants: list[dict]) -> list[dict]:
    """Count how many sampled pathogenic variants are linked to each condition."""
    counts: Counter = Counter()
    ids: dict[str, str | None] = {}
    for variant in variants:
        # set(): one variant counts once per condition, even if listed twice
        for name in {c["name"] for c in variant["conditions"]}:
            counts[name] += 1
        for c in variant["conditions"]:
            ids.setdefault(c["name"], c["id"])  # keep the first id seen
    return [
        {"name": name, "id": ids.get(name), "variant_count": n}
        for name, n in counts.most_common(TOP_CONDITIONS)
    ]


def _top_variants(variants: list[dict]) -> list[dict]:
    """Return the best-reviewed variants first (stable sort keeps recency order)."""
    ranked = sorted(
        variants,
        key=lambda v: REVIEW_STATUS_RANK.get(v["review_status"], 0),
        reverse=True,
    )
    return ranked[:TOP_VARIANTS]


async def fetch_clinvar(gene_name: str) -> dict:
    """Fetch germline variant evidence from ClinVar for a given gene.

    Runs in parallel:
      - one esearch for the total number of variants
      - one esearch per classification (counts only)
      - one esearch for a sample of PATHOGENIC_SAMPLE_SIZE pathogenic ids
    then one esummary call on that sample, used to:
      - aggregate the conditions linked to pathogenic variants
      - pick the TOP_VARIANTS best-reviewed pathogenic variants

    Args:
        gene_name: HGNC gene symbol, e.g. "BRCA1".

    Returns:
        A dict with keys:
            - gene_name (str)
            - total_variants (int | None)
            - classification_counts (dict[str, int | None]): pathogenic,
              likely_pathogenic, uncertain_significance, benign
            - sample_size (int | None): pathogenic variants actually analysed
            - top_conditions (list[dict] | None): name, id (MONDO/OMIM/MedGen),
              variant_count within the sample
            - top_variants (list[dict] | None): accession, title, classification,
              review_status, conditions, molecular_consequences, url
        Counts and lists are None when ClinVar could not be reached,
        0 or [] when the gene has no matching variant.
        A variant can carry several classifications, so counts may overlap.
        The sample holds the most recently added pathogenic variants, not a
        random draw: condition counts are indicative, not exhaustive.
    """
    logger.info("ClinVar: fetch_clinvar started for '%s'", gene_name)

    gene_term = f"{gene_name}[gene]"

    # Build the coroutines first, await them all together below
    total_call = ncbi_get(
        ESEARCH_URL, _params(term=gene_term, retmax=0), source="ClinVar"
    )
    count_calls = [
        ncbi_get(
            ESEARCH_URL,
            _params(term=f"{gene_term} AND {flt}", retmax=0),
            source="ClinVar",
        )
        for flt in CLASSIFICATION_FILTERS.values()
    ]
    sample_ids_call = ncbi_get(
        ESEARCH_URL,
        _params(
            term=f"{gene_term} AND {CLASSIFICATION_FILTERS['pathogenic']}",
            retmax=PATHOGENIC_SAMPLE_SIZE,
        ),
        source="ClinVar",
    )

    # gather() returns results in the order the coroutines were passed;
    # *count_data collects the remaining ones (one per classification filter)
    total_data, sample_ids_data, *count_data = await asyncio.gather(
        total_call, sample_ids_call, *count_calls
    )

    # zip relies on dicts keeping insertion order: names and results stay aligned
    classification_counts = {
        name: extract_count(data)
        for name, data in zip(CLASSIFICATION_FILTERS, count_data)
    }

    # esummary depends on the sampled ids, so it runs after the parallel batch
    sample_ids = extract_ids(sample_ids_data)
    variants = (
        None if sample_ids is None else await _fetch_variant_details(sample_ids)
    )

    result = {
        "gene_name": gene_name,
        "total_variants": extract_count(total_data),
        "classification_counts": classification_counts,
        "sample_size": None if variants is None else len(variants),
        "top_conditions": None if variants is None else _aggregate_conditions(variants),
        "top_variants": None if variants is None else _top_variants(variants),
    }

    logger.info(
        "ClinVar: fetch_clinvar ended for '%s' (total=%s, sample=%s)",
        gene_name,
        result["total_variants"],
        result["sample_size"],
    )
    return result
