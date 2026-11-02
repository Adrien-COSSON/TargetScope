# backend/services/evidence_engine.py
"""Evidence engine: turns raw service results into a TargetAssessment.

Rules of the engine (agreed design):
  - deterministic and explicit: thresholds below, no ML, no LLM.
    Claude (synthesis.py) only narrates the result, it never computes it.
  - three states, never two: evidence found / no evidence (a gap) /
    data unavailable (a source failed). A failed source is never a gap.
  - pure functions: every scorer takes service result dicts and returns
    plain values. No network here, so the whole logic is testable offline.

Every score is built the same way:
  1. walk down a list of tiers, from strongest to weakest
  2. the first tier whose condition holds gives the score
  3. each fact checked along the way is recorded, so the score explains itself
"""

import logging

from backend.schemas.evidence import EvidenceGap, EvidenceScore

logger = logging.getLogger(__name__)

ENGINE_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Configuration: every threshold of the engine, in one place.
# These are domain decisions. Changing one changes the scores, so bump
# ENGINE_VERSION whenever you edit this block.
# ---------------------------------------------------------------------------
CONFIG = {
    # Score -> label. Read top-down: first threshold reached wins.
    "labels": [(0.8, "Strong"), (0.5, "Moderate"), (0.01, "Limited"), (0.0, "None")],
    "tractability": {
        # pChEMBL >= 7 (<= 100 nM) activity records needed for "potent chemical matter"
        "potent_activities_min": 100,
        "tiers": {
            "approved_drug": 1.0,  # approved drug acting on the protein itself
            "clinical_candidate": 0.8,  # phase 1-3 drug acting on the protein itself
            # >= potent_activities_min potent activities. Caveat: ChEMBL counts say
            # nothing about selectivity (they may be pan-family compounds). V2 idea:
            # check the compounds' activity on related targets.
            "potent_chemistry": 0.6,
            # Drug only via a protein family or complex: clinical modulation is
            # proven, but not selectively. Ranked below potent chemistry on purpose:
            # for a best-in-class approach, selective matter matters more.
            "non_selective_clinical": 0.5,
            "some_chemistry": 0.4,  # quantitative activities, few or weak
            "predicted_only": 0.2,  # only structural predictions (pocket, druggable family)
            "nothing": 0.0,
        },
    },
}

# Open Targets tractability buckets that mean clinical precedence (not prediction)
OT_CLINICAL_BUCKETS = {"Approved Drug": 4.0, "Advanced Clinical": 3.0, "Phase 1 Clinical": 1.0}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def label_for(score: float | None) -> str:
    """Map a 0-1 score to its label; None means the dimension could not be assessed."""
    if score is None:
        return "Unavailable"
    for threshold, label in CONFIG["labels"]:
        if score >= threshold:
            return label
    return "None"


def _is_available(result: dict | None, key: str = "found") -> bool:
    """True if the service answered (found True or False), False if it failed.

    Services return found=None when the source could not be reached.
    """
    return result is not None and result.get(key) is not None


def _fmt(n: int | None) -> str:
    """12345 -> '12,345' (readable counts in facts)."""
    return f"{n:,}" if isinstance(n, int) else "unknown"


