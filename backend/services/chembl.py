# backend/services/chembl.py
"""ChEMBL service: chemical tractability and drug landscape of a target.

Feeds three assessment dimensions:
  - pharmacology / tractability: how much quantitative chemistry exists, how potent
  - modality landscape: which kinds of drugs (small molecule, antibody...) act on it
  - safety: withdrawn drugs and black box warnings among those drugs

Flow:
  1. find every ChEMBL target that contains the UniProt protein
  2. in parallel: activity counts (human single-protein target)
                  + mechanisms of action (all related targets)
  3. molecule details for the drugs found in step 2
"""

import asyncio
import logging
from collections import Counter

from backend.utils.http_client import CHEMBL_BASE_URL, get_json
from backend.utils.provenance import make_provenance

logger = logging.getLogger(__name__)

TARGET_URL = f"{CHEMBL_BASE_URL}/target.json"
ACTIVITY_URL = f"{CHEMBL_BASE_URL}/activity.json"
MECHANISM_URL = f"{CHEMBL_BASE_URL}/mechanism.json"
MOLECULE_URL = f"{CHEMBL_BASE_URL}/molecule.json"
TARGET_REPORT_URL = "https://www.ebi.ac.uk/chembl/target_report_card/{chembl_id}/"
MOLECULE_REPORT_URL = "https://www.ebi.ac.uk/chembl/compound_report_card/{chembl_id}/"

# pChEMBL = -log10(molar IC50/EC50/Ki/Kd). 7 means <= 100 nM: a common "potent" cut-off.
POTENT_PCHEMBL = 7

MAX_TARGETS = 50  # a protein rarely belongs to more complexes/families than that
MAX_MECHANISMS = 1000  # ChEMBL page size limit; enough for even the most drugged targets
MOLECULE_BATCH = 50  # ids per molecule request, keeps the URL short

# Only ask ChEMBL for the molecule fields we use (smaller, faster responses)
MOLECULE_FIELDS = (
    "molecule_chembl_id,pref_name,max_phase,molecule_type,"
    "first_approval,withdrawn_flag,black_box_warning"
)

# Readable labels for ChEMBL max_phase (0.5 = early phase 1)
PHASE_LABELS = {
    4.0: "Approved",
    3.0: "Phase 3",
    2.0: "Phase 2",
    1.0: "Phase 1",
    0.5: "Early phase 1",
}


def _total_count(data: dict | None) -> int | None:
    """Return page_meta.total_count of a list response (None on request failure).

    Same None / 0 convention as ncbi.extract_count: None = unknown, 0 = nothing.
    """
    if data is None:
        return None
    return data.get("page_meta", {}).get("total_count", 0)


