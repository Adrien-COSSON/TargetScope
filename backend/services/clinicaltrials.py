# backend/services/clinicaltrials.py

# Import libraries
import asyncio
import logging
from backend.utils.http_client import CLINICALTRIALS_BASE_URL, get_json

logger = logging.getLogger(__name__)

MAX_PAGES = 20  # safety cap on timeline pagination

async def fetch_trials(gene_name: str, page_size: int = 10) -> list[dict]:
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
    """   
    logger.info("ClinicalTrials: fetch_trials started for '%s'", gene_name)
    
    params = {"query.term": gene_name,
              "query.intr": gene_name,
              "pageSize": page_size,
              "format": "json",
              "fields": "NCTId,BriefTitle,OverallStatus,StartDate,Phase,Condition,BriefSummary"}
    
    raw = await get_json(f"{CLINICALTRIALS_BASE_URL}/studies", params=params, source="ClinicalTrials")
    
    if raw is None: 
        return []
            
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
                
    logger.info("ClinicalTrials: fetch_trials ended — %d trials collected for '%s'", len(trials), gene_name)
            
    return trials


async def trials_timeline(gene_name: str, page_size: int = 1000) -> dict[str, int]:
    """
    Fetch the number of clinical trials per year from ClinicalTrials.gov
    for a given gene or protein name, paginating through all results.

    Args:
        gene_name (str): Gene or protein name or alias (e.g. "BRCA1", "TP53").
        page_size (int): Number of trials per page (default: 1000).

    Returns:
        dict[str, int]: Dict mapping year to trial count, sorted in ascending order.
            Empty dict on failure.
    """    
    logger.info("ClinicalTrials: trials_timeline started for '%s'", gene_name)
    
    params = {"query.term": gene_name,
              "query.intr": gene_name, 
              "pageSize": page_size, 
              "format": "json", 
              "fields": "StartDate"}

    trials_years = {}
    
    for page in range(MAX_PAGES):
        raw = await get_json(f"{CLINICALTRIALS_BASE_URL}/studies", params=params, source="ClinicalTrials")
        if raw is None:
            logger.error("ClinicalTrials: page %d failed for '%s', timeline discarded", page + 1, gene_name)
            return {}

        for study in raw.get("studies", []):
            protocol = study.get("protocolSection", {})
            start_date = protocol.get("statusModule", {}).get("startDateStruct", {}).get("date", "")
            year = start_date[:4]
            if year.isdigit():
                trials_years[year] = trials_years.get(year, 0) + 1
            
        next_token = raw.get("nextPageToken")
        if not next_token:
            break
        params["pageToken"] = next_token
        await asyncio.sleep(0.5)
    else:
        logger.warning("ClinicalTrials: MAX_PAGES reached for '%s', timeline truncated", gene_name)
        
    if not trials_years:
        logger.warning("ClinicalTrials: no trials found for '%s'", gene_name)

    logger.info("ClinicalTrials: trials_timeline ended — %d years, %d trials collected for '%s'",
                len(trials_years), sum(trials_years.values()), gene_name)

    return dict(sorted(trials_years.items()))