# backend\utils\provenance.py

# Import libraries
from datetime import datetime
from schemas.target import Provenance
from utils.logger import logger

def make_provenance(source: str, source_id: str = None, 
                    confidence: str = "medium", url: str = None
                    ) -> Provenance:
    """
    Build a Provenance object with automatic retrieval date.
    
    Args:
        source: Data source name (e.g. "UniProt", "ChEMBL")
        source_id: Identifier in the source (e.g. "P00533")
        confidence: Evidence confidence level ("high", "medium", "low")
        url: Direct URL to the source record
    
    Returns:
        Provenance object with current date as retrieved_at
    """
    retrieved_at = datetime.now().strftime("%Y-%m-%d")
    return Provenance(source=source,
                      source_id=source_id,
                      retrieved_at=retrieved_at,
                      confidence=confidence,
                      url=url)