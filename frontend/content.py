# frontend/content.py
"""Static texts of the interface: what each dimension measures, how to read
its score, and the "what this tab answers" introductions.

Scoring ladders are built from the engine CONFIG (GET /api/engine/config),
never hard-coded: when a threshold changes in evidence_engine.py, the
interface explains the new rule automatically.
"""

DIMENSION_TITLES = {
    "human_genetics": "Human genetics",
    "clinical_precedence": "Clinical precedence",
    "tractability": "Tractability",
    "biological_knowledge": "Biological knowledge",
    "animal_models": "Animal models",
    "disease_expression": "Disease expression",
}

RADAR_LABELS = {
    "human_genetics": "Genetics",
    "clinical_precedence": "Clinical",
    "tractability": "Tractability",
    "biological_knowledge": "Biology",
    "animal_models": "Animal",
    "disease_expression": "Expression",
}

WHAT_WE_LOOK_AT = {
    "human_genetics": "Is the target-disease link supported by human genetics: somatic "
    "mutations in tumours, germline variants, association studies.",
    "clinical_precedence": "Has the target already been drugged in humans, and in this indication.",
    "tractability": "Can the protein itself be modulated: drugs and potent chemistry, by "
    "modality and by mechanism.",
    "biological_knowledge": "How well the target is understood: publication volume and "
    "trend, pathways, interaction partners, curated function.",
    "animal_models": "What genetic perturbation does in the mouse: knockout phenotypes, and "
    "mouse models of the disease.",
    "disease_expression": "Is the target present and deregulated where we want to act: "
    "diseased versus normal tissue.",
}

TAB_INTROS = {
    "clinical": "Who is developing drugs on this target, how far they went, and who "
    "stopped. An approved drug removes the chemistry risk but also the first-in-class "
    "opportunity; stopped programs tell why others failed.",
    "biology": "How well the target is understood. A well-described target brings fewer "
    "biological surprises, but also a crowded field; an emerging one is less de-risked "
    "and more open.",
    "safety": "The evidence that anticipates on-target toxicity: how tolerant humans are "
    "to losing one copy of the gene, what knocking it out does in the mouse, where the "
    "protein is expressed in healthy tissues, and which adverse events are documented.",
    "toolbox": "What a team needs to start experiments: 3D structures, chemical probes "
    "with their controls, potent compounds, validated antibodies, and where to source "
    "reagents.",
    "sources": "Where every number comes from. A source marked unavailable did not answer: "
    "its dimensions may be underestimated, and nothing it would have said is counted as "
    "an evidence gap.",
}


def is_oncology(therapeutic_areas: list[str] | None) -> bool:
    """True when Open Targets files the indication under cancer."""
    return any("cancer" in a.lower() or "neoplasm" in a.lower() for a in therapeutic_areas or [])


def genetics_intro(indication_name: str | None, therapeutic_areas: list[str] | None) -> str:
    """Introduction of the Genetics tab, adapted to the kind of indication:
    somatic mutations are the key genetic evidence in cancer only."""
    lead = "Human genetic evidence is the strongest predictor of clinical success for a target. "
    if not indication_name:
        return lead + (
            "Without an indication, this tab shows the diseases with the strongest evidence and its "
            "kind: somatic mutations (cancers), germline variants and association studies "
            "(inherited diseases and common traits), and literature co-mentions, which show interest "
            "but not causality."
        )
    if is_oncology(therapeutic_areas):
        return lead + (
            f"In {indication_name}, a cancer, the key evidence is somatic: mutations acquired by the "
            "tumour (Figure 2). Germline variants and association studies come next; literature "
            "co-mentions show interest but not causality."
        )
    return lead + (
        f"In {indication_name}, the relevant genetics is germline: rare variants causing the disease "
        "(ClinVar, Figure 3) and common variants from association studies (GWAS). Somatic mutations "
        "in tumours (Figure 2) do not apply to this indication and are shown for context only."
    )


OT_SCORE_HELP = (
    "Open Targets aggregates every piece of evidence of one type into a score from 0 to 1. "
    "It grows with the number and the strength of independent studies. It is not a "
    "probability, and not a measure of effect size or of how many patients are affected: "
    "a gene can score high because it was studied a lot. Use it to compare rows."
)
# TargetScope's own reading guide (Open Targets publishes no official thresholds)
OT_SCORE_RULE = [
    ("≥ 0.7", "solid, replicated evidence"),
    ("0.4 to 0.7", "real but limited"),
    ("< 0.4", "weak or single study"),
]

INTRO = (
    "<b>What it does</b> · TargetScope gathers the public evidence on a drug target in one "
    "report: human genetics, drugs and clinical trials, competition, biology, expression, "
    "mouse models, safety and research tools, from about fifteen sources (Open Targets, "
    "ChEMBL, UniProt, ClinVar, Human Protein Atlas, ClinicalTrials.gov…). "
    "<b>How to use it</b> · type a gene, choose an indication if you have one in mind, then "
    "<b>Assess</b>. Each of the six dimensions gets a score with its rule and its sources, "
    "never a single global score: the decision stays with the scientist."
)
CREDIT = (
    "Designed by Adrien Cosson · code written with Claude (Anthropic) under his review · "
    '<a href="https://github.com/Adrien-COSSON" target="_blank">GitHub</a>'
)

