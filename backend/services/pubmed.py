# backend\services\pubmed.py

# Import libraries
import logging
import requests
import time
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds

def fetch_pubmed(gene_name: str) -> dict:
    """Fetch publication metrics and recent articles from PubMed for a given gene.

    Performs three sequential API calls to NCBI E-utilities:
      1. esearch — retrieves total publication count and 10 most recent PMIDs
      2. esearch with date filter — retrieves publication count for the last 5 years
      3. efetch — retrieves title and year for each PMID

    Args:
        gene_name: HGNC gene symbol, e.g. "TP53".

    Returns:
        A dict with the following keys:
            - gene_name (str): the queried gene symbol
            - total_count (int): total number of PubMed publications
            - recent_count (int): publications from 2021 to present
            - articles (list[dict]): each item contains pmid, title, year
            - error (str | None): error message if a call failed, None otherwise
    """
    # First API call - esearch: total_count + pmids_list
    pmids_url = f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={gene_name}[Gene]&retmax=10&retmode=json'
    for attempt in range(1, MAX_RETRIES +1):
        try:
            response_pmids = requests.get(pmids_url, timeout = REQUEST_TIMEOUT)
            
            if response_pmids.status_code == 200:
                raw = response_pmids.json()
                result = raw.get("esearchresult", {})
                pmids_list: list[str] = result.get("idlist", [])
                total_count: int = int(result.get("count", 0))
    
            elif response_pmids.status_code == 404:
                logger.info("PubMed: no results for %s (404)", gene_name)
                return {"gene_name": gene_name, "pmids": [], "error": None}
            
            # Any other HTTP error — retry on 5xx, fail immediately on 4xx
            elif 400 <= response_pmids.status_code < 500:
                msg = f"PubMed HTTP {response_pmids.status_code} for {gene_name}."
                logger.error(msg)
                return {"gene_name": gene_name, "pmids": [], "error": msg}
            
            # 5xx — transient, worth retrying
            else:
                last_error = f"PubMed HTTP {response_pmids.status_code} (attempt {attempt}/{MAX_RETRIES})."
                logger.warning(last_error)
        
        except requests.exceptions.Timeout:
            last_error = f"Pubmed request timed out (attempt {attempt}/{MAX_RETRIES})."
            logger.warning(last_error)

        except requests.exceptions.ConnectionError as exc:
            last_error = f"Pubmed connection error (attempt {attempt}/{MAX_RETRIES}): {exc}."
            logger.warning(last_error)

        except requests.exceptions.RequestException as exc:
            last_error = f"Pubmed unexpected request error: {exc}."
            logger.error(last_error)
            break  # non-retryable

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
            
    # Guard — if pmids_list not defined, Call 1 fail
    if not pmids_list:
        return {"gene_name": gene_name, "pmids": [], "total_count": 0, "recent_count": 0, "articles": [], "error": last_error}
            
            
    # Second API call - esearch: recent_count
    recent_count = None
    recent_url = f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={gene_name}[Gene]&retmax=0&mindate=2021&maxdate=2026&datetype=pdat&retmode=json'
    
    for attempt in range(1, MAX_RETRIES +1):
        try:
            response_recent = requests.get(recent_url, timeout = REQUEST_TIMEOUT)

            if response_recent.status_code == 200:
                raw = response_recent.json()
                result = raw.get("esearchresult", {})
                recent_count: int = int(result.get("count", 0))
    
            elif response_recent.status_code == 404:
                logger.info("PubMed: no results for %s (404)", gene_name)
                return {"gene_name": gene_name, "pmids": [], "error": None}
            
            # Any other HTTP error — retry on 5xx, fail immediately on 4xx
            elif 400 <= response_recent.status_code < 500:
                msg = f"PubMed HTTP {response_recent.status_code} for {gene_name}."
                logger.error(msg)
                return {"gene_name": gene_name, "pmids": [], "error": msg}
            
            # 5xx — transient, worth retrying
            else:
                last_error = f"PubMed HTTP {response_recent.status_code} (attempt {attempt}/{MAX_RETRIES})."
                logger.warning(last_error)
        
        except requests.exceptions.Timeout:
            last_error = f"Pubmed request timed out (attempt {attempt}/{MAX_RETRIES})."
            logger.warning(last_error)

        except requests.exceptions.ConnectionError as exc:
            last_error = f"Pubmed connection error (attempt {attempt}/{MAX_RETRIES}): {exc}."
            logger.warning(last_error)

        except requests.exceptions.RequestException as exc:
            last_error = f"Pubmed unexpected request error: {exc}."
            logger.error(last_error)
            break  # non-retryable

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)    
    
    # Guard — if recent_count not defined, Call 2 fail
    if recent_count is None:
        return {"gene_name": gene_name, "pmids": pmids_list, "total_count": total_count, "recent_count": 0, "articles": [], "error": last_error}
            
    
        # Third API call - efetch: PMIDS titles
    ids = ",".join(pmids_list)
    title_url = f'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={ids}&retmode=xml'
    for attemps in range(1, MAX_RETRIES +1):
        try:
            response_title = requests.get(title_url, timeout = REQUEST_TIMEOUT)
            
            if response_title.status_code == 200:
                articles = _parse_articles(response_title.text)
                return {"gene_name": gene_name,
                        "total_count": total_count,
                        "recent_count": recent_count,
                        "articles": articles,
                        "error": None}
                
            elif response_title.status_code == 404:
                logger.info("PubMed: no results for %s (404)", gene_name)
                return {"gene_name": gene_name, "pmids": [], "error": None}
            
            # Any other HTTP error — retry on 5xx, fail immediately on 4xx
            elif 400 <= response_title.status_code < 500:
                msg = f"PubMed HTTP {response_title.status_code} for {gene_name}."
                logger.error(msg)
                return {"gene_name": gene_name, "pmids": [], "error": msg}
            
            # 5xx — transient, worth retrying
            else:
                last_error = f"PubMed HTTP {response_title.status_code} (attempt {attempt}/{MAX_RETRIES})."
                logger.warning(last_error)
        
        except requests.exceptions.Timeout:
            last_error = f"Pubmed request timed out (attempt {attempt}/{MAX_RETRIES})."
            logger.warning(last_error)

        except requests.exceptions.ConnectionError as exc:
            last_error = f"Pubmed connection error (attempt {attempt}/{MAX_RETRIES}): {exc}."
            logger.warning(last_error)

        except requests.exceptions.RequestException as exc:
            last_error = f"Pubmed unexpected request error: {exc}."
            logger.error(last_error)
            break  # non-retryable

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)    

    return {"gene_name": gene_name,
            "total_count": total_count,
            "recent_count": recent_count,
            "articles": articles,
            "error": None}


def _parse_articles(xml_text: str) -> list[dict]:
    """Parse PubMed efetch XML response and extract article metadata.

    Args:
        xml_text: raw XML string returned by the efetch endpoint.

    Returns:
        List of dicts with keys 'pmid' (str), 'title' (str), 'year' (str).
        Articles missing all three fields are still included with None values.
    """
    articles = []
    root = ET.fromstring(xml_text)
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID")
        title = article.findtext(".//ArticleTitle")
        year = article.findtext(".//PubDate/Year")
        articles.append({"pmid": pmid, "title": title, "year": year})
    return articles