# backend\services\uniprot.py

# Import libraries
import logging
import re

from backend.utils.http_client import UNIPROT_BASE_URL, get_json

logger = logging.getLogger(__name__)

ENTRY_URL = "https://www.uniprot.org/uniprotkb/{accession}/entry"
PDB_URL = "https://www.rcsb.org/structure/{pdb_id}"

# UniProt keyword categories shown on the identity card
CARD_KEYWORD_CATEGORIES = ("Molecular function", "Domain", "Biological process")


def _xref_property(xref: dict, key: str) -> str | None:
    """Value of one property of a UniProt cross-reference (e.g. PDB "Resolution")."""
    for prop in xref.get("properties", []):
        if prop.get("key") == key:
            value = prop.get("value")
            return None if value in (None, "-") else value
    return None


def _parse_resolution(value: str | None) -> float | None:
    """'2.30 A' -> 2.3; None for NMR entries ('-')."""
    if not value:
        return None
    try:
        return float(value.split()[0])
    except (ValueError, IndexError):
        return None


def _chain_coverage(chains: str | None) -> tuple[int | None, int | None]:
    """'A/B=707-1027' -> (707, 1027): residues of the protein in the structure.

    Several chain blocks ('A=1-500, B=600-900') give the widest span.
    """
    if not chains:
        return None, None
    starts, ends = [], []
    for match in re.finditer(r"=(\d+)-(\d+)", chains):
        starts.append(int(match.group(1)))
        ends.append(int(match.group(2)))
    return (min(starts), max(ends)) if starts else (None, None)


def _parse_structures(xrefs: list[dict]) -> list[dict]:
    """PDB entries of the protein, best resolution first (NMR / unknown last).

    UniProt lists them as cross-references, so no extra call to RCSB is needed.
    """
    structures = []
    for xref in xrefs:
        if xref.get("database") != "PDB":
            continue
        start, end = _chain_coverage(_xref_property(xref, "Chains"))
        structures.append({
            "pdb_id": xref.get("id"),
            "method": _xref_property(xref, "Method"),
            "resolution": _parse_resolution(_xref_property(xref, "Resolution")),
            "start": start,
            "end": end,
            "url": PDB_URL.format(pdb_id=xref.get("id")),
        })
    return sorted(structures, key=lambda s: (s["resolution"] is None, s["resolution"] or 0))


def _parse_domains(xrefs: list[dict], database: str) -> list[dict]:
    """Pfam / InterPro / PANTHER entries: [{"id": "PF08767", "name": "CRM1_C"}, ...]."""
    return [
        {"id": x.get("id"), "name": _xref_property(x, "EntryName")}
        for x in xrefs
        if x.get("database") == database
    ]


def _parse_family(comments: list[dict]) -> str | None:
    """'Belongs to the exportin family.' -> 'exportin family'."""
    for comment in comments:
        if comment.get("commentType") != "SIMILARITY":
            continue
        for text in comment.get("texts", []):
            value = (text.get("value") or "").strip().rstrip(".")
            if value:
                return re.sub(r"^Belongs to the\s+", "", value)
    return None


def _parse_names(data: dict) -> dict:
    """Recommended protein name, short names and gene synonyms."""
    description = data.get("proteinDescription", {})
    recommended = description.get("recommendedName", {})
    gene = (data.get("genes") or [{}])[0]
    return {
        "protein_name": (recommended.get("fullName") or {}).get("value"),
        "short_names": [s.get("value") for s in recommended.get("shortNames", [])],
        "alternative_names": [
            (alt.get("fullName") or {}).get("value")
            for alt in description.get("alternativeNames", [])
        ],
        "gene_synonyms": [s.get("value") for s in gene.get("synonyms", [])],
    }


def _keywords_by_category(keywords: list[dict]) -> dict[str, list[str]]:
    """{"Molecular function": ["RNA-binding"], "Domain": ["Repeat"], ...}."""
    grouped: dict[str, list[str]] = {}
    for kw in keywords:
        category = kw.get("category")
        if category in CARD_KEYWORD_CATEGORIES:
            grouped.setdefault(category, []).append(kw.get("name"))
    return grouped


