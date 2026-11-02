# backend/routers/synthesis.py
"""Claude synthesis endpoint (optional: works only with an API key)."""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from backend.schemas.evidence import TargetAssessment
from backend.schemas.synthesis import TargetSynthesis
from backend.services.synthesis import synthesize

logger = logging.getLogger(__name__)

router = APIRouter(tags=["synthesis"])


class SynthesisRequest(BaseModel):
    assessment: TargetAssessment  # as returned by POST /assess
    # Bring your own key (public demo). Never logged; falls back to .env.
    api_key: str | None = None


@router.post("/synthesis", response_model=TargetSynthesis | None)
async def synthesis(request: SynthesisRequest) -> TargetSynthesis | None:
    """Grounded summary of an assessment. null when no key or the API failed:
    the assessment stays complete without it."""
    return await synthesize(request.assessment, api_key=request.api_key)
