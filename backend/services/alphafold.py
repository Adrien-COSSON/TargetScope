# backend/services/alphafold.py
"""AlphaFold DB: predicted 3D structure of the protein (Tool Box).

The mean pLDDT (0-100) says how confident the prediction is: > 90 very high,
70-90 confident, < 50 often disordered regions. The model files are served
by URL, so the frontend can draw the structure without storing it.
"""

import logging

from backend.utils.http_client import ALPHAFOLD_BASE_URL, get_json

logger = logging.getLogger(__name__)

ENTRY_URL = "https://alphafold.ebi.ac.uk/entry/{accession}"


async def fetch_alphafold(uniprot_id: str | None) -> dict:
    """Metadata of the AlphaFold model of a UniProt accession.

    found=False when AlphaFold has no model (404), None when unreachable.
    """
    if not uniprot_id:
        return {"found": None}
    # 404 = no model for this accession: "no data", not a failure
    data = await get_json(
        f"{ALPHAFOLD_BASE_URL}/api/prediction/{uniprot_id}", source="AlphaFold", not_found=[]
    )
    if data is None:
        return {"found": None}
    model = data[0] if isinstance(data, list) and data else None
    if not model:
        return {"found": False}
    return {
        "found": True,
        "model_id": model.get("modelEntityId"),
        "mean_plddt": model.get("globalMetricValue"),
        "fraction_very_high": model.get("fractionPlddtVeryHigh"),
        "fraction_very_low": model.get("fractionPlddtVeryLow"),
        "pdb_url": model.get("pdbUrl"),
        "cif_url": model.get("cifUrl"),
        "version": model.get("latestVersion"),
        "url": ENTRY_URL.format(accession=uniprot_id),
    }
