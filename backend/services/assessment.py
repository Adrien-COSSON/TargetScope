# backend/services/assessment.py
"""Orchestration: resolve a target, run every source in parallel, assess.

    query (+ optional indication)
        -> resolver (canonical Target, ids for every source)
        -> 12 service calls in parallel (asyncio.gather)
        -> evidence_engine.assess() (pure function)
        -> TargetAssessment

This is the only module that touches both the network and the engine:
the router and the frontend call run_assessment(), nothing else.
"""

import asyncio
import logging
import time

from backend.schemas.evidence import TargetAssessment
from backend.services.chembl import fetch_chembl
from backend.services.clinicaltrials import count_trials
from backend.services.clinvar import fetch_clinvar
from backend.services.europe_pmc import publication_timeline
from backend.services.evidence_engine import assess
from backend.services.hpa import fetch_hpa
from backend.services.mgi import fetch_mgi
from backend.services.open_targets import fetch_open_targets
from backend.services.pubmed import fetch_pubmed
from backend.services.reactome import fetch_reactome
from backend.services.resolver import resolve_target
from backend.services.string_db import fetch_string
from backend.services.uniprot import fetch_uniprot

logger = logging.getLogger(__name__)


async def _safe(name: str, coro):
    """Run one service; an unexpected exception becomes None (= unavailable).

    Services already return None on network failures. This guard is for bugs
    or unforeseen API shapes: one broken source must not sink the whole
    assessment, and the engine already knows how to handle "unavailable".
    """
    try:
        return await coro
    except Exception:
        # logger.exception logs the full traceback: the bug stays visible
        logger.exception("Assessment: source '%s' raised, treated as unavailable", name)
        return None


async def _none():
    """Placeholder when a source cannot be queried (missing identifier)."""
    return None


async def collect(target, indication_id: str | None = None) -> dict:
    """Query every source in parallel for a resolved Target.

    A source whose identifier is missing on the Target (e.g. no UniProt
    accession) is not called and comes back as None (unavailable).

    Returns:
        dict: {source name: service result}, the input of assess().
    """
    gene = target.gene_symbol

    def needs(identifier, coro_factory):
        # Build the coroutine only if the identifier exists, so nothing is
        # created (and left un-awaited) for a source we cannot query
        return coro_factory() if identifier else _none()

    calls = {
        "open_targets": needs(target.ensembl_id, lambda: fetch_open_targets(target.ensembl_id, indication_id)),
        "chembl": needs(target.uniprot_id, lambda: fetch_chembl(target.uniprot_id)),
        "clinvar": fetch_clinvar(gene),
        "uniprot": needs(target.uniprot_id, lambda: fetch_uniprot(target.uniprot_id)),
        "pubmed": fetch_pubmed(gene),
        "europe_pmc": publication_timeline(gene),
        "reactome": needs(target.uniprot_id, lambda: fetch_reactome(target.uniprot_id)),
        "string": needs(target.uniprot_id, lambda: fetch_string(target.uniprot_id)),
        "mgi": needs(target.hgnc_id, lambda: fetch_mgi(target.hgnc_id)),
        "hpa": needs(target.ensembl_id, lambda: fetch_hpa(target.ensembl_id)),
        "clinicaltrials": count_trials(gene),
    }

    start = time.perf_counter()
    values = await asyncio.gather(*(_safe(name, coro) for name, coro in calls.items()))
    results = dict(zip(calls, values))
    logger.info("Assessment: %d sources collected for %s in %.1fs",
                len(results), gene, time.perf_counter() - start)

    # The engine expects {"total": ...} for ClinicalTrials (count_trials returns an int)
    results["clinicaltrials"] = {"total": results["clinicaltrials"]}
    return results


async def run_assessment(
    query: str, indication_id: str | None = None
) -> tuple[TargetAssessment, dict]:
    """Full pipeline for one user query.

    Args:
        query: anything the resolver accepts ("EGFR", "HER1", "P00533"...).
        indication_id: optional EFO / MONDO id chosen in the indication menus.

    Returns:
        (assessment, raw results). The raw results feed the detailed tabs of
        the frontend (pathway list, drug table...), the assessment feeds the
        radar, gaps, safety and the PDF.

    Raises:
        ValueError: the query matches no gene (resolver).
        ResolutionUnavailableError: MyGene.info could not be reached.
    """
    target = await resolve_target(query)
    results = await collect(target, indication_id)
    return assess(target, results), results
