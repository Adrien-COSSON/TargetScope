# tests/test_sources.py
"""Tests of the data-source services (network replaced by fakes).

Each service must respect the same convention:
  None = the source could not be reached (unknown)
  0 / [] / found=False = the source answered, and has nothing
"""

import asyncio
import time
from datetime import date

import pytest

from backend.services import (
    chembl,
    clinicaltrials,
    europe_pmc,
    mgi,
    pubmed,
    reactome,
    string_db,
    uniprot,
)
from backend.utils import ncbi


def fake_get(monkeypatch, module, answer):
    """Replace module.get_json by a fake. `answer` is a value or f(url, params)."""
    calls = []

    async def fake(url, params=None, source="", not_found=None):
        calls.append((url, params))
        return answer(url, params) if callable(answer) else answer

    monkeypatch.setattr(module, "get_json", fake)
    return calls


# ---------------------------------------------------------------------------
# NCBI helpers
# ---------------------------------------------------------------------------
class TestNcbi:
    def test_extract_count_none_vs_zero(self):
        assert ncbi.extract_count(None) is None
        assert ncbi.extract_count({"esearchresult": {"count": "0"}}) == 0
        assert ncbi.extract_count({"esearchresult": {"count": "31800"}}) == 31800

    def test_extract_ids(self):
        assert ncbi.extract_ids(None) is None
        assert ncbi.extract_ids({"esearchresult": {"idlist": ["1", "2"]}}) == ["1", "2"]

    def test_params_carry_identification(self):
        params = ncbi.ncbi_params("clinvar", term="BRCA1[gene]", retmax=0)
        assert params["db"] == "clinvar" and params["tool"] == "TargetScope"
        assert params["term"] == "BRCA1[gene]" and params["retmode"] == "json"

    def test_throttle_spaces_request_starts(self, monkeypatch):
        monkeypatch.setattr(ncbi, "_MIN_INTERVAL", 0.05)
        starts = []

        async def fake(url, params=None, source=""):
            starts.append(time.monotonic())
            return {}

        monkeypatch.setattr(ncbi, "get_json", fake)

        async def burst():
            await asyncio.gather(*(ncbi.ncbi_get("u", {}, "T") for _ in range(4)))

        asyncio.run(burst())
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        assert all(g >= 0.045 for g in gaps)  # small tolerance on timer resolution


def test_pubmed_counts_and_failure(monkeypatch):
    fake_get(monkeypatch, ncbi, {"esearchresult": {"count": "12"}})
    r = asyncio.run(pubmed.fetch_pubmed("TP53"))
    assert (r["total_count"], r["recent_count"]) == (12, 12)
    year = date.today().year
    assert r["recent_window"] == f"{year - 4}-{year}"

    fake_get(monkeypatch, ncbi, None)
    r = asyncio.run(pubmed.fetch_pubmed("TP53"))
    assert (r["total_count"], r["recent_count"]) == (None, None)


