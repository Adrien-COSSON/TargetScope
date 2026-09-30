# backend\schemas\evidence.py

# Import libraries

from pydantic import BaseModel


class EvidenceScore(BaseModel):
    dimension: str | None = None  # like "pharmacology", "genetics", "clinical"...
    score: float | None = None  # like float between 0 and 1
    label: str | None = None  # like "Strong" / "Moderate" / "Limited" / "None"
    explanation: str | None = (
        None  # like "47 active compounds, 18 with IC50 < 100nM..."
    )
    items_count: int | None = (
        None  # number of evidence which have contributed to the score
    )


class EvidenceGap(BaseModel):
    domain: str | None = None  # like "genetics"
    description: str | None = None  # like "No human genetic evidence identified"
    severity: str | None = None  # like "high" / "medium" / "low"
    why_it_matters: str | None = None  # like "Human genetic validation strengthens..."


class SafetySignal(BaseModel):
    tissue: str | None = None  # like "liver"
    expression_level: str | None = None  # like "High"
    signal_type: str | None = None  # like "potential_safety_consideration"
    statement: str | None = (
        None  # like "High physiological expression detected in liver..."
    )
