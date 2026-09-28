# backend\services\uniprot.py

# Import libraries
from backend.utils.logger import logger
import requests



def fetch_uniprot(uniprot_id: str):
    """Fetch and extract key biological data for a protein from UniProt REST API.

    Queries the UniProtKB entry for the given accession and extracts fields
    relevant to target assessment: function, localization, enzymatic activity,
    disease associations, tissue specificity, and structural properties.

    Args:
        uniprot_id (str): UniProt accession number (e.g. "P00533").

    Returns:
        dict: Extracted biological data with keys:
            - function (str | None): Protein function description.
            - subcellular_location (list[str] | None): List of subcellular locations.
            - molecular_weight (int | None): Molecular weight in Da.
            - protein_length (int | None): Number of amino acids.
            - has_kinase_domain (bool): True if UniProt keyword KW-0418 is present.
            - catalytic_activity (str | None): EC number (e.g. "2.7.10.1").
            - disease_associations (list[str] | None): List of associated disease IDs.
            - tissue_specificity (str | None): Tissue expression description.
            - isoform_count (int | None): Number of annotated isoforms.

    Raises:
        requests.HTTPError: If the UniProt API call fails.
    """
    logger.info(f"Fetch_uniprot initiated for uniprot_id: {uniprot_id}")
    
    uniprot_url = f'https://rest.uniprot.org/uniprotkb/{uniprot_id}.json'
    response = requests.get(uniprot_url)
    data = response.json()
    
    function_text = None
    subcellular_location = None
    molecular_weight = None
    protein_length = None
    has_kinase_domain = None
    catalytic_activity = None
    disease_associations = None
    tissue_specificity = None
    isoform_count = None
    
    for comment in data.get('comments', []):
        if comment['commentType'] == 'FUNCTION' and function_text is None:
            function_text = comment['texts'][0]['value']
        if comment['commentType'] == 'SUBCELLULAR LOCATION' and subcellular_location is None:
            subcellular_location = [loc['location']['value'] for loc in comment.get('subcellularLocations', [])]
        if comment['commentType'] == 'CATALYTIC ACTIVITY':
            catalytic_activity = comment.get('reaction', {}).get('ecNumber')
        if comment['commentType'] == 'DISEASE':
            if disease_associations is None:
                disease_associations = []
            disease_associations.append(comment.get('disease', {}).get('diseaseId'))
        if comment['commentType'] == 'TISSUE SPECIFICITY':
            tissue_specificity = comment['texts'][0]['value']
        if comment['commentType'] == 'ALTERNATIVE PRODUCTS':
            isoform_count = len(comment.get('isoforms', []))
            
    molecular_weight = data.get('sequence', {}).get('molWeight')
    protein_length = data.get('sequence', {}).get('length')
    keywords = [kw.get('id') for kw in data.get('keywords', [])]
    has_kinase_domain = 'KW-0418' in keywords
    
    logger.info(f"fetch_uniprot succeeded for {uniprot_id}")
    
    return {"function": function_text,
            "subcellular_location": subcellular_location,
            "molecular_weight": molecular_weight,
            "protein_length": protein_length,
            "has_kinase_domain": has_kinase_domain,
            "catalytic_activity": catalytic_activity,
            "disease_associations": disease_associations,
            "tissue_specificity": tissue_specificity,
            "isoform_count": isoform_count}