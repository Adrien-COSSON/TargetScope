# backend/routers/assessment.py
"""Assessment endpoints: indication menus, disease search, full assessment."""

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.schemas.evidence import TargetAssessment
from backend.services.assessment import run_assessment
from backend.services.open_targets import fetch_disease_options, search_diseases
from backend.services.resolver import ResolutionUnavailableError, resolve_target

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assessment"])


class AssessRequest(BaseModel):
    query: str  # the target, as typed by the user
    indication_id: str | None = None  # EFO / MONDO id from the menus, optional


class AssessResponse(BaseModel):
    assessment: TargetAssessment  # radar, gaps, safety, synthesis input
    raw: dict  # every source's result, for the detailed tabs (drug table, pathways...)


async def _resolve_or_http_error(query: str):
    """Resolver errors mapped to HTTP status codes, shared by the endpoints."""
    try:
        return await resolve_target(query)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ResolutionUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/targets/{query}/indications")
async def indications(query: str) -> dict:
    """Therapeutic areas and diseases associated with the target (indication menus)."""
    target = await _resolve_or_http_error(query)
    options = await fetch_disease_options(target.ensembl_id)
    if options is None:
        raise HTTPException(status_code=503, detail="Open Targets is unreachable")
    return {"target": target.model_dump(), **options}


@router.get("/diseases/search")
async def disease_search(q: str = Query(min_length=2)) -> list[dict]:
    """Free-text disease search ("search another disease...")."""
    hits = await search_diseases(q)
    if hits is None:
        raise HTTPException(status_code=503, detail="Open Targets is unreachable")
    return hits


@router.post("/assess", response_model=AssessResponse)
async def assess(request: AssessRequest) -> AssessResponse:
    """Run the full assessment: every source in parallel, then the evidence engine."""
    logger.info("POST /assess: '%s' x %s", request.query, request.indication_id)
    try:
        assessment, raw = await run_assessment(request.query, request.indication_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ResolutionUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return AssessResponse(assessment=assessment, raw=raw)