async def fetch_uniprot(uniprot_id: str) -> dict:
    """Fetch and extract key biological data for a protein from UniProt REST API.

    Queries the UniProtKB entry for the given accession and extracts fields
    relevant to target assessment: function, localization, enzymatic activity,
    disease associations, tissue specificity, and structural properties.

    Args:
        uniprot_id (str): UniProt accession number (e.g. "P00533").

    Returns:
        dict: Extracted biological data with keys:
            - function (str | None): Protein function description.
            - function_pmids (list[str]): PubMed IDs supporting the function annotation.
            - subcellular_location (list[str] | None): List of subcellular locations.
            - molecular_weight (int | None): Molecular weight in Da.
            - protein_length (int | None): Number of amino acids.
            - has_kinase_domain (bool): True if UniProt keyword KW-0418 is present.
            - ec_numbers (list[str]): EC numbers of catalyzed reactions (may be empty).
            - disease_associations (list[dict]): name, accession (UniProt DI-), mim (OMIM ID).
            - tissue_specificity (str | None): Tissue expression description.
            - isoform_count (int | None): Number of annotated isoforms.
            - found (bool | None): True on success, None if UniProt could not
              be reached (then the dict holds only this key)
    """
    logger.info("UniProt: fetch_uniprot started for '%s'", uniprot_id)

    data = await get_json(
        f"{UNIPROT_BASE_URL}/uniprotkb/{uniprot_id}.json", source="UniProt"
    )

    if data is None:
        return {"found": None}

    function_text = None
    subcellular_location = None
    molecular_weight = None
    protein_length = None
    has_kinase_domain = False
    disease_associations = []
    tissue_specificity = None
    isoform_count = None
    ec_numbers = []
    function_pmids = []

    for comment in data.get("comments", []):
        comment_type = comment.get("commentType")
        if comment_type == "FUNCTION" and function_text is None:
            texts = comment.get("texts", [])
            if texts:
                function_text = texts[0].get("value")
                for ev in texts[0].get("evidences", []):
                    if (
                        ev.get("source") == "PubMed"
                        and ev.get("id") not in function_pmids
                    ):
                        function_pmids.append(ev.get("id"))
        if comment_type == "SUBCELLULAR LOCATION" and subcellular_location is None:
            subcellular_location = [
                loc.get("location", {}).get("value")
                for loc in comment.get("subcellularLocations", [])
            ]
        if comment_type == "CATALYTIC ACTIVITY":
            ec = comment.get("reaction", {}).get("ecNumber")
            if ec and ec not in ec_numbers:
                ec_numbers.append(ec)
        if comment_type == "DISEASE":
            disease = comment.get("disease", {})
            if disease.get("diseaseId"):
                disease_associations.append(
                    {
                        "name": disease.get("diseaseId"),
                        "accession": disease.get("diseaseAccession"),
                        "mim": disease.get("diseaseCrossReference", {}).get("id"),
                    }
                )
        if comment_type == "TISSUE SPECIFICITY" and tissue_specificity is None:
            texts = comment.get("texts", [])
            if texts:
                tissue_specificity = texts[0].get("value")
        if comment_type == "ALTERNATIVE PRODUCTS" and isoform_count is None:
            isoform_count = len(comment.get("isoforms", []))

    if function_text:
        function_text = re.sub(r"\s*\(PubMed:[^)]*\)", "", function_text)

    molecular_weight = data.get("sequence", {}).get("molWeight")
    protein_length = data.get("sequence", {}).get("length")
    keywords = [kw.get("id") for kw in data.get("keywords", [])]
    has_kinase_domain = "KW-0418" in keywords

    xrefs = data.get("uniProtKBCrossReferences", [])
    comments = data.get("comments", [])

    logger.info("UniProt: fetch_uniprot ended for '%s'", uniprot_id)

    return {
        "found": True,
        "accession": data.get("primaryAccession", uniprot_id),
        "url": ENTRY_URL.format(accession=data.get("primaryAccession", uniprot_id)),
        **_parse_names(data),
        "family": _parse_family(comments),
        "keywords": _keywords_by_category(data.get("keywords", [])),
        "pfam": _parse_domains(xrefs, "Pfam"),
        "interpro": _parse_domains(xrefs, "InterPro"),
        "structures": _parse_structures(xrefs),
        "has_alphafold": any(x.get("database") == "AlphaFoldDB" for x in xrefs),
        "function": function_text,
        "function_pmids": function_pmids,
        "subcellular_location": subcellular_location,
        "molecular_weight": molecular_weight,
        "protein_length": protein_length,
        "has_kinase_domain": has_kinase_domain,
        "ec_numbers": ec_numbers,
        "disease_associations": disease_associations,
        "tissue_specificity": tissue_specificity,
        "isoform_count": isoform_count,
    }
