# backend\services\resolver.py

# Import libraries
from backend.utils.logger import logger
import requests
from backend.schemas.target import Provenance, Target
import datetime


def resolve_target(query: str) -> Target:
    """Resolve a gene/protein query to a canonical Target object via MyGene.info.

    Accepts any gene identifier (symbol, alias, Entrez ID, Ensembl ID, protein name)
    and returns a normalized Target with official symbol, name, and cross-references.

    Args:
        query (str): Gene identifier to resolve (e.g. "EGFR", "p53", "1956", "ENSG00000146648").

    Returns:
        Target: Canonical target object with gene_symbol, name, cross-references,
                and resolution_provenance metadata.

    Raises:
        ValueError: If no results are found for the given query.
        requests.HTTPError: If the MyGene.info API call fails.
    """
    logger.info("Resolve_target initiated")
    my_gene_url = f"https://mygene.info/v3/query?q={query}&species=human&fields=symbol,name,entrezgene,ensembl.gene,alias,uniprot"
    response = requests.get(my_gene_url)
    data = response.json()
    hits = data["hits"]
    if len(hits) == 0:
        logger.info('No data obtained from mygene => try another query!')
        raise ValueError(f"No results found for query: '{query}'")
    else:
        hit = hits[0]
        logger.info(f"resolve_target succeeded: {hit['symbol']} (Entrez: {hit.get('entrezgene')})")
        return Target(input_query = query,
                  gene_symbol = hit['symbol'],
                  name = hit['name'],
                  uniprot_id = hit.get('uniprot', {}).get('Swiss-Prot'),
                  entrez_id = hit.get('entrezgene'),
                  ensembl_id = hit.get('ensembl', {}).get('gene'),
                  aliases = hit.get('alias', []),
                  species = 'Homo sapiens',
                  resolution_provenance = Provenance(source = 'mygene.info',
                                                     source_id = str(hit['entrezgene']) if hit.get('entrezgene') else None,
                                                     retrieved_at = datetime.date.today().isoformat(),
                                                     confidence = 'high',
                                                     url = my_gene_url))
       
        