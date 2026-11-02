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
    "human_genetics": {
        # Open Targets datatypes counted as genetic evidence. somatic_mutation
        # is included: in oncology, recurrent somatic drivers ARE the genetics.
        "datatypes": ["genetic_association", "somatic_mutation", "genetic_literature"],
        # best genetic datatype score -> axis score (first threshold reached)
        "tiers": [(0.8, 1.0), (0.5, 0.7), (0.2, 0.4), (0.0001, 0.2)],
        # genetic evidence exists, but only for diseases other than the indication
        "other_diseases_only": 0.2,
        # fallback when Open Targets is down: ClinVar pathogenic variants
        "clinvar_only": 0.4,
    },
    "clinical_precedence": {
        # Open Targets "clinical" datatype on the indication -> late stage
        "indication_late_stage_min": 0.8,
        "tiers": {
            "approved_in_indication": 1.0,  # approved drug on the target + strong clinical score
            "clinical_in_indication": 0.7,  # clinical evidence in the indication
            "approved_elsewhere": 0.6,  # approved / clinical on the target, other diseases
            "clinical_elsewhere": 0.5,
            "non_selective_only": 0.3,  # only via a family / complex
            "trials_mention_only": 0.1,  # ClinicalTrials mentions, no drug on the target
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


# ---------------------------------------------------------------------------
# Helpers on Open Targets associations
# ---------------------------------------------------------------------------
def _best_datatype(associations: list[dict], datatypes: list[str]) -> tuple[float, dict | None]:
    """Highest score among `datatypes` over a list of associations.

    Returns (score, the association that holds it).
    """
    best, holder = 0.0, None
    for assoc in associations or []:
        for dt in datatypes:
            value = (assoc.get("datatype_scores") or {}).get(dt, 0.0)
            if value > best:
                best, holder = value, assoc
    return best, holder


def _indication_associations(open_targets: dict) -> list[dict]:
    """Associations to score against when an indication is chosen.

    The indication itself (direct row) plus its best sub-diseases: for a broad
    indication ("cancer"), the evidence lives in the sub-diseases.
    """
    rows = list(open_targets.get("focus_associations") or [])
    ind = open_targets.get("indication") or {}
    if ind.get("direct_datatype_scores"):
        rows.append({
            "disease_name": ind.get("disease_name"),
            "datatype_scores": ind["direct_datatype_scores"],
        })
    return rows


# ---------------------------------------------------------------------------
# Dimension: human genetics
# ---------------------------------------------------------------------------
def score_human_genetics(
    open_targets: dict | None, clinvar: dict | None, uniprot: dict | None
) -> tuple[EvidenceScore, list[EvidenceGap]]:
    """Is the target-disease link supported by human genetics?

    Human genetic support is the strongest predictor of clinical success
    among target-level evidence. With an indication, only genetics on that
    indication (and its sub-diseases) scores high; genetics elsewhere is
    reported but scores low.

    Sources:
      - Open Targets: genetic_association, somatic_mutation, genetic_literature
      - ClinVar: germline pathogenic variants (fact; fallback if OT is down)
      - UniProt: curated disease involvement (fact)
    """
    cfg = CONFIG["human_genetics"]
    facts: list[str] = []
    sources: list[str] = []
    ot_ok = _is_available(open_targets)
    clinvar_ok = clinvar is not None and clinvar.get("total_variants") is not None

    if not ot_ok and not clinvar_ok:
        return EvidenceScore(dimension="human_genetics", score=None, label="Unavailable",
                             explanation="Open Targets and ClinVar could not be reached."), []

    indication = (open_targets or {}).get("indication") if ot_ok else None

    # --- Open Targets ---------------------------------------------------------
    best_focus, focus_holder = 0.0, None
    best_any, any_holder = 0.0, None
    if ot_ok:
        sources.append("Open Targets")
        best_any, any_holder = _best_datatype(open_targets.get("top_associations"), cfg["datatypes"])
        if any_holder:
            facts.append(
                f"Strongest genetic evidence overall: {any_holder['disease_name']} "
                f"({best_any:.2f}) (Open Targets)"
            )
        if indication:
            best_focus, focus_holder = _best_datatype(
                _indication_associations(open_targets), cfg["datatypes"]
            )
            if focus_holder:
                facts.append(
                    f"Genetic evidence in {indication['disease_name']}: "
                    f"{focus_holder['disease_name']} ({best_focus:.2f}) (Open Targets)"
                )
            else:
                facts.append(f"No genetic evidence in {indication['disease_name']} (Open Targets)")

    # --- ClinVar / UniProt (facts) -------------------------------------------
    pathogenic = None
    if clinvar_ok:
        sources.append("ClinVar")
        counts = clinvar.get("classification_counts") or {}
        pathogenic = (counts.get("pathogenic") or 0) + (counts.get("likely_pathogenic") or 0)
        facts.append(f"{_fmt(pathogenic)} germline (likely) pathogenic variants (ClinVar)")
        conditions = [c["name"] for c in (clinvar.get("top_conditions") or [])[:3]]
        if conditions:
            facts.append("Main ClinVar conditions: " + ", ".join(conditions))
    if uniprot and uniprot.get("disease_associations"):
        sources.append("UniProt")
        names = [d.get("name") for d in uniprot["disease_associations"][:3] if d.get("name")]
        facts.append("UniProt disease involvement: " + ", ".join(names))

    # --- Score ---------------------------------------------------------------
    def tiered(value: float) -> float:
        for threshold, score in cfg["tiers"]:
            if value >= threshold:
                return score
        return 0.0

    if ot_ok and indication:
        if best_focus > 0:
            score = tiered(best_focus)
            explanation = f"Human genetic evidence in the indication (best {best_focus:.2f})."
        elif best_any > 0:
            score = cfg["other_diseases_only"]
            explanation = "Human genetic evidence exists, but not for the chosen indication."
        else:
            score, explanation = 0.0, "No human genetic evidence found."
    elif ot_ok:
        score = tiered(best_any)
        explanation = (
            f"Human genetic evidence (best {best_any:.2f}, no indication chosen)."
            if best_any > 0 else "No human genetic evidence found."
        )
    else:
        # Open Targets down: ClinVar alone can show genetic involvement, not more
        score = cfg["clinvar_only"] if (pathogenic or 0) > 0 else 0.0
        explanation = "Based on ClinVar only (Open Targets unavailable): score may be underestimated."

    gaps = []
    if ot_ok and score <= cfg["other_diseases_only"]:
        where = f" for {indication['disease_name']}" if indication else ""
        gaps.append(EvidenceGap(
            dimension="human_genetics",
            description=f"No human genetic evidence linking the target to disease{where}",
            severity="high",
            why_it_matters=(
                "Lack of efficacy is the main cause of phase 2 failure, and targets "
                "with human genetic support are markedly more likely to succeed. "
                "Without it, the causal role of the target remains a hypothesis."
            ),
        ))

    return EvidenceScore(dimension="human_genetics", score=score, label=label_for(score),
                         explanation=explanation, facts=facts, sources=sources), gaps


# ---------------------------------------------------------------------------
# Dimension: clinical precedence
# ---------------------------------------------------------------------------
def score_clinical_precedence(
    chembl: dict | None, open_targets: dict | None, trials: dict | None
) -> tuple[EvidenceScore, list[EvidenceGap]]:
    """Has the target already been tested in the clinic, and where?

    Sources:
      - ChEMBL: drugs on the target and their max phase (selective or not)
      - Open Targets: "clinical" datatype on the indication
      - ClinicalTrials.gov: trials mentioning the gene (fact only: a gene
        mentioned in a trial is often a biomarker, not a drug target)

    `trials` is {"total": int | None}. ClinicalTrials currently returns []
    both on failure and on no result, so it never creates a gap on its own.
    """
    cfg = CONFIG["clinical_precedence"]
    tiers = cfg["tiers"]
    facts: list[str] = []
    sources: list[str] = []
    chembl_ok = _is_available(chembl)
    ot_ok = _is_available(open_targets)

    if not chembl_ok and not ot_ok:
        return EvidenceScore(dimension="clinical_precedence", score=None, label="Unavailable",
                             explanation="ChEMBL and Open Targets could not be reached."), []

    # --- ChEMBL: phases, selective vs not --------------------------------------
    single_phase = any_phase = None
    if chembl_ok:
        sources.append("ChEMBL")
        drugs = chembl.get("drugs") or []
        single = [d for d in drugs if d.get("via_target_type") == "SINGLE PROTEIN"]
        single_phase = max((d["max_phase"] for d in single if d.get("max_phase")), default=None)
        any_phase = max((d["max_phase"] for d in drugs if d.get("max_phase")), default=None)
        if single_phase:
            facts.append(f"Most advanced drug on the protein itself: phase {single_phase:g} (ChEMBL)")
        elif any_phase:
            facts.append(f"Clinical drugs only via a family or complex: phase {any_phase:g} (ChEMBL)")
        else:
            facts.append("No clinical-stage drug on the target (ChEMBL)")

    # --- Open Targets: clinical evidence in the indication ---------------------
    indication = (open_targets or {}).get("indication") if ot_ok else None
    ind_clinical = 0.0
    if ot_ok:
        sources.append("Open Targets")
        if indication:
            ind_clinical, holder = _best_datatype(_indication_associations(open_targets), ["clinical"])
            facts.append(
                f"Clinical evidence in {indication['disease_name']}: "
                + (f"{holder['disease_name']} ({ind_clinical:.2f})" if holder else "none")
                + " (Open Targets)"
            )

    # --- ClinicalTrials (fact only) ----------------------------------------------
    total_trials = (trials or {}).get("total")
    if total_trials:
        sources.append("ClinicalTrials.gov")
        facts.append(f"{_fmt(total_trials)} trials mention the gene (ClinicalTrials.gov; "
                     "includes biomarker studies)")

    # --- Score ---------------------------------------------------------------
    clinical_on_target = single_phase is not None and single_phase >= 0.5
    if indication and ind_clinical >= cfg["indication_late_stage_min"] and single_phase == 4.0:
        tier, explanation = "approved_in_indication", "Approved drug on the target, late-stage evidence in the indication."
    elif indication and ind_clinical > 0:
        tier, explanation = "clinical_in_indication", "Clinical evidence for the target in the indication."
    elif single_phase == 4.0:
        tier = "approved_elsewhere"
        explanation = ("Approved drug on the target, in other indications (expansion / repurposing)."
                       if indication else "Approved drug on the target.")
    elif clinical_on_target:
        tier = "clinical_elsewhere"
        explanation = ("Clinical-stage drug on the target, in other indications."
                       if indication else f"Clinical-stage drug on the target (phase {single_phase:g}).")
    elif any_phase is not None and any_phase >= 0.5:
        tier, explanation = "non_selective_only", "Clinical drugs only via a protein family or complex."
    elif total_trials:
        tier, explanation = "trials_mention_only", "Gene mentioned in clinical trials, no drug acting on it."
    else:
        tier, explanation = "nothing", "No clinical precedence: novel target."

    # Without an indication, "elsewhere" tiers describe the whole target
    if not indication and tier == "approved_elsewhere":
        score = tiers["approved_in_indication"]
    elif not indication and tier == "clinical_elsewhere":
        score = tiers["clinical_in_indication"]
    else:
        score = tiers[tier]

    gaps = []
    if chembl_ok and score == 0.0:
        gaps.append(EvidenceGap(
            dimension="clinical_precedence",
            description="No clinical precedence for this target",
            severity="low",
            why_it_matters=(
                "Novel targets carry more translational risk (no human proof of "
                "pharmacology or tolerability), but also first-in-class potential."
            ),
        ))

    return EvidenceScore(dimension="clinical_precedence", score=score, label=label_for(score),
                         explanation=explanation, facts=facts, sources=sources), gaps
