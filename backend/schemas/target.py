# backend\schemas\target.py

# Import libraries
from pydantic import BaseModel
from typing import Optional, List

class Provenance(BaseModel):
    source: Optional[str] = None
    source_id: Optional[str] = None
    retrieved_at: Optional[str] = None
    confidence: Optional[str] = None
    url: Optional[str] = None

class EvidenceItem(BaseModel):
    domain: str # like "pharmacology", "genetics", "expression"
    field: str # like "IC50", "variant_type", "tissue_expression_level"
    value: str
    provenance: Provenance

class Target(BaseModel):
    input_query: str
    gene_symbol: str
    uniprot_id: Optional[str] = None
    entrez_id: Optional[int] = None
    ensembl_id: Optional[str] = None
    chembl_id: Optional[str] = None
    aliases: List[str] = []
    species: str = 'Homo sapiens'
    resolution_provenance: Optional[Provenance] = None