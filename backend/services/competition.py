# backend/services/competition.py
"""Competitive landscape: who develops drugs on the target, how far, who stopped.

Built from two sources already collected:
  - ChEMBL: the drugs acting on the target (modality, mechanism, max phase)
  - ClinicalTrials.gov: the trials of each drug (sponsor, status, why stopped)

Answers the questions a project team asks before committing: is there an
approved drug (first-in-class is gone)? how many companies are active? did
programs stop, and why (efficacy, safety, business)? is anyone in my indication?

fetch_landscape() does the network calls; build_landscape() is pure and tested
offline.
"""

import asyncio
import logging
import re
from collections import Counter

from backend.services.clinicaltrials import fetch_drug_trials

logger = logging.getLogger(__name__)

MAX_DRUGS = 6  # most advanced drugs searched in ClinicalTrials.gov

ACTIVE_STATUSES = {
    "RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "NOT_YET_RECRUITING",
    "ENROLLING_BY_INVITATION",
}
STOPPED_STATUSES = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}

# Why a trial stopped, from its free-text reason. Order matters: the first
# category whose words appear wins ("lack of efficacy" before "business").
STOP_CATEGORIES = [
    ("Safety", ("safety", "toxicit", "adverse", "side effect", "tolerab")),
    (
        "Efficacy",
        (
            "efficacy",
            "futility",
            "lack of benefit",
            "lack of response",
            "no benefit",
            "insufficient activity",
            "interim analysis",
        ),
    ),
    ("Enrollment", ("accrual", "enrol", "recruit", "participants", "slow")),
    (
        "Business",
        (
            "business",
            "strategic",
            "portfolio",
            "sponsor decision",
            "commercial",
            "competitive",
            "funding",
            "company decision",
            "development program",
        ),
    ),
]


def classify_stop_reason(reason: str | None) -> str:
    """'Funding Sponsor no longer supporting study' -> 'Business'."""
    if not reason:
        return "Not given"
    text = reason.lower()
    for category, words in STOP_CATEGORIES:
        if any(w in text for w in words):
            return category
    return "Other"


def _phase_label(phases: list[str]) -> str:
    """['PHASE1', 'PHASE2'] -> 'Phase 1/2'; ['NA'] or [] -> 'N/A'."""
    numbers = [p.replace("PHASE", "") for p in phases if p.startswith("PHASE")]
    if not numbers:
        return "N/A"
    return "Phase " + "/".join(numbers)


def _matches_indication(conditions: list[str], names: list[str]) -> bool:
    """True if a trial condition mentions the indication or one of its synonyms.

    Whole-word matching, so "CLL" does not match inside another word.
    """
    for name in names:
        pattern = re.compile(rf"\b{re.escape(name.lower())}\b")
        if any(pattern.search(c.lower()) for c in conditions):
            return True
    return False


def _select_drugs(chembl: dict | None) -> list[dict]:
    """Clinical-stage drugs (phase >= 1), most advanced first, named ones only."""
    drugs = [
        d
        for d in (chembl or {}).get("drugs") or []
        if d.get("name") and (d.get("max_phase") or 0) >= 1
    ]
    return drugs[:MAX_DRUGS]  # already sorted by phase in chembl.py


