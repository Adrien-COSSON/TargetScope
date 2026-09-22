# backend\schemas\evidence.py

# Import libraries
from pydantic import BaseModel
from typing import Optional

class EvidenceScore(BaseModel):
    dimension: Optional[str] = None   # like "pharmacology", "genetics", "clinical"...
    score: Optional[float] = None     # like float between 0 and 1
    label: Optional[str] = None       # like "Strong" / "Moderate" / "Limited" / "None"
    explanation: Optional[str] = None # like "47 active compounds, 18 with IC50 < 100nM..."
    items_count: Optional[int] = None # number of evidence which have contributed to the score

class EvidenceGap(BaseModel):
    domain: Optional[str] = None          # like "genetics"
    description: Optional[str] = None     # like "No human genetic evidence identified"
    severity: Optional[str] = None        # like "high" / "medium" / "low"
    why_it_matters: Optional[str] = None  # like "Human genetic validation strengthens..."

class SafetySignal(BaseModel):
    tissue: Optional[str] = None            # like "liver"
    expression_level: Optional[str] = None  # like "High"
    signal_type: Optional[str] = None       # like "potential_safety_consideration"
    statement: Optional[str] = None         # like "High physiological expression detected in liver..."