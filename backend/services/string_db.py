# backend\services\string_db.py

# Import libraries
import logging
import requests
import time

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds

def fetch_string(uniprot_id: str, gene_name: str) -> dict:
    """Fetch protein-protein interactions from STRING for a given UniProt accession.

    Queries the STRING API for the top 10 interactions involving the target,
    filtered to Homo sapiens (taxon 9606).

    Args:
        uniprot_id: UniProt accession, e.g. "P04637".
        gene_name: HGNC gene symbol, e.g. "TP53". Used to identify the target
            within each interaction pair and extract the partner.

    Returns:
        A dict with the following keys:
            - uniprot_id (str): the queried accession
            - interactions (list[dict]): each item contains:
                  - partner (str): gene symbol of the interacting protein
                  - score (float): STRING combined score (0 to 1)
            - error (str | None): error message if the request failed,
              None otherwise
    """  
    string_db_url = f'https://string-db.org/api/json/network?identifiers={uniprot_id}&species=9606&limit=10'
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(string_db_url, timeout = REQUEST_TIMEOUT)
            
            if response.status_code == 200:
                raw: list[dict] = response.json()
                interactions = _parse_interactions(raw, gene_name)
                return {"uniprot_id": uniprot_id, "interactions": interactions, "error": None}
                
            if response.status_code == 404:
                logger.info("Reactome: no pathways found for %s (404)", uniprot_id)
                return {"uniprot_id": uniprot_id, "interactions": [], "error": None}
            
            # Any other HTTP error — retry on 5xx, fail immediately on 4xx
            if 400 <= response.status_code < 500:
                msg = f"String_db HTTP {response.status_code} for {uniprot_id}."
                logger.error(msg)
                return {"uniprot_id": uniprot_id, "interactions": [], "error": msg}
            
            # 5xx — transient, worth retrying
            last_error = f"String_db HTTP {response.status_code} (attempt {attempt}/{MAX_RETRIES})."
            logger.warning(last_error)
        
        except requests.exceptions.Timeout:
            last_error = f"String_db request timed out (attempt {attempt}/{MAX_RETRIES})."
            logger.warning(last_error)

        except requests.exceptions.ConnectionError as exc:
            last_error = f"String_db connection error (attempt {attempt}/{MAX_RETRIES}): {exc}."
            logger.warning(last_error)

        except requests.exceptions.RequestException as exc:
            last_error = f"String_db unexpected request error: {exc}."
            logger.error(last_error)
            break  # non-retryable

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
        
    return {"uniprot_id": uniprot_id, "interactions": [], "error": last_error}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_interactions(raw: list[dict], gene_name: str) -> list[dict]:
    """Extract direct interactions with the target from raw STRING response.

    Filters interaction pairs where gene_name is involved and returns
    the partner gene symbol and combined score for each.

    Args:
        raw: list of interaction dicts returned by the STRING API.
        gene_name: HGNC gene symbol of the query target.

    Returns:
        List of dicts with keys 'partner' (str) and 'score' (float).
        Pairs not involving gene_name are silently skipped.
    """
    interactions = []
    for item in raw:
        name_a = item.get("preferredName_A", "")
        name_b = item.get("preferredName_B", "")
        score = item.get("score", 0.0)
        
        if name_a == gene_name:
            partner = name_b
        elif name_b == gene_name:
            partner = name_a
        else:
            continue  # paire sans rapport avec la cible
        
        interactions.append({"partner": partner, "score": score})
    return interactions