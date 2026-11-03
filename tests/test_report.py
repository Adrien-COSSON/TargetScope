# tests/test_report.py
"""PDF report: builds for a full assessment, without AI summary, and with
every source down (the report must never crash on missing data)."""

import pytest

from backend.services import evidence_engine
from frontend.report import build_pdf, file_name
from tests import factories as f
from tests.test_frontend import xpo1_raw

CONFIG = {"safety": evidence_engine.CONFIG.get("safety", {})}
SYNTHESIS = {
    "headline": "Clinically validated target, narrow window.",
    "strengths": [
        {
            "text": "Somatic driver.",
            "evidence": [{"id": "F1", "kind": "fact", "text": "4.7% of CLL"}],
        }
    ],
    "risks": [],
    "open_questions": [],
    "model": "test-model",
    "disclaimer": "AI-written summary.",
}


@pytest.fixture
def target():
    return f.target("XPO1", uniprot_id="O14980", ensembl_id="ENSG00000082898", hgnc_id="HGNC:12825")


def _assess(target, raw):
    return evidence_engine.assess(target, raw).model_dump(mode="json")


def test_full_report_is_a_pdf(target):
    raw = xpo1_raw()
    pdf = build_pdf(_assess(target, raw), raw, SYNTHESIS, CONFIG)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 20_000  # figures embedded


def test_report_without_ai_summary(target):
    raw = xpo1_raw()
    assert build_pdf(_assess(target, raw), raw, None, CONFIG).startswith(b"%PDF")


def test_report_with_every_source_down(target):
    raw = {name: None for name in xpo1_raw()}
    assert build_pdf(_assess(target, raw), raw, None, {}).startswith(b"%PDF")


def test_file_name_is_safe(target):
    raw = xpo1_raw()
    name = file_name(_assess(target, raw))
    assert name.startswith("TargetScope_XPO1") and name.endswith(".pdf")
    assert " " not in name and "/" not in name


# ---------------------------------------------------------------------------
# Genetics intro by indication type, links and publications
# ---------------------------------------------------------------------------
from frontend.content import genetics_intro  # noqa: E402
from frontend.links import cited_publications, key_links  # noqa: E402


def test_genetics_intro_depends_on_indication():
    cancer = genetics_intro("chronic lymphocytic leukemia", ["cancer or benign tumor"])
    asthma = genetics_intro("asthma", ["respiratory or thoracic disease"])
    alone = genetics_intro(None, None)
    assert "somatic" in cancer and "a cancer" in cancer
    assert "germline" in asthma and "do not apply" in asthma
    assert "Without an indication" in alone


def test_key_links_use_resolved_ids(target):
    raw = xpo1_raw()
    links = {x["resource"]: x["url"] for x in key_links(_assess(target, raw), raw)}
    assert links["UniProt"].endswith("/O14980/entry")
    assert links["DepMap"] == "https://depmap.org/portal/gene/XPO1"


def test_cited_publications_dedupe_mgi_references(target):
    raw = xpo1_raw()
    ref = {"id": "MGI:1", "citation": "Doe J (2001)", "url": "https://mgi/1"}
    raw["mgi"]["top_phenotypes"] = [
        {"term": "a", "references": [ref]},
        {"term": "b", "references": [ref]},
    ]
    pubs = [
        p for p in cited_publications(_assess(target, raw), raw) if p["reference"] == "Doe J (2001)"
    ]
    assert len(pubs) == 1 and "a (MGI)" in pubs[0]["supports"] and "b (MGI)" in pubs[0]["supports"]