def build_landscape(
    drugs: list[dict],
    trials_by_drug: dict[str, dict | None],
    indication_names: list[str] | None = None,
) -> dict:
    """Aggregate per drug and overall. Pure function.

    Args:
        drugs: ChEMBL drug records (name, max_phase, molecule_type, action_type...)
        trials_by_drug: {drug name: fetch_drug_trials() result, None if failed}
        indication_names: the indication and its synonyms, to count trials in it
    """
    indication_names = [n for n in (indication_names or []) if n and len(n) >= 3]
    seen_trials: dict[str, dict] = {}  # combination trials appear under several drugs
    trial_drugs: dict[str, list[str]] = {}  # nct id -> drugs of the target it tests
    programs = []
    for drug in drugs:
        result = trials_by_drug.get(drug["name"])
        trials = (result or {}).get("trials") or []
        for trial in trials:
            seen_trials[trial["nct_id"]] = trial
            names = trial_drugs.setdefault(trial["nct_id"], [])
            if drug["name"].capitalize() not in names:
                names.append(drug["name"].capitalize())
        sponsors = Counter(t["sponsor"] for t in trials if t.get("sponsor_class") == "INDUSTRY")
        programs.append(
            {
                "drug": drug["name"],
                "molecule_chembl_id": drug.get("molecule_chembl_id"),
                "modality": drug.get("molecule_type"),
                "action_type": drug.get("action_type"),
                "max_phase": drug.get("phase_label") or drug.get("max_phase"),
                "selective": drug.get("via_target_type") == "SINGLE PROTEIN",
                "target_variant": drug.get("target_variant"),
                # None = ClinicalTrials.gov failed for this drug (unknown, not zero)
                "trial_count": result["total"] if result else None,
                "active_trials": sum(t["status"] in ACTIVE_STATUSES for t in trials)
                if result
                else None,
                "stopped_trials": sum(t["status"] in STOPPED_STATUSES for t in trials)
                if result
                else None,
                "main_industry_sponsor": sponsors.most_common(1)[0][0] if sponsors else None,
                "url": drug.get("url"),
            }
        )

    trials = list(seen_trials.values())
    stopped = sorted(
        (t for t in trials if t["status"] in STOPPED_STATUSES),
        key=lambda t: t.get("start_date") or "",
        reverse=True,
    )
    in_indication = [
        t
        for t in trials
        if indication_names and _matches_indication(t["conditions"], indication_names)
    ]
    industry = Counter(t["sponsor"] for t in trials if t.get("sponsor_class") == "INDUSTRY")

    return {
        "programs": programs,
        "approved_count": sum(1 for d in drugs if (d.get("max_phase") or 0) >= 4),
        "trial_count": len(trials),
        "active_trial_count": sum(t["status"] in ACTIVE_STATUSES for t in trials),
        "industry_sponsors": [{"name": n, "trials": c} for n, c in industry.most_common(10)],
        "academic_trial_share": round(
            sum(t.get("sponsor_class") != "INDUSTRY" for t in trials) / len(trials), 2
        )
        if trials
        else None,
        "stop_reasons": dict(Counter(classify_stop_reason(t["why_stopped"]) for t in stopped)),
        "stopped_trials": [
            {
                **{
                    k: t[k]
                    for k in (
                        "nct_id",
                        "title",
                        "status",
                        "why_stopped",
                        "sponsor",
                        "start_date",
                        "link",
                    )
                },
                "phase": _phase_label(t["phases"]),
                "reason_category": classify_stop_reason(t["why_stopped"]),
                "target_drugs": trial_drugs.get(t["nct_id"], []),
                "sponsor_class": t.get("sponsor_class"),
            }
            for t in stopped[:15]
        ],
        "indication_trials": [
            {
                **{k: t[k] for k in ("nct_id", "title", "status", "sponsor", "start_date", "link")},
                "phase": _phase_label(t["phases"]),
                "drugs": [i for i in t["interventions"] if i][:4],
            }
            for t in sorted(in_indication, key=lambda t: t.get("start_date") or "", reverse=True)[
                :15
            ]
        ]
        if indication_names
        else None,
        "phase_counts": dict(Counter(_phase_label(t["phases"]) for t in trials)),
        # True when at least one drug's trial list could not be fetched
        "incomplete": any(trials_by_drug.get(d["name"]) is None for d in drugs),
    }


async def fetch_landscape(chembl: dict | None, indication_names: list[str] | None = None) -> dict:
    """Trials of the most advanced drugs, in parallel, then build_landscape().

    found=None when ChEMBL itself was unavailable (no drug list to start from).
    """
    if not chembl or chembl.get("found") is None:
        return {"found": None}
    drugs = _select_drugs(chembl)
    if not drugs:
        return {"found": True, **build_landscape([], {}, indication_names)}
    results = await asyncio.gather(*(fetch_drug_trials(d["name"]) for d in drugs))
    landscape = build_landscape(
        drugs, dict(zip((d["name"] for d in drugs), results, strict=True)), indication_names
    )
    logger.info(
        "Competition: %d drugs, %d trials, %d active",
        len(drugs),
        landscape["trial_count"],
        landscape["active_trial_count"],
    )
    return {"found": True, **landscape}
