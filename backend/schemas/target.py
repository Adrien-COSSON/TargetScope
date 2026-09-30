# backend\schemas\target.py

# Import libraries

from pydantic import BaseModel


class Provenance(BaseModel):
    source: str | None = None  # like "Uniprot"
    source_id: str | None = None  # like "P00533"
    retrieved_at: str | None = None  # like "2026-09-22"
    confidence: str | None = None  # like "high" / "medium" / "low"
    url: str | None = None


class EvidenceItem(BaseModel):
    domain: str  # like "pharmacology", "genetics", "expression"
    field: str  # like "IC50", "variant_type", "tissue_expression_level"
    value: str  # like "Receptor tyrosine kinase"
    provenance: Provenance


class Target(BaseModel):
    input_query: str  # the query of the user
    gene_symbol: str  # like "EGFR"
    name: str  # like "Epidermal growth factor receptor"
    uniprot_id: str | None = None  # like "P00533"
    entrez_id: int | None = None  # like 1956
    ensembl_id: str | None = None  # like "ENSG00000146648"
    chembl_id: str | None = None  # like "CHEMBL203"
    aliases: list[str] = []  # like ["HER1", "ErbB1", "mENA", ...]
    species: str = "Homo sapiens"
    resolution_provenance: Provenance | None = None
