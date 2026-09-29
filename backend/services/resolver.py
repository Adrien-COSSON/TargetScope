# backend/services/resolver.py

# Import libraries
import logging
import re
from typing import Any

from backend.schemas.target import Target
from backend.utils.http_client import MYGENE_BASE_URL, get_json
from backend.utils.provenance import make_provenance

logger = logging.getLogger(__name__)

FIELDS = "symbol,name,entrezgene,ensembl.gene,alias,uniprot,type_of_gene"
MAX_HITS = 20

# Official UniProt accession format
UNIPROT_ACCESSION = re.compile(
    r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})$"
)


class ResolutionUnavailableError(Exception):
    """Raised when the resolution service (MyGene.info) cannot be reached."""


def _first(value: Any) -> Any:
    """Return the first element if value is a list, else the value itself.

    MyGene.info returns a single value when a field has one entry and a list
    when it has several (e.g. uniprot.Swiss-Prot, ensembl).
    """
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _as_list(value: Any) -> list:
    """Return value as a list (MyGene.info returns a bare string for single aliases)."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _is_protein_coding(hit: dict) -> bool:
    return hit.get("type_of_gene") == "protein-coding"


def _search_strategies(query: str) -> list[tuple[str, str]]:
    """Return the ordered (strategy, MyGene query) pairs to try for an input query.

    Identifier-shaped inputs are searched on their dedicated field first. Every
    input then falls back to an exact symbol/alias search, and finally to a
    free-text search (always resolved with low confidence).
    """
    term = query.strip().replace('"', "")
    upper = term.upper()
    strategies = []

    if term.isdigit():
        strategies.append(("entrez", f"entrezgene:{term}"))
    elif upper.startswith("ENSG"):
        strategies.append(("ensembl", f"ensembl.gene:{upper}"))
    elif UNIPROT_ACCESSION.match(upper):
        strategies.append(("uniprot", f"uniprot:{upper}"))

    strategies.append(("symbol_or_alias", f'symbol:"{term}" OR alias:"{term}"'))
    strategies.append(("free_text", term))
    return strategies


def _select_hit(hits: list[dict], query: str, strategy: str) -> tuple[dict, str]:
    """Pick the best hit and a confidence level ("high" / "medium" / "low").

    Protein-coding genes are preferred (targets for drug discovery); within each
    group MyGene's score order is kept (sorted() is stable).
    """
    term = query.strip().upper()
    ranked = sorted(hits, key=lambda h: not _is_protein_coding(h))

    if strategy in ("entrez", "ensembl", "uniprot"):
        return ranked[0], "high"

    if strategy == "symbol_or_alias":
        symbol_matches = [h for h in ranked if str(h.get("symbol", "")).upper() == term]
        if symbol_matches:
            return symbol_matches[0], "high"

        alias_matches = [
            h for h in ranked
            if term in (str(a).upper() for a in _as_list(h.get("alias")))
        ]
        if alias_matches:
            coding = [h for h in alias_matches if _is_protein_coding(h)]
            if len(coding) == 1:
                return coding[0], "medium"
            logger.warning(
                "MyGene: ambiguous alias '%s' shared by %s",
                query, [h.get("symbol") for h in alias_matches],
            )
            return alias_matches[0], "low"

    logger.warning("MyGene: no exact symbol/alias match for '%s', best free-text hit used", query)
    return ranked[0], "low"


async def resolve_target(query: str) -> Target:
    """Resolve a gene/protein query to a canonical Target object via MyGene.info.

    Accepts any gene identifier (symbol, alias, Entrez ID, Ensembl ID, UniProt
    accession, protein name) and returns a normalized Target with official symbol,
    name, cross-references and a confidence level reflecting how the match was made:
    high (identifier or exact official symbol), medium (unique protein-coding alias),
    low (ambiguous alias or free-text match).

    Args:
        query (str): Gene identifier to resolve (e.g. "EGFR", "p53", "1956", "ENSG00000146648").

    Returns:
        Target: Canonical target object with gene_symbol, name, cross-references,
            and resolution_provenance metadata.

    Raises:
        ValueError: If no results are found for the given query.
        ResolutionUnavailableError: If MyGene.info cannot be reached.
    """
    logger.info("MyGene: resolve_target started for '%s'", query)

    hit, confidence, strategy = None, None, None

    for strategy, q in _search_strategies(query):
        params = {"q": q, "species": "human", "fields": FIELDS, "size": MAX_HITS}
        data = await get_json(f"{MYGENE_BASE_URL}/query", params=params, source="MyGene")

        if data is None:
            logger.error("MyGene: service unavailable while resolving '%s'", query)
            raise ResolutionUnavailableError("MyGene.info is unavailable")

        hits = data.get("hits", [])
        if hits:
            hit, confidence = _select_hit(hits, query, strategy)
            break

    if hit is None:
        logger.warning("MyGene: no match for '%s'", query)
        raise ValueError(f"No results found for query: '{query}'")

    entrez_id = hit.get("entrezgene")
    uniprot_id = _first(hit.get("uniprot", {}).get("Swiss-Prot"))
    ensembl = _first(hit.get("ensembl"))
    ensembl_id = ensembl.get("gene") if isinstance(ensembl, dict) else None

    logger.info("MyGene: resolve_target ended: '%s' -> %s (Entrez: %s, strategy: %s, confidence: %s)",
                query, hit.get("symbol"), entrez_id, strategy, confidence)

    return Target(input_query=query,
                  gene_symbol=hit.get("symbol"),
                  name=hit.get("name"),
                  uniprot_id=uniprot_id,entrez_id=entrez_id,
                  ensembl_id=ensembl_id,
                  aliases=_as_list(hit.get("alias")),species="Homo sapiens",
                  resolution_provenance=make_provenance(
                      source="mygene.info",
                      source_id=str(entrez_id) if entrez_id else None,
                      confidence=confidence,
                      url=f"{MYGENE_BASE_URL}/gene/{entrez_id}" if entrez_id else None))