# backend\services\uniprot.py

# Import libraries
import logging
import requests
import time
import re

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # seconds

def fetch_uniprot(uniprot_id: str) -> dict:
    """Fetch and extract key biological data for a protein from UniProt REST API.

    Queries the UniProtKB entry for the given accession and extracts fields
    relevant to target assessment: function, localization, enzymatic activity,
    disease associations, tissue specificity, and structural properties.

    Args:
        uniprot_id (str): UniProt accession number (e.g. "P00533").

    Returns:
        dict: Extracted biological data with keys:
            - function (str | None): Protein function description.
            - function_pmids (list[str]): PubMed IDs supporting the function annotation.
            - subcellular_location (list[str] | None): List of subcellular locations.
            - molecular_weight (int | None): Molecular weight in Da.
            - protein_length (int | None): Number of amino acids.
            - has_kinase_domain (bool): True if UniProt keyword KW-0418 is present.
            - ec_numbers (list[str]): EC numbers of catalyzed reactions (may be empty).
            - disease_associations (list[dict]): name, accession (UniProt DI-), mim (OMIM ID).
            - tissue_specificity (str | None): Tissue expression description.
            - isoform_count (int | None): Number of annotated isoforms.
            - empty dict on failure
    """
    logger.info("UniProt: fetch_uniprot started for '%s'", uniprot_id)
    
    uniprot_url = f'https://rest.uniprot.org/uniprotkb/{uniprot_id}.json'
    
    for attempt in range(1, MAX_RETRIES +1):
        try:
            response = requests.get(uniprot_url, timeout=REQUEST_TIMEOUT)
    
            response.raise_for_status()
            data = response.json()
    
            function_text = None
            subcellular_location = None
            molecular_weight = None
            protein_length = None
            has_kinase_domain = False
            disease_associations = []
            tissue_specificity = None
            isoform_count = None
            ec_numbers = []
            function_pmids = []
    
            for comment in data.get('comments', []):
                comment_type = comment.get('commentType')
                if comment_type == 'FUNCTION' and function_text is None:
                    texts = comment.get('texts', [])
                    if texts:
                        function_text = texts[0].get('value')
                        for ev in texts[0].get('evidences', []):
                            if ev.get('source') == 'PubMed' and ev.get('id') not in function_pmids:
                                function_pmids.append(ev.get('id'))               
                if comment_type == 'SUBCELLULAR LOCATION' and subcellular_location is None:
                    subcellular_location = [loc.get('location', {}).get('value') for loc in comment.get('subcellularLocations', [])]
                if comment_type == 'CATALYTIC ACTIVITY':
                    ec = comment.get('reaction', {}).get('ecNumber')
                    if ec and ec not in ec_numbers:
                        ec_numbers.append(ec)                    
                if comment_type == 'DISEASE':
                    disease = comment.get('disease', {})
                    if disease.get('diseaseId'):
                        disease_associations.append({
                            "name": disease.get('diseaseId'),
                            "accession": disease.get('diseaseAccession'),
                            "mim": disease.get('diseaseCrossReference', {}).get('id'),
                        })                      
                if comment_type == 'TISSUE SPECIFICITY' and tissue_specificity is None:
                    texts = comment.get('texts', [])
                    if texts:
                        tissue_specificity = texts[0].get('value')
                if comment_type == 'ALTERNATIVE PRODUCTS' and isoform_count is None:
                    isoform_count = len(comment.get('isoforms', []))
                   
            if function_text:
                function_text = re.sub(r"\s*\(PubMed:[^)]*\)", "", function_text)
            molecular_weight = data.get('sequence', {}).get('molWeight')
            protein_length = data.get('sequence', {}).get('length')
            keywords = [kw.get('id') for kw in data.get('keywords', [])]
            has_kinase_domain = 'KW-0418' in keywords
            
            logger.info("UniProt: fetch_uniprot ended for '%s'", uniprot_id)
            
            return {"function": function_text,
                    "function_pmids": function_pmids,
                    "subcellular_location": subcellular_location,
                    "molecular_weight": molecular_weight,
                    "protein_length": protein_length,
                    "has_kinase_domain": has_kinase_domain,
                    "ec_numbers": ec_numbers,
                    "disease_associations": disease_associations,
                    "tissue_specificity": tissue_specificity,
                    "isoform_count": isoform_count}
            
        except requests.exceptions.RequestException as e:
            logger.warning("UniProt: attempt error %d/%d for '%s': %s", attempt, MAX_RETRIES, uniprot_id, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
            else:
                logger.error("UniProt: definitive failure after %d attempts for '%s'", MAX_RETRIES, uniprot_id)
                return {}