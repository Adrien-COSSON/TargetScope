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
from backend.services.alphafold import fetch_alphafold
from backend.services.chembl import fetch_chembl
from backend.services.clinicaltrials import count_trials
from backend.services.clinvar import fetch_clinvar
from backend.services.competition import fetch_landscape
from backend.services.europe_pmc import publication_timeline
from backend.services.evidence_engine import assess
from backend.services.gene_info import fetch_gene_info
from backend.services.hpa import fetch_hpa, fetch_hpa_profiles
from backend.services.mgi import fetch_mgi
from backend.services.open_targets import (
    fetch_mutation_landscape,
    fetch_open_targets,
    fetch_ot_extras,
)
from backend.services.pubmed import fetch_pubmed
from backend.services.reactome import fetch_reactome
from backend.services.resolver import resolve_target
from backend.services.string_db import fetch_string
from backend.services.uniprot import fetch_uniprot

logger = logging.getLogger(__name__)

# Hard limit per source. Each HTTP request already times out at 20 s and is
# retried (http_client), so a slow first call + a fast retry takes ~30 s
# (seen on ChEMBL / EGFR, probably a cold server-side cache). 45 s lets that
# case through while still bounding a real hang: past it, the source is
# reported as unavailable, which the engine handles.
SOURCE_TIMEOUT = 45  # seconds


async def _safe(name: str, coro):
    """Run one service; a timeout or an unexpected exception becomes None.

    Services already return None on network failures. This guard adds:
      - a per-source time limit (SOURCE_TIMEOUT)
      - a net for bugs or unforeseen API shapes
    One slow or broken source must not sink the whole assessment, and the
    engine already knows how to handle "unavailable".
    """
    try:
        # wait_for cancels the service call if it runs past the limit
        return await asyncio.wait_for(coro, timeout=SOURCE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Assessment: source '%s' exceeded %ds, treated as unavailable",
                       name, SOURCE_TIMEOUT)
        return None
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
        # Identity card and Tool Box (context: they do not change the scores)
        "gene_info": needs(target.entrez_id, lambda: fetch_gene_info(target.entrez_id)),
        "alphafold": needs(target.uniprot_id, lambda: fetch_alphafold(target.uniprot_id)),
        "hpa_profiles": needs(target.ensembl_id, lambda: fetch_hpa_profiles(target.ensembl_id)),
        "ot_extras": needs(target.ensembl_id, lambda: fetch_ot_extras(target.ensembl_id)),
    }

    start = time.perf_counter()
    values = await asyncio.gather(*(_safe(name, coro) for name, coro in calls.items()))
    results = dict(zip(calls, values))

    # Second wave: these need first-wave results (the drug list, the associations)
    ot = results["open_targets"] or {}
    indication = ot.get("indication") or {}
    indication_names = [indication.get("disease_name"), *indication.get("synonyms", [])] if indication else None
    competition, mutations = await asyncio.gather(
        _safe("competition", fetch_landscape(results["chembl"], indication_names)),
        _safe("mutation_landscape",
              fetch_mutation_landscape(target.ensembl_id, ot.get("top_associations"))),
    )
    results["competition"] = competition
    results["mutation_landscape"] = mutations

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
