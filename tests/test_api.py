# tests/test_api.py
"""Tests of the HTTP API (FastAPI TestClient, services replaced by fakes)."""

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import assessment as assessment_router
from backend.routers import resolver as resolver_router
from backend.routers import synthesis as synthesis_router
from backend.services import evidence_engine
from backend.services.resolver import ResolutionUnavailableError
from tests import factories as f

client = TestClient(app)


def raising(exc):
    async def fake(*args, **kwargs):
        raise exc
    return fake


def returning(value):
    async def fake(*args, **kwargs):
        return value
    return fake


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_resolve_ok_and_errors(monkeypatch):
    monkeypatch.setattr(resolver_router, "resolve_target", returning(f.target("EGFR")))
    assert client.post("/api/resolve", json={"query": "egfr"}).json()["gene_symbol"] == "EGFR"

    monkeypatch.setattr(resolver_router, "resolve_target", raising(ValueError("no match")))
    assert client.post("/api/resolve", json={"query": "zzz"}).status_code == 404

    monkeypatch.setattr(resolver_router, "resolve_target",
                        raising(ResolutionUnavailableError("down")))
    assert client.post("/api/resolve", json={"query": "egfr"}).status_code == 503


def test_assess_returns_assessment_and_raw(monkeypatch):
    results = {"chembl": f.chembl([f.drug("ERLOTINIB", 4.0)])}
    assessment = evidence_engine.assess(f.target("EGFR"), results)
    monkeypatch.setattr(assessment_router, "run_assessment", returning((assessment, results)))

    body = client.post("/api/assess", json={"query": "EGFR", "indication_id": "MONDO_1"}).json()
    assert body["assessment"]["target"]["gene_symbol"] == "EGFR"
    assert [s["dimension"] for s in body["assessment"]["scores"]] == evidence_engine.DIMENSIONS
    assert body["raw"]["chembl"]["drugs"][0]["name"] == "ERLOTINIB"


@pytest.mark.parametrize("exc, status", [(ValueError("x"), 404),
                                         (ResolutionUnavailableError("x"), 503)])
def test_assess_errors(monkeypatch, exc, status):
    monkeypatch.setattr(assessment_router, "run_assessment", raising(exc))
    assert client.post("/api/assess", json={"query": "x"}).status_code == status


def test_indications(monkeypatch):
    monkeypatch.setattr(assessment_router, "resolve_target",
                        returning(f.target("XPO1", ensembl_id="ENSG1")))
    monkeypatch.setattr(assessment_router, "fetch_disease_options", returning(
        {"therapeutic_areas": [{"id": "C", "name": "cancer", "disease_count": 1}],
         "diseases": [{"id": "CLL", "name": "CLL", "score": 0.6, "therapeutic_area_ids": ["C"]}],
         "total_associations": 513}))
    body = client.get("/api/targets/XPO1/indications").json()
    assert body["target"]["gene_symbol"] == "XPO1" and body["total_associations"] == 513

    monkeypatch.setattr(assessment_router, "fetch_disease_options", returning(None))
    assert client.get("/api/targets/XPO1/indications").status_code == 503


def test_disease_search(monkeypatch):
    monkeypatch.setattr(assessment_router, "search_diseases",
                        returning([{"id": "MONDO_1", "name": "CLL"}]))
    assert client.get("/api/diseases/search", params={"q": "chronic"}).json()[0]["id"] == "MONDO_1"
    assert client.get("/api/diseases/search", params={"q": "c"}).status_code == 422  # too short


def test_synthesis_null_without_key(monkeypatch):
    assessment = evidence_engine.assess(f.target(), {})
    monkeypatch.setattr(synthesis_router, "synthesize", returning(None))
    r = client.post("/api/synthesis", json={"assessment": assessment.model_dump()})
    assert r.status_code == 200 and r.json() is None
