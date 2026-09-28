# backend\routers\resolver.py

# Import libraries
from backend.utils.logger import logger
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.schemas.target import Target
from backend.services.resolver import resolve_target

router = APIRouter()

class ResolverRequests(BaseModel):
    query: str

@router.post("/resolve", response_model=Target)
def resolve(request: ResolverRequests):
    """Resolve a gene/protein query to a canonical Target object.

    Args:
        request (ResolverRequests): Request body containing the query string.

    Returns:
        Target: Canonical target object with gene_symbol, name, cross-references,
                and resolution_provenance metadata.

    Raises:
        HTTPException 404: If no results are found for the given query.
    """
    logger.info(f"POST /resolve — query: '{request.query}'")
    try:
        result = resolve_target(request.query)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))