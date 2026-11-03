# tests/test_frontend.py
"""Smoke tests of the Streamlit frontend (streamlit.testing AppTest).

The API client is replaced by fakes returning a realistic XPO1 x CLL
assessment built by the real engine from fake source results: every tab is
rendered, and any exception in a tab fails the test.
"""

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")

from streamlit.testing.v1 import AppTest  # noqa: E402

from backend.services import competition, evidence_engine  # noqa: E402
from tests import factories as f  # noqa: E402
from tests.test_evidence_engine import xpo1_results  # noqa: E402
from tests.test_v1_sources import ELTANEXOR, HPA_XML, SELINEXOR, trial  # noqa: E402


def xpo1_raw() -> dict:
    from backend.services.hpa import parse_hpa_xml

    ind = f.indication(
        "chronic lymphocytic leukemia",
        direct={"somatic_mutation": 0.77, "genetic_association": 0.48, "clinical": 0.08},
        mutation={"disease_name": "CLL", "mutated": 180, "tested": 3865, "frequency": 0.047},
        synonyms=["CLL"],
    )
    raw = xpo1_results(ind)
    raw["open_targets"]["top_associations"] = [
        f.assoc("multiple myeloma", score=0.64, clinical=0.97, literature=0.88),
        f.assoc("CLL", score=0.63, somatic_mutation=0.77),
    ]
    raw["chembl"]["drugs"] = [{**SELINEXOR, "url": "https://x"}, {**ELTANEXOR, "url": "https://y"}]
    raw["chembl"]["target_chembl_id"] = "CHEMBL5661"
    raw["uniprot"] = {
        "found": True,
        "protein_name": "Exportin-1",
        "family": "exportin family",
        "gene_synonyms": ["CRM1"],
        "short_names": ["Exp1"],
        "molecular_weight": 123386,
        "protein_length": 1071,
        "keywords": {"Molecular function": ["RNA-binding"]},
        "function": "Mediates the nuclear export of proteins.",
        "subcellular_location": ["Nucleus"],
        "pfam": [{"id": "PF08767", "name": "CRM1_C"}],
        "url": "https://u",
        "structures": [
            {
                "pdb_id": "1W9C",
                "method": "X-ray",
                "resolution": 2.3,
                "start": 707,
                "end": 1027,
                "url": "https://r",
            }
        ],
    }
    raw["gene_info"] = {
        "found": True,
        "chromosome": "2",
        "band": "2p15",
        "gene_length_bp": 62722,
        "exon_count": 25,
        "summary": "Cell-cycle-regulated gene.",
    }
    raw["alphafold"] = {
        "found": True,
        "mean_plddt": 91.4,
        "fraction_very_high": 0.78,
        "pdb_url": "https://af/model.pdb",
        "url": "https://af",
    }
    raw["hpa_profiles"] = {"found": True, **parse_hpa_xml(HPA_XML), "url": "https://hpa"}
    raw["ot_extras"] = {
        "found": True,
        "chemical_probes": [],
        "target_class": [{"label": "Transporter"}],
        "is_essential": True,
    }
    raw["mutation_landscape"] = [
        {"disease_name": "PMBL", "mutated": 24, "tested": 100, "frequency": 0.24, "disease_id": "X"}
    ]
    raw["competition"] = {
        "found": True,
        **competition.build_landscape(
            [SELINEXOR, ELTANEXOR],
            {
                "SELINEXOR": {
                    "total": 207,
                    "trials": [
                        trial("NCT1", "RECRUITING", conditions=["Chronic Lymphocytic Leukemia"]),
                        trial(
                            "NCT2", "TERMINATED", why="Funding Sponsor no longer supporting study"
                        ),
                    ],
                },
                "ELTANEXOR": None,
            },
            ["chronic lymphocytic leukemia", "CLL"],
        ),
    }
    raw["clinvar"] = {
        "total_variants": 280,
        "classification_counts": {"pathogenic": 3, "uncertain_significance": 210},
        "top_variants": [
            {
                "title": "NM_003400.4(XPO1):c.1A>G",
                "classification": "Pathogenic",
                "conditions": [{"name": "Some condition", "id": None}],
                "review_status": "criteria provided, single submitter",
                "url": "https://c",
            }
        ],
    }
    raw["reactome"] = {
        "pathways": [{"pathway_id": "R-HSA-1", "name": "Nuclear export", "url": "https://re"}]
    }
    raw["string"] = {"interactions": [{"partner": "RAN", "score": 0.999}]}
    raw["europe_pmc"] = {"timeline": {"2020": 300, "2021": 320, "2022": 310}}
    return raw


@pytest.fixture
def app(monkeypatch):
    from frontend import api

    target = f.target(
        "XPO1", uniprot_id="O14980", ensembl_id="ENSG00000082898", hgnc_id="HGNC:12825"
    )
    raw = xpo1_raw()
    assessment = evidence_engine.assess(target, raw).model_dump(mode="json")
    options = {
        "target": target.model_dump(),
        "total_associations": 2,
        "therapeutic_areas": [{"id": "A1", "name": "cancer or benign tumor", "disease_count": 2}],
        "diseases": [
            {
                "id": "MONDO_0004948",
                "name": "B-cell chronic lymphocytic leukemia",
                "score": 0.63,
                "therapeutic_area_ids": ["A1"],
            }
        ],
    }
    config = {"engine_version": "1.0", "config": evidence_engine.CONFIG}
    monkeypatch.setattr(api, "indications", lambda q: options)
    monkeypatch.setattr(api, "search_diseases", lambda q: [])
    monkeypatch.setattr(api, "assess", lambda q, i: {"assessment": assessment, "raw": raw})
    monkeypatch.setattr(api, "synthesis", lambda a: None)
    monkeypatch.setattr(api, "engine_config", lambda: config)
    monkeypatch.setattr(api, "structure_file", lambda url: None)
    return AppTest.from_file("../frontend/app.py", default_timeout=30)


def test_landing_page(app):
    app.run()
    assert not app.exception
    assert any("How to use it" in m.value for m in app.markdown)
    assert any("Designed by Adrien Cosson" in m.value for m in app.markdown)


def test_full_assessment_renders_every_tab(app):
    app.run()
    app.text_input[0].input("XPO1").run()
    app.selectbox[1].select_index(1).run()  # the CLL indication
    next(b for b in app.button if b.label == "Assess").click().run()
    assert not app.exception, app.exception
    titles = " ".join(m.value for m in app.markdown)
    assert "XPO1 in chronic lymphocytic leukemia" in titles
    assert "Who is drugging XPO1" in titles and "Where does every number come from?" in titles
    assert len(app.tabs) == 7