# ---------------------------------------------------------------------------
# ChEMBL
# ---------------------------------------------------------------------------
class TestChembl:
    def test_build_drugs_merges_salts_and_prefers_single_protein(self):
        mechanisms = [
            {"molecule_chembl_id": "VAN", "target_chembl_id": "FAM", "action_type": "INHIBITOR"},
            {"molecule_chembl_id": "VAN", "target_chembl_id": "T1", "action_type": "INHIBITOR"},
            {"molecule_chembl_id": "SALT", "parent_molecule_chembl_id": "ERL", "target_chembl_id": "T1"},
            {"molecule_chembl_id": "SOT", "target_chembl_id": "T1",
             "variant_sequence": {"mutation": "G12C"}},
        ]
        molecules = {"VAN": {"pref_name": "VANDETANIB", "max_phase": "4.0"},
                     "ERL": {"pref_name": "ERLOTINIB", "max_phase": 4.0, "black_box_warning": 1},
                     "SOT": {"pref_name": "SOTORASIB", "max_phase": "4.0"}}
        types = {"T1": "SINGLE PROTEIN", "FAM": "PROTEIN FAMILY"}
        drugs = {d["name"]: d for d in chembl._build_drugs(mechanisms, molecules, types)}
        assert set(drugs) == {"VANDETANIB", "ERLOTINIB", "SOTORASIB"}
        assert drugs["VANDETANIB"]["via_target_type"] == "SINGLE PROTEIN"
        assert drugs["ERLOTINIB"]["black_box_warning"] is True
        assert drugs["SOTORASIB"]["target_variant"] == "G12C"

    def test_summary(self):
        drugs = [{"max_phase": 4.0, "molecule_type": "Antibody", "action_type": "INHIBITOR",
                  "withdrawn": False, "black_box_warning": True, "target_variant": None},
                 {"max_phase": 2.0, "molecule_type": None, "action_type": None,
                  "withdrawn": True, "black_box_warning": False, "target_variant": "G12C"}]
        s = chembl._summarize_drugs(drugs)
        assert (s["approved_count"], s["max_phase_label"], s["withdrawn_count"],
                s["black_box_count"], s["variant_selective_count"]) == (1, "Approved", 1, 1, 1)
        assert s["modality_counts"] == {"Antibody": 1, "Unknown": 1}

    def test_not_in_chembl_vs_unreachable(self, monkeypatch):
        fake_get(monkeypatch, chembl, {"targets": []})
        assert asyncio.run(chembl.fetch_chembl("Q96LT7"))["found"] is False
        fake_get(monkeypatch, chembl, None)
        r = asyncio.run(chembl.fetch_chembl("Q96LT7"))
        assert r["found"] is None and r["drugs"] is None

    def test_human_targets_only(self, monkeypatch):
        fake_get(monkeypatch, chembl, {"targets": [
            {"target_chembl_id": "H", "target_type": "SINGLE PROTEIN", "organism": "Homo sapiens"},
            {"target_chembl_id": "M", "target_type": "PROTEIN-PROTEIN INTERACTION",
             "organism": "Mus musculus"}]})
        assert [t["target_chembl_id"] for t in asyncio.run(chembl._find_targets("P"))] == ["H"]


# ---------------------------------------------------------------------------
# MGI (via Alliance)
# ---------------------------------------------------------------------------
def ortholog(curie, mgi_id, best="Yes", confidence="high"):
    return {"geneToGeneOrthologyGenerated": {
        "objectGene": {"primaryExternalId": mgi_id, "geneSymbol": {"displayText": mgi_id},
                       "taxon": {"curie": curie}},
        "isBestScore": {"name": best}, "confidence": {"name": confidence}}}


def phenotype_record(term, curie, pmid):
    return {"primaryAnnotations": [{"evidenceItem": {"referenceID": pmid},
                                    "phenotypeTerms": [{"curie": curie, "name": term}]}]}


class TestMgi:
    def test_picks_best_mouse_ortholog(self):
        rows = [ortholog("NCBITaxon:7955", "ZFIN:1"),
                ortholog("NCBITaxon:10090", "MGI:2", best="No", confidence="low"),
                ortholog("NCBITaxon:10090", "MGI:1")]
        picked = mgi._pick_mouse_ortholog(rows)
        assert (picked["mgi_id"], picked["candidates"]) == ("MGI:1", 2)

    def test_phenotypes_merged_all_references_count(self):
        records = [phenotype_record("embryonic lethality", "MP:1", "PMID:1"),
                   phenotype_record("embryonic lethality", "MP:1", "PMID:2"),
                   phenotype_record("abnormal heart", "MP:2", "MGI:9")]
        parsed = mgi._parse_phenotypes(records)
        assert parsed[0]["term"] == "embryonic lethality"
        assert parsed[0]["reference_count"] == 2
        assert parsed[0]["pmids"] == ["PMID:1", "PMID:2"]
        # an MGI-only reference counts, links to MGI, and is not a PMID
        assert parsed[1]["reference_count"] == 1
        assert parsed[1]["pmids"] == []
        assert parsed[1]["references"][0]["url"] == (
            "https://www.informatics.jax.org/reference/MGI:9")

    def test_disease_models_positive_only(self):
        records = [{"diseaseModels": [{"associationType": "IS_MODEL_OF", "diseaseModel": "PDAC"}]},
                   {"diseaseModels": [{"associationType": "IS_MODEL_OF", "diseaseModel": "PDAC"},
                                      {"associationType": "DOES_NOT_MODEL", "diseaseModel": "AML"}]}]
        assert mgi._parse_disease_models(records) == [{"disease": "PDAC", "model_count": 2}]

    def test_full_flow(self, monkeypatch):
        def answer(url, params):
            if url.endswith("/orthologs"):
                return {"results": [ortholog("NCBITaxon:10090", "MGI:96680")]}
            if url.endswith("/phenotypes"):
                return {"total": 1, "results": [phenotype_record("embryonic lethality", "MP:1", "PMID:1")]}
            return {"results": [{"diseaseModels": [{"associationType": "IS_MODEL_OF",
                                                    "diseaseModel": "PDAC"}]}]}

        fake_get(monkeypatch, mgi, answer)
        r = asyncio.run(mgi.fetch_mgi("HGNC:6407"))
        assert r["found"] is True and r["has_lethality"] is True
        assert r["disease_models"] == [{"disease": "PDAC", "model_count": 1}]

    def test_no_hgnc_and_no_ortholog(self, monkeypatch):
        assert asyncio.run(mgi.fetch_mgi(None))["found"] is None
        fake_get(monkeypatch, mgi, {"results": [ortholog("NCBITaxon:7955", "ZFIN:1")]})
        assert asyncio.run(mgi.fetch_mgi("HGNC:1"))["found"] is False