def _plural(n: int, word: str) -> str:
    """_plural(1, "drug") -> "1 drug", _plural(3, "drug") -> "3 drugs"."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# ---------------------------------------------------------------------------
# Dimension: tractability
# ---------------------------------------------------------------------------
def score_tractability(
    chembl: dict | None, open_targets: dict | None
) -> tuple[EvidenceScore, list[EvidenceGap]]:
    """Can the target be modulated, and with which modality?

    Sources:
      - ChEMBL: drugs and their phase, potent activities (pChEMBL >= 7)
      - Open Targets: tractability buckets per modality (clinical and predicted)

    Tiers (first match wins), see CONFIG["tractability"]["tiers"]:
      approved_drug          approved drug acting on the single protein
      clinical_candidate     clinical-stage drug acting on the single protein
      potent_chemistry       many potent compounds, no selective drug yet
      non_selective_clinical drug only via a family/complex
                             (e.g. vandetanib on the ErbB family: not selective)
      some_chemistry         some quantitative chemistry
      predicted_only     only predicted druggability (pocket, family, localisation)
      nothing
    """
    cfg = CONFIG["tractability"]
    tiers = cfg["tiers"]
    facts: list[str] = []
    sources: list[str] = []

    chembl_ok = _is_available(chembl)
    ot_ok = _is_available(open_targets)

    if not chembl_ok and not ot_ok:
        # Both sources failed: we know nothing, which is NOT "not tractable"
        return EvidenceScore(dimension="tractability", score=None, label="Unavailable",
                             explanation="ChEMBL and Open Targets could not be reached."), []

    # --- Facts from ChEMBL -------------------------------------------------
    best_single_phase = None  # best phase among drugs acting on the protein itself
    best_any_phase = None  # best phase including family / complex targets
    potent = None
    if chembl_ok:
        sources.append("ChEMBL")
        drugs = chembl.get("drugs") or []
        single = [d for d in drugs if d.get("via_target_type") == "SINGLE PROTEIN"]
        best_single_phase = max((d["max_phase"] for d in single if d.get("max_phase")), default=None)
        best_any_phase = max((d["max_phase"] for d in drugs if d.get("max_phase")), default=None)
        potent = chembl.get("potent_activity_count")

        summary = chembl.get("drug_summary") or {}
        if drugs:
            approved = [d["name"] for d in single if d.get("max_phase") == 4.0 and d.get("name")]
            facts.append(
                f"{_plural(summary.get('drug_count', len(drugs)), 'drug')} with a known mechanism "
                f"({summary.get('approved_count', 0)} approved)"
                + (f", e.g. {', '.join(approved[:3])}" if approved else "")
                + " (ChEMBL)"
            )
            # Selective (single protein) vs via family/complex: key for best-in-class
            facts.append(
                f"{_plural(len(single), 'drug')} acting on the protein itself, "
                f"{_plural(len(drugs) - len(single), 'drug')} only via a family or complex"
            )
            modalities = summary.get("modality_counts") or {}
            if modalities:
                facts.append("Drug modalities: " + ", ".join(f"{m} ({n})" for m, n in modalities.items()))
        elif chembl.get("found") is False:
            facts.append("Protein not found in ChEMBL")
        else:
            facts.append("No drug with a known mechanism (ChEMBL)")

        # Skipped when the protein is absent from ChEMBL: "not found" already says it
        if chembl.get("found") and chembl.get("activity_count") is not None:
            facts.append(
                f"{_fmt(chembl.get('activity_count'))} quantitative activities, "
                f"{_fmt(potent)} with pChEMBL >= 7 (ChEMBL)"
            )

    # --- Facts from Open Targets --------------------------------------------
    ot_buckets: dict[str, list[str]] = {}
    ot_phase = None
    if ot_ok:
        sources.append("Open Targets")
        ot_buckets = open_targets.get("tractability") or {}
        for modality, buckets in ot_buckets.items():
            facts.append(f"{modality}: {', '.join(buckets)} (Open Targets)")
            for b in buckets:
                if b in OT_CLINICAL_BUCKETS:
                    ot_phase = max(ot_phase or 0.0, OT_CLINICAL_BUCKETS[b])

    # --- Tiers ----------------------------------------------------------------
    # Open Targets clinical buckets back up ChEMBL when ChEMBL is unavailable
    single_phase = best_single_phase if chembl_ok else ot_phase
    any_phase = best_any_phase if chembl_ok else ot_phase

    if single_phase == 4.0:
        tier, explanation = "approved_drug", "Approved drug acting on the target."
    elif single_phase is not None and single_phase >= 0.5:
        tier = "clinical_candidate"
        explanation = f"Clinical-stage drug acting on the target (max phase {single_phase:g})."
    elif potent is not None and potent >= cfg["potent_activities_min"]:
        tier = "potent_chemistry"
        explanation = "Potent chemical matter, no selective clinical drug yet."
        if any_phase is not None and any_phase >= 0.5:
            explanation += " Clinical drugs exist only via a protein family or complex."
    elif any_phase is not None and any_phase >= 0.5:
        tier = "non_selective_clinical"
        explanation = (
            f"Clinical drug only via a protein family or complex (max phase {any_phase:g}): "
            "modulation proven, but not selectively."
        )
    elif (potent or 0) > 0 or (chembl_ok and (chembl.get("activity_count") or 0) > 0):
        tier, explanation = "some_chemistry", "Some quantitative chemistry, limited potency or volume."
    elif ot_buckets:
        tier, explanation = "predicted_only", "Druggability predicted only (structure, family or localisation)."
    else:
        tier, explanation = "nothing", "No chemical matter and no predicted druggability found."

    score = tiers[tier]
    # With one source down the score can only be too low, never too high:
    # say so, unless it is already the maximum
    if not (chembl_ok and ot_ok) and score < max(tiers.values()):
        missing = "ChEMBL" if not chembl_ok else "Open Targets"
        explanation += f" ({missing} unavailable: score may be underestimated.)"

    gaps = []
    # A gap only if the sources answered: absence of data is not absence of evidence
    if chembl_ok and score <= tiers["predicted_only"]:
        gaps.append(EvidenceGap(
            dimension="tractability",
            description="No potent chemical matter or drug identified",
            severity="medium",
            why_it_matters=(
                "Without validated chemical starting points, a programme needs "
                "hit finding before any pharmacological validation of the hypothesis."
            ),
        ))

    return EvidenceScore(
        dimension="tractability",
        score=score,
        label=label_for(score),
        explanation=explanation,
        facts=facts,
        sources=sources,
    ), gaps
