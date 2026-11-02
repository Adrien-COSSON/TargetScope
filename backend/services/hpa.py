import logging
 
from backend.utils.http_client import HPA_BASE_URL, get_json
from backend.utils.provenance import make_provenance
 
logger = logging.getLogger(__name__)
 
PROGNOSTIC_PREFIX = "Cancer prognostics - "
 
# HPA IHC reliability -> EvidenceItem confidence
RELIABILITY_TO_CONFIDENCE = {"Enhanced": "high",
                             "Supported": "medium",
                             "Approved": "low",
                             "Uncertain": "low"}
 
 
def _confidence_from_reliability(reliability: str | None) -> str:
    return RELIABILITY_TO_CONFIDENCE.get(reliability, "low")
 
 
def _to_float_dict(value: dict | None) -> dict[str, float] | None:
    """Convert HPA {label: "44.6"} dicts to floats. Returns None when HPA gives null."""
    if not isinstance(value, dict):
        return None
    result = {}
    for label, raw in value.items():
        try:
            result[label] = float(raw)
        except (TypeError, ValueError):
            continue
    return result or None
 
 
def _parse_expression(data: dict) -> dict:
    return {"rna_tissue_specificity": data.get("RNA tissue specificity"),
            "rna_tissue_distribution": data.get("RNA tissue distribution"),
            "rna_tissue_specific_ntpm": _to_float_dict(data.get("RNA tissue specific nTPM")),
            "rna_single_cell_specificity": data.get("RNA single cell type specificity"),
            "rna_single_cell_specific_ncpm": _to_float_dict(data.get("RNA single cell type specific nCPM")),
            "protein_tissue_specificity": data.get("Protein tissue specificity"),
            "reliability_ih": data.get("Reliability (IH)")}
 
 
def _parse_cancer_expression(data: dict) -> dict:
    """RNA expression across cancer types (TCGA data in the HPA Pathology Atlas).

    Feeds the "disease_expression" axis for oncology indications.
    The unit of the specific-expression field changed across HPA releases
    (FPKM, nTPM, now pTPM), so the key is found by prefix rather than hard-coded.

    Caveat: HPA works on the MEDIAN expression per cancer type, so it misses
    biomarker-defined subgroups (ERBB2 is "Low cancer specificity" because
    HER2 is amplified in only ~15-20% of breast cancers). Context, not proof.
    """
    specific_key = next(
        (k for k in data if k.startswith("RNA cancer specific") and isinstance(data[k], dict)),
        None,
    )
    return {
        # e.g. "Cancer enhanced (breast cancer)", "Low cancer specificity"
        "specificity": data.get("RNA cancer specificity"),
        # numeric companion of "specificity" (higher = more cancer-type specific)
        "specificity_score": data.get("RNA cancer specificity score"),
        # e.g. "Detected in all", "Detected in many"
        "distribution": data.get("RNA cancer distribution"),
        # {cancer type: expression} for the cancers where it is enriched
        "specific_expression": _to_float_dict(data.get(specific_key)) if specific_key else None,
        "unit": specific_key.removeprefix("RNA cancer specific ").strip() if specific_key else None,
    }


def _parse_localization(data: dict) -> dict:
    return {"main": data.get("Subcellular main location") or [],
            "additional": data.get("Subcellular additional location") or [],
            "secretome": data.get("Secretome location")}
 
 
def _parse_prognostics(data: dict) -> list[dict]:
    """Keep only significant prognostic associations, sorted by p-value."""
    prognostics = []
    for key, value in data.items():
        if not key.startswith(PROGNOSTIC_PREFIX) or not isinstance(value, dict):
            continue
        if not value.get("is_prognostic"):
            continue
 
        label = key.removeprefix(PROGNOSTIC_PREFIX)
        cancer, sep, cohort = label.rpartition(" (")
        if not sep:
            cancer, cohort = label, None
        else:
            cohort = cohort.rstrip(")")
 
        try:
            p_value = float(value.get("p_val"))
        except (TypeError, ValueError):
            p_value = None
 
        prognostics.append({"cancer": cancer,
                            "cohort": cohort,
                            "type": value.get("prognostic type") or None,
                            "p_value": p_value})
 
    prognostics.sort(key=lambda p: (p["p_value"] is None, p["p_value"] or 0.0))
    return prognostics
 
 
async def fetch_hpa(ensembl_id: str) -> dict:
    """Fetch expression, localization and cancer data from the Human Protein Atlas.

    Returns found=None (and empty blocks) when HPA could not be reached:
    get_json returns None on failure, it never raises. The previous version
    assumed an exception and would have crashed on data.get().
    """
    logger.info("Fetching HPA data for %s", ensembl_id)
    url = f"{HPA_BASE_URL}/{ensembl_id}.json"
    data = await get_json(url, source="HPA")

    if data is None:
        logger.error("HPA: no data for %s (request failed or unknown id)", ensembl_id)
        return {"found": None, "gene": None, "uniprot_ids": [], "expression": None,
                "localization": None, "cancer_expression": None, "prognostics": None,
                "provenance": None}

    expression = _parse_expression(data)
    localization = _parse_localization(data)
    prognostics = _parse_prognostics(data)
 
    result = {"found": True,
              "gene": data.get("Gene"),
              "uniprot_ids": data.get("Uniprot") or [],
              "expression": expression,
              "localization": localization,
              "cancer_expression": _parse_cancer_expression(data),
              "prognostics": prognostics,
              "provenance": make_provenance(source="HPA",
                                            source_id=ensembl_id,
                                            url=url,
                                            confidence=_confidence_from_reliability(expression["reliability_ih"]))}
 
    logger.info("HPA data retrieved for %s: %d significant prognostics, main location=%s",
                ensembl_id,
                len(prognostics),
                localization["main"])
    
    return result