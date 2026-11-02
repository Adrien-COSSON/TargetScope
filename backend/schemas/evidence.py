# backend/schemas/evidence.py
"""Pydantic models produced by evidence_engine.py.

TargetAssessment is the single object the engine returns. The router serves it,
Streamlit draws it (radar, gaps, safety) and the PDF report renders it, so
every field a user sees must live here.

Design rules (see evidence_engine.py):
  - no global score: one score per dimension, drawn as a radar
  - safety is a list of signals, never a score (no signal != safe)
  - "no evidence" (a gap) and "data unavailable" (a source failed) are
    always kept apart
"""

from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.target import Target

# The five radar axes. Literal = Pydantic rejects any other value,
# so a typo in the engine fails loudly instead of creating a 6th axis.
Dimension = Literal[
    "human_genetics",
    "clinical_precedence",
    "tractability",
    "biological_knowledge",
    "animal_models",
]

ScoreLabel = Literal["Strong", "Moderate", "Limited", "None", "Unavailable"]
Severity = Literal["high", "medium", "low"]


class EvidenceScore(BaseModel):
    """One radar axis: a 0-1 score, a label and the facts behind it."""

    dimension: Dimension
    # None = could not be assessed (every source of this dimension failed);
    # 0.0 = assessed, and nothing was found. The radar draws them differently.
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    label: ScoreLabel = "Unavailable"
    explanation: str | None = None  # one sentence, e.g. "Approved drug (erlotinib)"
    # The verifiable facts that produced the score, one per line,
    # e.g. "11,544 activities with pChEMBL >= 7 (ChEMBL)"
    facts: list[str] = []
    sources: list[str] = []  # sources that contributed, e.g. ["ChEMBL", "Open Targets"]


class EvidenceGap(BaseModel):
    """Something a target assessment would expect and did not find."""

    dimension: Dimension
    description: str  # e.g. "No human genetic evidence identified"
    severity: Severity
    why_it_matters: str  # e.g. "Targets with human genetic support succeed more often..."


SafetyCategory = Literal[
    "clinical_liability",  # curated adverse events (Open Targets safety)
    "genetic_constraint",  # human loss-of-function intolerance (gnomAD LOEUF)
    "mouse_phenotype",  # lethality in mouse models (MGI)
    "drug_label",  # black box warning / withdrawal of a drug on the target (ChEMBL)
    "tissue_expression",  # high expression in a critical tissue (HPA)
]


class SafetySignal(BaseModel):
    """A potential safety consideration, worded as such.

    Signals are prompts for expert review, never conclusions: the engine only
    reports what the sources say, with their caveats.
    """

    category: SafetyCategory
    statement: str  # e.g. "Skin rash reported with inhibition (Brennan et al. 2024)"
    source: str  # e.g. "Open Targets", "MGI", "gnomAD (via Open Targets)"
    signal_type: Literal["potential_safety_consideration"] = (
        "potential_safety_consideration"
    )
    caveat: str | None = None  # e.g. "Pools all mouse alleles, not only knockouts"
    tissue: str | None = None  # tissue_expression signals only, e.g. "liver"
    expression_level: str | None = None  # e.g. "nTPM 412"
    references: list[str] = []  # PMIDs, URLs, publication names


class SourceStatus(BaseModel):
    """What happened with one data source during the assessment.

    ok:          the source answered with data
    no_data:     the source answered, but has nothing for this target
    unavailable: the source could not be reached -> its dimensions are
                 partly unknown, and nothing it would have said is a gap
    """

    source: str
    status: Literal["ok", "no_data", "unavailable"]
    detail: str | None = None


class IndicationContext(BaseModel):
    """The disease the user focused on, and how the target relates to it."""

    disease_id: str  # EFO / MONDO id
    disease_name: str
    direct_score: float = 0.0  # Open Targets score on the indication itself
    associated_descendant_count: int = 0  # sub-diseases with evidence
    best_descendant: str | None = None  # e.g. "plasma cell myeloma (0.64)"
    # True / False, or None if Open Targets could not answer
    has_evidence: bool | None = None


class LiteratureTrend(BaseModel):
    """Publication volume and direction, shown next to the knowledge axis."""

    total_count: int | None = None  # PubMed, all time
    recent_count: int | None = None  # PubMed, last 5 years
    trend: Literal["Emerging", "Growing", "Stable", "Declining", "Unknown"] = "Unknown"
    timeline: dict[str, int | None] = {}  # Europe PMC, {"1990": 3, ...}


class TargetAssessment(BaseModel):
    """Everything the engine concludes about one target (and indication)."""

    target: Target
    indication: IndicationContext | None = None  # None = no indication chosen
    scores: list[EvidenceScore]  # always the five dimensions, in radar order
    gaps: list[EvidenceGap] = []
    safety_signals: list[SafetySignal] = []
    literature: LiteratureTrend = LiteratureTrend()
    sources: list[SourceStatus] = []
    generated_at: str  # ISO 8601 UTC timestamp
    engine_version: str  # bump when scoring rules change: old reports stay explainable
