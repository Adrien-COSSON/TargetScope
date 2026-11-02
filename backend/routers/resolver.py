# backend/routers/resolver.py
"""Target resolution endpoint."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.schemas.target import Target
from backend.services.resolver import ResolutionUnavailableError, resolve_target

logger = logging.getLogger(__name__)

router = APIRouter(tags=["target"])


class ResolveRequest(BaseModel):
    query: str  # any gene identifier: "EGFR", "HER1", "1956", "P00533"...


@router.post("/resolve", response_model=Target)
async def resolve(request: ResolveRequest) -> Target:
    """Resolve a gene/protein query to a canonical Target.

    404 if nothing matches, 503 if MyGene.info is unreachable.
    """
    logger.info("POST /resolve: '%s'", request.query)
    try:
        # resolve_target is async since the httpx migration: it must be awaited
        # (the previous sync version returned a coroutine instead of a Target)
        return await resolve_target(request.query)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ResolutionUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