# ---------------------------------------------------------------------------
# Reactome, STRING, Europe PMC, ClinicalTrials, UniProt
# ---------------------------------------------------------------------------
def test_reactome_404_means_no_pathway(monkeypatch):
    seen = {}

    async def fake(url, params=None, source="", not_found=None):
        seen["not_found"] = not_found
        return not_found  # what get_json returns on a 404

    monkeypatch.setattr(reactome, "get_json", fake)
    r = asyncio.run(reactome.fetch_reactome("Q96LT7"))
    assert seen["not_found"] == [] and r["pathways"] == []


def test_reactome_parses_and_fails(monkeypatch):
    fake_get(monkeypatch, reactome, [{"stId": "R-HSA-1", "displayName": " Signaling "},
                                     {"displayName": "no id"}])
    r = asyncio.run(reactome.fetch_reactome("P00533"))
    assert [(p["pathway_id"], p["name"]) for p in r["pathways"]] == [("R-HSA-1", "Signaling")]
    fake_get(monkeypatch, reactome, None)
    assert asyncio.run(reactome.fetch_reactome("P00533"))["pathways"] is None


def test_string_partners_sorted(monkeypatch):
    fake_get(monkeypatch, string_db, [
        {"preferredName_B": "GRB2", "stringId_A": "9606.ENSP1", "stringId_B": "9606.ENSP2", "score": 0.9},
        {"preferredName_B": "SOS1", "stringId_A": "9606.ENSP1", "stringId_B": "9606.ENSP3", "score": 0.99}])
    r = asyncio.run(string_db.fetch_string("P00533"))
    assert [i["partner"] for i in r["interactions"]] == ["SOS1", "GRB2"]
    fake_get(monkeypatch, string_db, None)
    assert asyncio.run(string_db.fetch_string("P00533"))["interactions"] is None


def test_europe_pmc_failed_years(monkeypatch):
    bad_year = "PUB_YEAR:2000"

    def answer(url, params):
        return None if bad_year in params["query"] else {"hitCount": 5}

    fake_get(monkeypatch, europe_pmc, answer)
    r = asyncio.run(europe_pmc.publication_timeline("EGFR"))
    assert r["timeline"]["2000"] is None and r["timeline"]["2001"] == 5
    assert r["failed_years"] == 1

    fake_get(monkeypatch, europe_pmc, None)
    r = asyncio.run(europe_pmc.publication_timeline("EGFR"))
    assert r["timeline"] is None


def test_europe_pmc_title_abstract_scope(monkeypatch):
    calls = fake_get(monkeypatch, europe_pmc, {"hitCount": 1})
    asyncio.run(europe_pmc.publication_timeline("KIT"))
    assert calls[0][1]["query"].startswith('(TITLE:"KIT" OR ABSTRACT:"KIT")')


@pytest.mark.parametrize("answer, expected", [({"totalCount": 42}, 42), ({}, 0), (None, None)])
def test_count_trials(monkeypatch, answer, expected):
    fake_get(monkeypatch, clinicaltrials, answer)
    assert asyncio.run(clinicaltrials.count_trials("XPO1")) == expected


def test_fetch_trials_none_vs_empty(monkeypatch):
    fake_get(monkeypatch, clinicaltrials, None)
    assert asyncio.run(clinicaltrials.fetch_trials("XPO1")) is None
    fake_get(monkeypatch, clinicaltrials, {"studies": []})
    assert asyncio.run(clinicaltrials.fetch_trials("XPO1")) == []


def test_uniprot_failure(monkeypatch):
    fake_get(monkeypatch, uniprot, None)
    assert asyncio.run(uniprot.fetch_uniprot("P00533")) == {"found": None}
