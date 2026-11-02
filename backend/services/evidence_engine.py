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

from datetime import date

from backend.schemas.evidence import EvidenceGap, EvidenceScore, LiteratureTrend

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
    "biological_knowledge": {
        # This axis measures how well the biology is characterised. It is NOT
        # "higher is better": a high score also means a crowded field. The
        # literature trend (Emerging / Growing / Stable / Declining) gives the
        # other half of the picture.
        # Weighted mean of sub-scores (weights sum to 1); a sub-score whose
        # source failed is left out and the weights are renormalised.
        "weights": {"literature": 0.5, "pathways": 0.2, "interactions": 0.15, "function": 0.15},
        # PubMed all-time count -> literature sub-score
        "literature_tiers": [(5000, 1.0), (1000, 0.75), (200, 0.5), (20, 0.25), (1, 0.1)],
        "pathway_tiers": [(5, 1.0), (1, 0.5)],
        # STRING partners above this combined score count as high confidence
        "partner_min_score": 0.9,
        "partner_tiers": [(5, 1.0), (1, 0.5)],
        # below this score the biology is considered poorly characterised (gap)
        "gap_below": 0.25,
    },
    "literature_trend": {
        "window_years": 5,  # compare the last 5 complete years with the 5 before
        "growing_ratio": 1.5,  # recent / previous >= 1.5 -> Growing
        "declining_ratio": 0.8,  # recent / previous < 0.8 -> Declining
        "emerging_max_previous": 50,  # growing from almost nothing -> Emerging
    },
    "animal_models": {
        "tiers": {
            "relevant_strong": 1.0,  # mouse phenotype mapped to the disease, strong (OT animal_model)
            "relevant_some": 0.7,  # mouse phenotype mapped to the disease, weaker
            "well_characterised": 0.6,  # many mouse phenotypes, none mapped to the disease
            "some_phenotypes": 0.4,
            "nothing": 0.0,
        },
        "relevant_strong_min": 0.5,  # OT animal_model score for "relevant_strong"
        "well_characterised_min": 20,  # distinct MP terms
    },
    "disease_expression": {
        # Open Targets rna_expression (Expression Atlas: disease vs normal) -> score
        "tiers": [(0.5, 1.0), (0.2, 0.6), (0.0001, 0.3)],
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


def _tiered(value: float | int | None, tiers: list[tuple[float, float]]) -> float:
    """First (threshold, score) whose threshold is reached; 0.0 if none."""
    if value is None:
        return 0.0
    for threshold, score in tiers:
        if value >= threshold:
            return score
    return 0.0


# ---------------------------------------------------------------------------
# Literature trend (shown next to the knowledge axis, not a score)
# ---------------------------------------------------------------------------
def literature_trend(pubmed: dict | None, europe_pmc: dict | None) -> LiteratureTrend:
    """Publication volume (PubMed) and direction (Europe PMC timeline).

    Compares the last `window_years` COMPLETE years with the window before.
    The current year is excluded: it is still being indexed and would make
    every target look like it is declining.
    """
    cfg = CONFIG["literature_trend"]
    trend = LiteratureTrend(
        total_count=(pubmed or {}).get("total_count"),
        recent_count=(pubmed or {}).get("recent_count"),
    )
    timeline = (europe_pmc or {}).get("timeline")
    if not timeline:
        return trend
    trend.timeline = timeline

    last = date.today().year - 1  # last complete year
    w = cfg["window_years"]
    recent_years = [str(y) for y in range(last - w + 1, last + 1)]
    previous_years = [str(y) for y in range(last - 2 * w + 1, last - w + 1)]
    values = [timeline.get(y) for y in recent_years + previous_years]
    if any(v is None for v in values):
        return trend  # a failed year in the windows: no trend rather than a wrong one

    recent = sum(timeline[y] for y in recent_years)
    previous = sum(timeline[y] for y in previous_years)

    if recent == 0 and previous == 0:
        trend.trend = "Unknown"
    elif previous == 0 or (
        recent / previous >= cfg["growing_ratio"] and previous <= cfg["emerging_max_previous"]
    ):
        trend.trend = "Emerging"
    elif recent / previous >= cfg["growing_ratio"]:
        trend.trend = "Growing"
    elif recent / previous < cfg["declining_ratio"]:
        trend.trend = "Declining"
    else:
        trend.trend = "Stable"
    return trend


# ---------------------------------------------------------------------------
# Dimension: biological knowledge
# ---------------------------------------------------------------------------
def score_biological_knowledge(
    pubmed: dict | None,
    reactome: dict | None,
    string: dict | None,
    uniprot: dict | None,
) -> tuple[EvidenceScore, list[EvidenceGap]]:
    """How well is the target's biology characterised?

    Unlike the other axes, this one is built as a weighted mean of four
    sub-scores, because "knowledge" is breadth across independent sources
    rather than a single ladder:
      - literature volume (PubMed, all time)
      - curated pathways (Reactome)
      - high-confidence interaction partners (STRING)
      - curated function text (UniProt)
    A sub-score whose source failed is excluded (weights renormalised).
    """
    cfg = CONFIG["biological_knowledge"]
    facts: list[str] = []
    sources: list[str] = []
    parts: dict[str, float] = {}

    total = (pubmed or {}).get("total_count")
    if total is not None:
        sources.append("PubMed")
        parts["literature"] = _tiered(total, cfg["literature_tiers"])
        recent = pubmed.get("recent_count")
        facts.append(
            f"{_fmt(total)} publications, {_fmt(recent)} in {pubmed.get('recent_window')} (PubMed)"
        )

    pathways = (reactome or {}).get("pathways")
    if pathways is not None:
        sources.append("Reactome")
        parts["pathways"] = _tiered(len(pathways), cfg["pathway_tiers"])
        names = [p["name"] for p in pathways[:3]]
        facts.append(
            f"{_plural(len(pathways), 'Reactome pathway')}"
            + (f", e.g. {', '.join(names)}" if names else "")
        )

    interactions = (string or {}).get("interactions")
    if interactions is not None:
        sources.append("STRING")
        strong = [i for i in interactions if (i.get("score") or 0) >= cfg["partner_min_score"]]
        parts["interactions"] = _tiered(len(strong), cfg["partner_tiers"])
        facts.append(
            f"{_plural(len(strong), 'high-confidence partner')} (STRING >= "
            f"{cfg['partner_min_score']})"
            + (f": {', '.join(i['partner'] for i in strong[:5])}" if strong else "")
        )

    # uniprot.py returns {} on failure: an empty dict means "unknown"
    if uniprot:
        sources.append("UniProt")
        has_function = bool(uniprot.get("function"))
        parts["function"] = 1.0 if has_function else 0.0
        facts.append("Curated function in UniProt" if has_function else "No curated function in UniProt")

    if not parts:
        return EvidenceScore(dimension="biological_knowledge", score=None, label="Unavailable",
                             explanation="No literature or pathway source could be reached."), []

    weight_sum = sum(cfg["weights"][k] for k in parts)
    score = round(sum(cfg["weights"][k] * v for k, v in parts.items()) / weight_sum, 2)

    if score >= 0.8:
        explanation = "Well-characterised biology (also a crowded field)."
    elif score >= 0.5:
        explanation = "Reasonably characterised biology."
    else:
        explanation = "Poorly characterised biology: higher novelty, higher uncertainty."
    if len(parts) < len(cfg["weights"]):
        missing = sorted(set(cfg["weights"]) - set(parts))
        explanation += f" (Missing: {', '.join(missing)}.)"

    gaps = []
    if score < cfg["gap_below"] and "literature" in parts:
        gaps.append(EvidenceGap(
            dimension="biological_knowledge",
            description="Target biology poorly characterised",
            severity="low",
            why_it_matters=(
                "Little is known about function, pathways or partners: the "
                "mechanism linking the target to disease will need to be built "
                "experimentally (higher risk, but also less competition)."
            ),
        ))

    return EvidenceScore(dimension="biological_knowledge", score=score, label=label_for(score),
                         explanation=explanation, facts=facts, sources=sources), gaps


# ---------------------------------------------------------------------------
# Dimension: animal models
# ---------------------------------------------------------------------------
def score_animal_models(
    mgi: dict | None, open_targets: dict | None
) -> tuple[EvidenceScore, list[EvidenceGap]]:
    """What does genetic perturbation of the mouse ortholog show?

    Sources:
      - Open Targets animal_model datatype: mouse phenotypes mapped to the disease
        (on the indication if chosen, else across all diseases)
      - MGI: all phenotypes of the mouse ortholog

    Lethality is NOT scored here: it is a safety signal (see safety section).
    Caveat carried in the facts: MGI pools every allele (knockouts,
    conditional, activating knock-ins such as Kras G12D).
    """
    cfg = CONFIG["animal_models"]
    tiers = cfg["tiers"]
    facts: list[str] = []
    sources: list[str] = []
    mgi_ok = _is_available(mgi)
    ot_ok = _is_available(open_targets)

    if not mgi_ok and not ot_ok:
        return EvidenceScore(dimension="animal_models", score=None, label="Unavailable",
                             explanation="MGI and Open Targets could not be reached."), []

    # --- Open Targets: phenotypes relevant to the disease -----------------------
    relevant, holder = 0.0, None
    indication = (open_targets or {}).get("indication") if ot_ok else None
    if ot_ok:
        sources.append("Open Targets")
        rows = (_indication_associations(open_targets) if indication
                else open_targets.get("top_associations"))
        relevant, holder = _best_datatype(rows, ["animal_model"])
        if holder:
            facts.append(f"Mouse model evidence for {holder['disease_name']} ({relevant:.2f}) (Open Targets)")
        elif indication:
            facts.append(f"No mouse model evidence for {indication['disease_name']} (Open Targets)")

    # --- MGI: breadth of mouse phenotypes ----------------------------------------
    count = None
    if mgi_ok:
        sources.append("MGI")
        mouse = mgi.get("mouse_gene") or {}
        if mgi.get("found") is False:
            facts.append("No mouse ortholog (MGI)")
        else:
            count = mgi.get("phenotype_count")
            top = [p["term"] for p in (mgi.get("top_phenotypes") or [])[:3]]
            facts.append(
                f"{_plural(count or 0, 'phenotype')} for {mouse.get('symbol')} (MGI)"
                + (f", e.g. {', '.join(top)}" if top else "")
            )
            facts.append("MGI phenotypes pool all alleles (knockouts, conditional, activating knock-ins)")

    # --- Score ---------------------------------------------------------------
    if relevant >= cfg["relevant_strong_min"]:
        tier, explanation = "relevant_strong", "Mouse models reproduce disease-relevant phenotypes."
    elif relevant > 0:
        tier, explanation = "relevant_some", "Some disease-relevant mouse model evidence."
    elif (count or 0) >= cfg["well_characterised_min"]:
        tier, explanation = "well_characterised", "Well-characterised mouse genetics, not mapped to the disease."
    elif (count or 0) > 0:
        tier, explanation = "some_phenotypes", "Some mouse phenotypes, not mapped to the disease."
    else:
        tier, explanation = "nothing", "No characterised mouse model."
    score = tiers[tier]

    gaps = []
    if mgi_ok and score == 0.0:
        gaps.append(EvidenceGap(
            dimension="animal_models",
            description="No characterised mouse model",
            severity="medium",
            why_it_matters=(
                "Without an in vivo genetic model, both the efficacy hypothesis and "
                "the consequences of losing the target remain untested in a mammal."
            ),
        ))

    return EvidenceScore(dimension="animal_models", score=score, label=label_for(score),
                         explanation=explanation, facts=facts, sources=sources), gaps


# ---------------------------------------------------------------------------
# Dimension: disease expression
# ---------------------------------------------------------------------------
def score_disease_expression(
    open_targets: dict | None, hpa: dict | None
) -> tuple[EvidenceScore, list[EvidenceGap]]:
    """Is the target expressed / deregulated in the chosen indication?

    Sources:
      - Open Targets rna_expression: differential expression, disease vs normal
        (Expression Atlas). Drives the score.
      - HPA cancer expression (TCGA): context only. HPA uses the median per
        cancer type, so it misses biomarker-defined subgroups (e.g. HER2+).

    Needs an indication: without one there is no disease to compare against,
    so the axis is reported as not assessed (score None), which is not a gap.
    """
    cfg = CONFIG["disease_expression"]
    facts: list[str] = []
    sources: list[str] = []
    ot_ok = _is_available(open_targets)
    indication = (open_targets or {}).get("indication") if ot_ok else None

    # HPA cancer context: only relevant without an indication or for an
    # oncology one (showing tumour expression for arthritis would mislead)
    areas = (indication or {}).get("therapeutic_areas") or []
    oncology = not indication or any("cancer" in a.lower() for a in areas)
    cancer = (hpa or {}).get("cancer_expression") or {}
    if oncology and cancer.get("specificity"):
        sources.append("HPA")
        line = f"Across cancers: {cancer['specificity']}, {cancer.get('distribution')} (HPA, TCGA medians)"
        if cancer.get("specific_expression"):
            enriched = ", ".join(cancer["specific_expression"])
            line += f"; enriched in {enriched}"
        facts.append(line)

    if not ot_ok:
        return EvidenceScore(dimension="disease_expression", score=None, label="Unavailable",
                             explanation="Open Targets could not be reached.",
                             facts=facts, sources=sources), []
    if not indication:
        return EvidenceScore(dimension="disease_expression", score=None, label="Unavailable",
                             explanation="Choose an indication to assess expression in disease.",
                             facts=facts, sources=sources), []

    sources.insert(0, "Open Targets")
    best, holder = _best_datatype(_indication_associations(open_targets), ["rna_expression"])
    if holder:
        facts.insert(0, f"Differential expression in {holder['disease_name']} ({best:.2f}) "
                        "(Open Targets / Expression Atlas)")
        score = _tiered(best, cfg["tiers"])
        explanation = "Target deregulated in the indication (disease vs normal)."
    else:
        facts.insert(0, f"No differential expression data for {indication['disease_name']} (Open Targets)")
        score = 0.0
        explanation = ("No differential expression evidence in the indication. "
                       "Such data are sparse outside oncology: absence is weak evidence.")

    gaps = []
    if score == 0.0:
        gaps.append(EvidenceGap(
            dimension="disease_expression",
            description=f"No evidence of target deregulation in {indication['disease_name']}",
            severity="low",
            why_it_matters=(
                "Expression in the diseased tissue supports biological relevance and, "
                "for antibodies or ADCs, is a prerequisite. Here it should be checked "
                "experimentally (patient samples, IHC)."
            ),
        ))

    return EvidenceScore(dimension="disease_expression", score=score, label=label_for(score),
                         explanation=explanation, facts=facts, sources=sources), gaps
