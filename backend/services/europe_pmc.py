# backend/services/europe_pmc.py  (ajout)

# Import libraries
import logging
import requests
import time
from datetime import date

logger = logging.getLogger(__name__)

EUROPEPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def publication_timeline(gene_name: str) -> dict[str, int]:
    """
    Retourne un histogramme {année: count} des publications EuropePMC
    pour un nom de gène/protéine, via la facette PUB_YEAR.

    Args:
        gene_name: Nom ou alias du gène (ex. "BRCA1", "TP53").

    Returns:
        Dict trié par année croissante. Vide si aucun résultat.

    Raises:
        requests.exceptions.HTTPError: Si l'API renvoie un code d'erreur HTTP.
        requests.exceptions.RequestException: En cas de timeout ou de problème réseau.
    """
    url = f"{EUROPEPMC_BASE}/search"

    pub_count_year = {}
    start_year = 1990
    
    for year in range(start_year, date.today().year + 1):
        params = {"query": f"{gene_name} AND PUB_YEAR:{year}",
                "resultType": "lite",
                "pageSize": 1,
                "format": "json"}
        response = requests.get(url, params=params, timeout=30.0)
        response.raise_for_status()
        time.sleep(0.3)

        data = response.json()
        hit_count = data.get("hitCount", 0)
        pub_count_year[str(year)] = hit_count

    return dict(sorted(pub_count_year.items()))