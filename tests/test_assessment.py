# tests/test_assessment.py
"""Tests of the orchestration: isolation of sources and collection wiring."""

import asyncio

from backend.services import assessment
from tests import factories as f


def test_slow_source_is_cut(monkeypatch):
    monkeypatch.setattr(assessment, "SOURCE_TIMEOUT", 0.05)

    async def slow():
        await asyncio.sleep(1)
        return "late"

    assert asyncio.run(assessment._safe("slow", slow())) is None


def test_crashing_source_is_isolated():
    async def boom():
        raise KeyError("unexpected API shape")

    assert asyncio.run(assessment._safe("boom", boom())) is None


def test_collect_skips_sources_without_identifier(monkeypatch):
    called = []

    def fake(name, value):
        async def service(*args, **kwargs):
            called.append(name)
            return value
        return service

    for name, fn in [("open_targets", "fetch_open_targets"), ("chembl", "fetch_chembl"),
                     ("clinvar", "fetch_clinvar"), ("uniprot", "fetch_uniprot"),
                     ("pubmed", "fetch_pubmed"), ("europe_pmc", "publication_timeline"),
                     ("reactome", "fetch_reactome"), ("string", "fetch_string"),
                     ("mgi", "fetch_mgi"), ("hpa", "fetch_hpa"), ("clinicaltrials", "count_trials"),
                     ("gene_info", "fetch_gene_info"), ("alphafold", "fetch_alphafold"),
                     ("hpa_profiles", "fetch_hpa_profiles"), ("ot_extras", "fetch_ot_extras"),
                     ("competition", "fetch_landscape"),
                     ("mutation_landscape", "fetch_mutation_landscape")]:
        monkeypatch.setattr(assessment, fn, fake(name, 7 if name == "clinicaltrials" else {"ok": name}))

    # No UniProt accession: ChEMBL, UniProt, Reactome and STRING are not called
    t = f.target("XYZ", ensembl_id="ENSG1", hgnc_id="HGNC:1")
    results = asyncio.run(assessment.collect(t))
    # entrez_id is missing too: no gene_info. AlphaFold needs UniProt.
    # competition and mutation_landscape are the second wave (always run)
    assert set(called) == {"open_targets", "clinvar", "pubmed", "europe_pmc", "mgi", "hpa",
                           "clinicaltrials", "hpa_profiles", "ot_extras", "competition",
                           "mutation_landscape"}
    assert results["chembl"] is None and results["reactome"] is None
    assert results["clinicaltrials"] == {"total": 7}
