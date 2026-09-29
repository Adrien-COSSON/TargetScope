# backend/services/clinicaltrials.py

# Import libraries
import logging
import requests
import time
from datetime import date

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds

def fetch_trials(gene_name: str, page_size: int = 10) -> dict:
    """
    Fetch the most recent clinical trials from ClinicalTrials.gov
    for a given gene or protein name.

    Args:
        gene_name (str): Gene or protein name or alias (e.g. "BRCA1", "TP53").
        page_size (int): Number of trials to retrieve (default: 10).

    Returns:
        list[dict]: List of dicts with keys nct_id, brief_title,
            overall_status, start_date, phases, conditions,
            brief_summary, link. Empty list on failure.

    Raises:
        requests.exceptions.RequestException: If all MAX_RETRIES attempts fail.
    """
    base_url = "https://clinicaltrials.gov/api/v2/studies"
    params = {"query.term": gene_name, "pageSize": page_size, "format": "json"}
    
    for attempt in range(1, MAX_RETRIES +1):
        try:
            response_trials = requests.get(base_url, params=params, timeout=REQUEST_TIMEOUT)
            
            response_trials.raise_for_status()
            raw = response_trials.json()
            
            trials = []

            for study in raw.get('studies', []):
                protocol = study.get("protocolSection", {})

                identification = protocol.get("identificationModule", {})
                nct_id = identification.get("nctId", "")
                brief_title = identification.get("briefTitle", "")

                status = protocol.get("statusModule", {})
                overall_status = status.get("overallStatus", "")
                start_date = status.get("startDateStruct", {}).get("date", "")

                design = protocol.get("designModule", {})
                phases = design.get("phases", [])

                conditions = protocol.get("conditionsModule", {}).get("conditions", [])

                brief_summary = protocol.get("descriptionModule", {}).get("briefSummary", "")

                link = f"https://clinicaltrials.gov/study/{nct_id}"
                
                trials.append({'nct_id': nct_id,
                               'brief_title': brief_title,
                               'overall_status': overall_status,
                               'start_date': start_date,
                               'phases': phases,
                               'conditions': conditions,
                               'brief_summary': brief_summary,
                               'link': link})
            return trials
        
        except requests.exceptions.RequestException as e:
            logger.warning("ClinicalTrials: erreur tentative %d/%d pour '%s': %s", attempt, MAX_RETRIES, gene_name, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
            else:
                return []


def trials_timeline(gene_name: str, page_size: int = 1000) -> dict[str, int]:
    """
    Fetch the number of clinical trials per year from ClinicalTrials.gov
    for a given gene or protein name, paginating through all results.

    Args:
        gene_name (str): Gene or protein name or alias (e.g. "BRCA1", "TP53").
        page_size (int): Number of trials per page (default: 1000).

    Returns:
        dict[str, int]: Dict mapping year to trial count, sorted in ascending order.
            Empty dict on failure.

    Raises:
        requests.exceptions.RequestException: If all MAX_RETRIES attempts fail.
    """    
    base_url = "https://clinicaltrials.gov/api/v2/studies"
    params = {"query.term": gene_name, "pageSize": page_size, "format": "json"}
    
    for attempt in range(1, MAX_RETRIES +1):
        try:
            trials_years = {}
            while True:
                response_timeline = requests.get(base_url, params=params, timeout=REQUEST_TIMEOUT)
            
                response_timeline.raise_for_status()
                time.sleep(0.5)
                raw = response_timeline.json()

                for study in raw.get("studies", []):
                    protocol = study.get("protocolSection", {})
                    start_date = protocol.get("statusModule", {}).get("startDateStruct", {}).get("date", "")
                    year = start_date[:4]
                    if year.isdigit():
                        trials_years[year] = trials_years.get(year, 0) + 1
                    
                next_token = raw.get("nextPageToken")
                if next_token:
                    params["pageToken"] = next_token
                else:
                    break
                
            if not trials_years:
                logger.warning("ClinicalTrials: no trials found for '%s'", gene_name)
                    
            return dict(sorted(trials_years.items()))
            
        except requests.exceptions.RequestException as e:
            logger.warning("ClinicalTrials: erreur tentative %d/%d pour '%s': %s", attempt, MAX_RETRIES, gene_name, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
            else:
                return {}