def _to_phase(value) -> float | None:
    """ChEMBL returns max_phase as "4.0", 4.0 or null depending on the version."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_single_protein(targets: list[dict]) -> str | None:
    """Return the human SINGLE PROTEIN target id, used for activity counts.

    Activities measured on complexes or families are not specific to our protein,
    so tractability counts only use the single-protein target.
    """
    for t in targets:
        if t.get("target_type") == "SINGLE PROTEIN" and t.get("organism") == "Homo sapiens":
            return t.get("target_chembl_id")
    return None


async def _find_targets(uniprot_id: str) -> list[dict] | None:
    """All ChEMBL targets whose components include this UniProt accession."""
    data = await get_json(
        TARGET_URL,
        params={"target_components__accession": uniprot_id, "limit": MAX_TARGETS},
        source="ChEMBL",
    )
    if data is None:
        return None
    # Keep human targets only: the accession can also appear in
    # cross-species constructs (e.g. a mouse cereblon/human EGFR complex)
    return [t for t in data.get("targets", []) if t.get("organism") == "Homo sapiens"]


async def _count_activities(target_id: str, **filters) -> int | None:
    """Count activities with limit=1: we only read page_meta.total_count."""
    data = await get_json(
        ACTIVITY_URL,
        params={"target_chembl_id": target_id, "limit": 1, **filters},
        source="ChEMBL",
    )
    return _total_count(data)


async def _fetch_mechanisms(target_ids: list[str]) -> list[dict] | None:
    """Mechanisms of action for all related targets, in one call (__in filter)."""
    data = await get_json(
        MECHANISM_URL,
        params={"target_chembl_id__in": ",".join(target_ids), "limit": MAX_MECHANISMS},
        source="ChEMBL",
    )
    if data is None:
        return None
    return data.get("mechanisms", [])


async def _fetch_molecules(molecule_ids: list[str]) -> dict[str, dict] | None:
    """Molecule details keyed by ChEMBL id, fetched in parallel batches."""
    if not molecule_ids:
        return {}
    batches = [
        molecule_ids[i : i + MOLECULE_BATCH]
        for i in range(0, len(molecule_ids), MOLECULE_BATCH)
    ]
    responses = await asyncio.gather(
        *(
            get_json(
                MOLECULE_URL,
                params={
                    "molecule_chembl_id__in": ",".join(batch),
                    "only": MOLECULE_FIELDS,
                    "limit": MOLECULE_BATCH,
                },
                source="ChEMBL",
            )
            for batch in batches
        )
    )
    # One failed batch = incomplete drug list: report it as a failure, not as partial data
    if any(r is None for r in responses):
        return None
    return {
        m["molecule_chembl_id"]: m
        for r in responses
        for m in r.get("molecules", [])
    }


def _build_drugs(
    mechanisms: list[dict],
    molecules: dict[str, dict],
    target_types: dict[str, str],
) -> list[dict]:
    """Merge mechanisms and molecule details into one record per drug.

    A drug can have several mechanism rows (salt forms, several related targets):
    we key on the parent molecule so each drug appears once.
    """
    # Rows on the single protein come first, so when a drug acts both on the
    # protein and on a family containing it, we keep the more specific row.
    # sorted() is stable: the API order is kept within each group.
    mechanisms = sorted(
        mechanisms,
        key=lambda m: target_types.get(m.get("target_chembl_id")) != "SINGLE PROTEIN",
    )
    drugs: dict[str, dict] = {}
    for mech in mechanisms:
        # parent id groups salts with their parent compound (e.g. erlotinib HCl -> erlotinib)
        mol_id = mech.get("parent_molecule_chembl_id") or mech.get("molecule_chembl_id")
        if not mol_id or mol_id in drugs:
            continue
        mol = molecules.get(mol_id, {})
        phase = _to_phase(mol.get("max_phase"))
        drugs[mol_id] = {
            "molecule_chembl_id": mol_id,
            "name": mol.get("pref_name"),
            "molecule_type": mol.get("molecule_type"),
            "max_phase": phase,
            "phase_label": PHASE_LABELS.get(phase),
            "first_approval": mol.get("first_approval"),
            "action_type": mech.get("action_type"),
            "mechanism": mech.get("mechanism_of_action"),
            # e.g. "G12C" for sotorasib: the drug targets a mutant form only
            "target_variant": (mech.get("variant_sequence") or {}).get("mutation"),
            # tells whether the drug hits our protein alone or a complex/family containing it
            "via_target_type": target_types.get(mech.get("target_chembl_id")),
            "withdrawn": bool(mol.get("withdrawn_flag")),
            # ChEMBL uses 1/0 for black_box_warning
            "black_box_warning": mol.get("black_box_warning") == 1,
            "url": MOLECULE_REPORT_URL.format(chembl_id=mol_id),
        }
    # Most advanced drugs first; unknown phase goes last
    return sorted(drugs.values(), key=lambda d: d["max_phase"] or -1, reverse=True)


def _summarize_drugs(drugs: list[dict]) -> dict:
    """Aggregates used by evidence_engine (modality landscape, safety flags)."""
    phases = [d["max_phase"] for d in drugs if d["max_phase"] is not None]
    max_phase = max(phases, default=None)
    return {
        "drug_count": len(drugs),
        "approved_count": sum(1 for d in drugs if d["max_phase"] == 4.0),
        "max_phase": max_phase,
        "max_phase_label": PHASE_LABELS.get(max_phase),
        # e.g. {"Small molecule": 12, "Antibody": 4}
        "modality_counts": dict(
            Counter(d["molecule_type"] or "Unknown" for d in drugs)
        ),
        "action_types": dict(Counter(d["action_type"] or "Unknown" for d in drugs)),
        # drugs acting on a mutant form only (e.g. KRAS G12C inhibitors)
        "variant_selective_count": sum(1 for d in drugs if d["target_variant"]),
        "withdrawn_count": sum(d["withdrawn"] for d in drugs),
        "black_box_count": sum(d["black_box_warning"] for d in drugs),
    }


def _empty_result(uniprot_id: str, found: bool | None) -> dict:
    """Result skeleton when we stop early.

    found=False: ChEMBL answered, the protein is simply not in ChEMBL (counts = 0).
    found=None : ChEMBL could not be reached (everything unknown = None).
    """
    zero = 0 if found is False else None
    empty = [] if found is False else None
    return {
        "uniprot_id": uniprot_id,
        "found": found,
        "target_chembl_id": None,
        "related_targets": empty,
        "activity_count": zero,
        "potent_activity_count": zero,
        "drugs": empty,
        "drug_summary": None,
        "provenance": None,
    }


async def fetch_chembl(uniprot_id: str) -> dict:
    """Fetch chemical tractability and known drugs from ChEMBL for a protein.

    Args:
        uniprot_id: UniProt accession, e.g. "P00533" (EGFR).

    Returns:
        A dict with keys:
            - uniprot_id (str)
            - found (bool | None): False if the protein is not in ChEMBL,
              None if ChEMBL could not be reached
            - target_chembl_id (str | None): human single-protein target
            - related_targets (list[dict] | None): every target containing the
              protein (id, type, name), incl. complexes and families
            - activity_count (int | None): activities with a pChEMBL value
            - potent_activity_count (int | None): pChEMBL >= POTENT_PCHEMBL
            - drugs (list[dict] | None): one record per drug with a known
              mechanism on any related target, most advanced first
              (incl. target_variant, e.g. "G12C", for mutant-selective drugs)
            - drug_summary (dict | None): counts per modality / action type,
              max phase, withdrawn and black box counts
            - provenance (Provenance | None)
        None means "unknown" (request failed), 0 / [] means "nothing in ChEMBL".
        Activity counts are activity records, not distinct compounds.
    """
    logger.info("ChEMBL: fetch_chembl started for '%s'", uniprot_id)

    # Step 1: which ChEMBL targets contain this protein?
    targets = await _find_targets(uniprot_id)
    if targets is None:
        logger.error("ChEMBL: target lookup failed for '%s'", uniprot_id)
        return _empty_result(uniprot_id, found=None)
    if not targets:
        logger.info("ChEMBL: no target found for '%s'", uniprot_id)
        return _empty_result(uniprot_id, found=False)

    single_id = _pick_single_protein(targets)
    target_ids = [t["target_chembl_id"] for t in targets]
    target_types = {t["target_chembl_id"]: t.get("target_type") for t in targets}

    # Step 2: activity counts and mechanisms do not depend on each other -> parallel.
    # Without a single-protein target there is no specific activity to count.
    async def _none():
        return None

    activity_call = (
        _count_activities(single_id, pchembl_value__isnull="false")
        if single_id
        else _none()
    )
    potent_call = (
        _count_activities(single_id, pchembl_value__gte=POTENT_PCHEMBL)
        if single_id
        else _none()
    )
    activity_count, potent_count, mechanisms = await asyncio.gather(
        activity_call, potent_call, _fetch_mechanisms(target_ids)
    )

    # Step 3: molecule details depend on the mechanisms -> sequential
    drugs = None
    if mechanisms is not None:
        mol_ids = sorted(
            {
                m.get("parent_molecule_chembl_id") or m.get("molecule_chembl_id")
                for m in mechanisms
            }
            - {None}
        )
        molecules = await _fetch_molecules(mol_ids)
        if molecules is not None:
            drugs = _build_drugs(mechanisms, molecules, target_types)

    result = {
        "uniprot_id": uniprot_id,
        "found": True,
        "target_chembl_id": single_id,
        "related_targets": [
            {
                "target_chembl_id": t["target_chembl_id"],
                "target_type": t.get("target_type"),
                "name": t.get("pref_name"),
            }
            for t in targets
        ],
        "activity_count": activity_count,
        "potent_activity_count": potent_count,
        "drugs": drugs,
        "drug_summary": None if drugs is None else _summarize_drugs(drugs),
        "provenance": make_provenance(
            source="ChEMBL",
            source_id=single_id,
            url=TARGET_REPORT_URL.format(chembl_id=single_id) if single_id else None,
            # curated mechanisms on a single-protein target = high; otherwise medium
            confidence="high" if single_id else "medium",
        ),
    }

    logger.info(
        "ChEMBL: fetch_chembl ended for '%s' (target=%s, activities=%s, drugs=%s)",
        uniprot_id,
        single_id,
        activity_count,
        None if drugs is None else len(drugs),
    )
    return result
