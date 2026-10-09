import asyncio
import logging
from collections import Counter
 
from backend.config import settings
from backend.utils.http_client import CLINVAR_BASE_URL, get_json
 
logger = logging.getLogger(__name__)
 
ESEARCH_URL = f"{CLINVAR_BASE_URL}/esearch.fcgi"
ESUMMARY_URL = f"{CLINVAR_BASE_URL}/esummary.fcgi"
VARIANT_URL = "https://www.ncbi.nlm.nih.gov/clinvar/variation/{uid}/"
NCBI_TOOL = "TargetScope"
 
PATHOGENIC_SAMPLE_SIZE = 200  # variants used to aggregate conditions
TOP_CONDITIONS = 10
TOP_VARIANTS = 10
 
# Filters validated against querytranslation (scripts/explore_clinvar.py)
CLASSIFICATION_FILTERS = {
    "pathogenic": "clinsig_pathogenic[prop]",
    "likely_pathogenic": "clinsig_likely_pathogenic[prop]",
    "uncertain_significance": "clinsig_vus[prop]",
    "benign": "clinsig_benign[prop]",
}
 
# ClinVar review status, from strongest to weakest evidence
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
 
 
def _base_params(**extra) -> dict:
    """Build the E-utilities parameters shared by every ClinVar call."""
    params = {
        "db": "clinvar",
        "retmode": "json",
        "tool": NCBI_TOOL,
        **extra,
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
 
 
def _extract_ids(data: dict | None) -> list[str] | None:
    """Return the id list from an esearch response, None if the request failed."""
    if data is None:
        return None
    return data.get("esearchresult", {}).get("idlist", [])
 
 
def _condition_id(trait: dict) -> str | None:
    """Return the preferred ontology id of a trait (MONDO > OMIM > MedGen)."""
    xrefs = {x.get("db_source"): x.get("db_id") for x in trait.get("trait_xrefs", [])}
    for db in CONDITION_ID_SOURCES:
        if xrefs.get(db):
            return xrefs[db]
    return None
 
 
def _parse_variant(uid: str, record: dict) -> dict:
    """Extract the fields TargetScope needs from one esummary record."""
    classification = record.get("germline_classification", {})
    conditions = [
        {"name": trait.get("trait_name"), "id": _condition_id(trait)}
        for trait in classification.get("trait_set", [])
        if trait.get("trait_name")
        and trait.get("trait_name").lower() not in UNINFORMATIVE_CONDITIONS
    ]
    return {
        "accession": record.get("accession"),
        "title": record.get("title"),
        "classification": classification.get("description"),
        "review_status": classification.get("review_status"),
        "conditions": conditions,
        "molecular_consequences": record.get("molecular_consequence_list", []),
        "url": VARIANT_URL.format(uid=uid),
    }
 
 
async def _fetch_variant_details(ids: list[str]) -> list[dict] | None:
    """Fetch and parse esummary records for a list of ClinVar ids."""
    if not ids:
        return []
    data = await get_json(
        ESUMMARY_URL, params=_base_params(id=",".join(ids)), source="ClinVar"
    )
    if data is None:
        return None
    result = data.get("result", {})
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
            ids.setdefault(c["name"], c["id"])
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
 
    total_call = get_json(
        ESEARCH_URL, params=_base_params(term=gene_term, retmax=0), source="ClinVar"
    )
    count_calls = [
        get_json(
            ESEARCH_URL,
            params=_base_params(term=f"{gene_term} AND {flt}", retmax=0),
            source="ClinVar",
        )
        for flt in CLASSIFICATION_FILTERS.values()
    ]
    sample_ids_call = get_json(
        ESEARCH_URL,
        params=_base_params(
            term=f"{gene_term} AND {CLASSIFICATION_FILTERS['pathogenic']}",
            retmax=PATHOGENIC_SAMPLE_SIZE,
        ),
        source="ClinVar",
    )
 
    total_data, sample_ids_data, *count_data = await asyncio.gather(
        total_call, sample_ids_call, *count_calls
    )
 
    classification_counts = {
        name: _extract_count(data)
        for name, data in zip(CLASSIFICATION_FILTERS, count_data)
    }
 
    sample_ids = _extract_ids(sample_ids_data)
    variants = (
        None if sample_ids is None else await _fetch_variant_details(sample_ids)
    )
 
    result = {
        "gene_name": gene_name,
        "total_variants": _extract_count(total_data),
        "classification_counts": classification_counts,
        "sample_size": None if variants is None else len(variants),
        "top_conditions": None if variants is None else _aggregate_conditions(variants),
        "top_variants": None if variants is None else _top_variants(variants),
    }
 
    logger.info("ClinVar: fetch_clinvar ended for '%s'", gene_name)
    return result