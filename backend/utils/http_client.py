# backend\utils\http_client.py

# Import libraries
from utils.logger import logger
import httpx

# APIs URLs
UNIPROT_BASE_URL        = "https://rest.uniprot.org"
MYGENE_BASE_URL         = "https://mygene.info/v3"
PUBMED_BASE_URL         = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OPENALEX_BASE_URL       = "https://api.openalex.org"
CHEMBL_BASE_URL         = "https://www.ebi.ac.uk/chembl/api/data"
HPA_BASE_URL            = "https://www.proteinatlas.org/api"
CLINVAR_BASE_URL        = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"  # même base que PubMed
COSMIC_BASE_URL         = "https://cancer.sanger.ac.uk/cosmic/api"
MGI_BASE_URL            = "https://www.informatics.jax.org/api"
CLINICALTRIALS_BASE_URL = "https://clinicaltrials.gov/api/v2"
REACTOME_BASE_URL       = "https://reactome.org/ContentService"
STRING_BASE_URL         = "https://string-db.org/api"
LENS_BASE_URL           = "https://api.lens.org"

# Shared client
_client: httpx.AsyncClient = None

async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "TargetScope/1.0"}
        )
        logger.info("HTTP client initialized")
    return _client

async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("HTTP client closed")