DISCLAIMER = (
    "Research use only, not for clinical decisions. Data are provided as is by the "
    "original sources; TargetScope adds explicit, documented rules on top of them."
)


def _pct(x: float) -> str:
    return f"{x * 100:g}%"


def score_ladder(dimension: str, config: dict) -> list[tuple[str, str]]:
    """[(score, condition), ...] from the engine CONFIG, strongest first."""
    c = config.get(dimension, {})
    if dimension == "human_genetics":
        freq = c.get("mutation_frequency_tiers", [])
        tiers = c.get("tiers", [])
        rows = [
            (f"{s:g}", f"mutated in ≥ {_pct(t)} of tumours of the indication") for t, s in freq[:2]
        ]
        rows += [
            (f"{s:g}", f"Open Targets genetic score ≥ {t:g} in the indication")
            for t, s in tiers[:2]
        ]
        rows.append(
            (
                f"{c.get('other_diseases_only', 0.2):g}",
                "genetic evidence only in other diseases (gap: high)",
            )
        )
        return sorted(rows, key=lambda r: -float(r[0]))
    if dimension == "tractability":
        t = c.get("tiers", {})
        return [
            (f"{t.get('approved_drug', 1):g}", "approved drug acting on the protein itself"),
            (
                f"{t.get('clinical_candidate', 0.8):g}",
                "clinical candidate acting on the protein itself",
            ),
            (
                f"{t.get('potent_chemistry', 0.6):g}",
                f"≥ {c.get('potent_activities_min', 100)} potent activities (≤ 100 nM)",
            ),
            (
                f"{t.get('non_selective_clinical', 0.5):g}",
                "drug acting only via a protein family or complex",
            ),
            (f"{t.get('some_chemistry', 0.4):g}", "some measured chemistry"),
            (
                f"{t.get('predicted_only', 0.2):g}",
                "predicted only: pocket or druggable family (gap)",
            ),
        ]
    if dimension == "clinical_precedence":
        t = c.get("tiers", {})
        return [
            (f"{t.get('approved_in_indication', 1):g}", "approved drug in the indication"),
            (f"{t.get('clinical_in_indication', 0.7):g}", "clinical evidence in the indication"),
            (f"{t.get('approved_elsewhere', 0.6):g}", "approved for other diseases (repurposing)"),
            (f"{t.get('clinical_elsewhere', 0.5):g}", "clinical stage, other diseases"),
            (f"{t.get('non_selective_only', 0.3):g}", "only drugs acting via a family or complex"),
            (
                f"{t.get('trials_mention_only', 0.1):g}",
                "mentioned in trials, no drug on the target",
            ),
        ]
    if dimension == "animal_models":
        t = c.get("tiers", {})
        return [
            (f"{t.get('disease_model', 1):g}", "a mouse model of the indication exists"),
            (f"{t.get('relevant_some', 0.7):g}", "mouse phenotypes mapped to the disease"),
            (
                f"{t.get('well_characterised', 0.6):g}",
                f"≥ {c.get('well_characterised_min', 20)} phenotypes, none mapped to the disease",
            ),
            (f"{t.get('some_phenotypes', 0.4):g}", "a few phenotypes"),
        ]
    if dimension == "biological_knowledge":
        lit = c.get("literature_tiers", [])
        w = c.get("weights", {})
        rows = [(f"{s:g}", f"literature sub-score: ≥ {n:,} publications") for n, s in lit[:3]]
        rows.append(
            (
                "—",
                "weighted with pathways ({:.0%}), partners ({:.0%}), function ({:.0%})".format(
                    w.get("pathways", 0), w.get("interactions", 0), w.get("function", 0)
                ),
            )
        )
        return rows
    if dimension == "disease_expression":
        return [
            (f"{s:g}", f"disease vs normal expression score ≥ {t:g}") for t, s in c.get("tiers", [])
        ] + [("n/s", "no disease-vs-normal data: not scored")]
    return []


READING_SUMMARY = {
    "human_genetics": "Highest when a large share of patients carry the alteration, or "
    "germline evidence is strong.",
    "clinical_precedence": "Highest with an approved drug in this indication; approval "
    "elsewhere means a repurposing case.",
    "tractability": "Highest with a selective approved drug. Drugs hitting only a family "
    "count less: the best-in-class room is in selectivity.",
    "biological_knowledge": "High = well understood but crowded; low = novel and less "
    "de-risked. Not 'higher is better'.",
    "animal_models": "Highest when a mouse model of the indication exists, not just phenotypes.",
    "disease_expression": "Not scored when no disease-versus-normal data exist: unknown, "
    "not absent. Reliable mostly in oncology.",
}
