# backend\schemas\target.py

# Import libraries
from pydantic import BaseModel
from typing import Optional, List

class Provenance(BaseModel):
    source: Optional[str] = None    # like "Uniprot"
    source_id: Optional[str] = None # like "P00533"
    retrieved_at: Optional[str] = None  # like "2026-09-22"
    confidence: Optional[str] = None    # like "high" / "medium" / "low"
    url: Optional[str] = None

class EvidenceItem(BaseModel):
    domain: str # like "pharmacology", "genetics", "expression"
    field: str  # like "IC50", "variant_type", "tissue_expression_level"
    value: str  # like "Receptor tyrosine kinase"
    provenance: Provenance

class Target(BaseModel):
    input_query: str    # the query of the user
    gene_symbol: str    # like "EGFR"
    uniprot_id: Optional[str] = None    # like "P00533"
    entrez_id: Optional[int] = None     # like 1956
    ensembl_id: Optional[str] = None    # like "ENSG00000146648"
    chembl_id: Optional[str] = None     # like "CHEMBL203"
    aliases: List[str] = []     # like ["HER1", "ErbB1", "mENA", ...]
    species: str = 'Homo sapiens'
    resolution_provenance: Optional[Provenance] = None