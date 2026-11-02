# backend/schemas/synthesis.py
"""Models for the Claude-written synthesis of a TargetAssessment.

Two families:
  - LLM output (ClaimDraft, SynthesisDraft): the exact shape Claude must return,
    enforced by structured outputs (JSON schema validated by the API)
  - Final output (Claim, TargetSynthesis): drafts after the grounding check,
    with every evidence id resolved, so the frontend can answer "Why?"
"""

from typing import Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# What Claude returns (kept simple: no numeric or length constraints, which
# structured outputs do not support)
# ---------------------------------------------------------------------------
class ClaimDraft(BaseModel):
    text: str  # one sentence
    evidence_ids: list[str]  # ids from the dossier, e.g. ["F3", "S1"]


class SynthesisDraft(BaseModel):
    headline: str  # one-sentence verdict on the target (for the indication)
    strengths: list[ClaimDraft]
    risks: list[ClaimDraft]
    open_questions: list[ClaimDraft]  # what a project team should resolve next


# ---------------------------------------------------------------------------
# What TargetScope serves
# ---------------------------------------------------------------------------
class EvidenceRef(BaseModel):
    """One dossier entry, as cited by a claim (the answer to "Why?")."""

    id: str  # "F3"
    kind: Literal["fact", "gap", "safety", "context"]
    dimension: str | None = None  # e.g. "tractability"
    text: str  # the fact itself, with its source in parentheses
    sources: list[str] = []


class Claim(BaseModel):
    text: str
    evidence: list[EvidenceRef]  # resolved citations, never empty


class TargetSynthesis(BaseModel):
    headline: str
    strengths: list[Claim]
    risks: list[Claim]
    open_questions: list[Claim]
    model: str  # model that wrote it, e.g. "claude-sonnet-5-5"
    dropped_claims: int = 0  # claims rejected by the grounding check
    disclaimer: str = (
        "AI-written summary of the evidence above. Every statement cites the "
        "facts it relies on; it adds no information of its own."
    )
