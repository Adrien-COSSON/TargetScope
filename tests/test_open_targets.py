# tests/test_open_targets.py
"""Tests of the Open Targets service: parsing, cleaning and query orchestration.

The GraphQL transport (_graphql / post_json) is replaced by fakes that answer
according to the query sent, so the whole flow runs without network.
"""

import asyncio

from backend.services import open_targets as ot


def row(name, score=0.5, areas=("cancer or benign tumor",), **datatypes):
    """A raw association row, as returned by the GraphQL API."""
    return {
        "score": score,
        "disease": {"id": name.upper(), "name": name,
                    "therapeuticAreas": [{"id": a, "name": a} for a in areas]},
        "datatypeScores": [{"id": k, "score": v} for k, v in datatypes.items()],
    }


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def test_parse_associations_flattens_and_rounds():
    parsed = ot._parse_associations([row("NSCLC", 0.85123, clinical=0.99567)])
    assert parsed == [{
        "disease_id": "NSCLC", "disease_name": "NSCLC", "score": 0.851,
        "therapeutic_areas": ["cancer or benign tumor"], "datatype_scores": {"clinical": 0.996},
    }]


def test_parse_tractability_keeps_true_buckets_by_modality():
    items = [{"label": "Approved Drug", "modality": "SM", "value": True},
             {"label": "Pocket", "modality": "SM", "value": False},
             {"label": "UniProt loc", "modality": "AB", "value": True}]
    assert ot._parse_tractability(items) == {"Small molecule": ["Approved Drug"],
                                             "Antibody": ["UniProt loc"]}


def test_parse_constraint_keeps_lof_only():
    items = [{"constraintType": "syn", "oe": 1.0},
             {"constraintType": "lof", "oe": 0.2, "oeUpper": 0.3, "oeLower": 0.1}]
    assert ot._parse_constraint(items) == {"oe": 0.2, "loeuf": 0.3, "oe_lower": 0.1}
    assert ot._parse_constraint([]) is None


def test_parse_safety_cleans_and_merges():
    items = [
        {"event": "Diarrhea", "eventId": None, "datasource": "ClinPGx", "effects": []},
        {"event": "diarrhoea", "eventId": "MONDO_1", "datasource": "Brennan",
         "effects": [{"direction": "Inhibition/Decrease", "dosing": None}]},
        {"event": "Skin rash", "eventId": "MONDO_2", "datasource": "Brennan", "effects": []},
        {"event": "rash", "eventId": None, "datasource": "ClinPGx", "effects": []},
        {"event": "drug toxicity", "eventId": None, "datasource": "ClinPGx", "effects": []},
        {"event": "regulation of catalytic activity", "eventId": None, "datasource": "ToxCast"},
    ]
    parsed = {e["event"]: e for e in ot._parse_safety(items)}
    assert set(parsed) == {"diarrhea", "rash"}  # merged, generic and ToxCast dropped
    assert parsed["diarrhea"]["datasources"] == ["Brennan", "ClinPGx"]
    assert parsed["diarrhea"]["event_id"] == "MONDO_1"
    assert parsed["diarrhea"]["effects"] == [{"direction": "Inhibition/Decrease"}]


class TestParseIndication:
    IND = {"id": "CANCER", "name": "cancer", "synonyms": ["neoplasm"]}

    def test_broad_indication_lives_in_sub_diseases(self):
        focus = {"count": 40, "rows": [row("AML", 0.8), row("myeloma", 0.7)]}
        parsed = ot._parse_indication(self.IND, {"indication": {"rows": []}}, focus, [], None)
        assert parsed["has_evidence"] is True
        assert parsed["associated_descendant_count"] == 40
        assert parsed["best_descendant"]["disease_name"] == "AML"
        assert parsed["therapeutic_areas"] == ["cancer or benign tumor"]

    def test_no_evidence_at_all(self):
        parsed = ot._parse_indication(self.IND, {"indication": {"rows": []}},
                                      {"count": 0, "rows": []}, [], None)
        assert parsed["has_evidence"] is False

    def test_failed_focus_is_unknown_not_false(self):
        parsed = ot._parse_indication(self.IND, {"indication": {"rows": []}}, None, [], None)
        assert parsed["has_evidence"] is None

    def test_carries_synonyms_fallback_and_mutation(self):
        mutation = {"frequency": 0.7}
        parsed = ot._parse_indication(self.IND, {"indication": {"rows": [row("CANCER", 0.4)]}},
                                      {"count": 1, "rows": [row("CANCER", 0.4)]},
                                      [{"disease_name": "parent"}], mutation)
        assert parsed["direct_score"] == 0.4
        assert parsed["associated_descendant_count"] == 0  # itself is not a descendant
        assert parsed["synonyms"] == ["neoplasm"]
        assert parsed["fallback_associations"] == [{"disease_name": "parent"}]
        assert parsed["mutation_frequency"] is mutation


# ---------------------------------------------------------------------------
# GraphQL transport
# ---------------------------------------------------------------------------
def test_graphql_errors_become_none(monkeypatch):
    async def fake_post(url, json_body, source=""):
        return {"errors": [{"message": "Cannot query field 'x'"}]}

    monkeypatch.setattr(ot, "post_json", fake_post)
    assert asyncio.run(ot._graphql("query", {})) is None


def test_graphql_failure_becomes_none(monkeypatch):
    async def fake_post(url, json_body, source=""):
        return None

    monkeypatch.setattr(ot, "post_json", fake_post)
    assert asyncio.run(ot._graphql("query", {})) is None


# ---------------------------------------------------------------------------
# Focus chunks and mutation frequency
# ---------------------------------------------------------------------------
def test_focus_chunks_are_merged_and_resorted(monkeypatch):
    """A first version truncated ids and lost CLL/DLBCL: no id may be dropped."""
    scores = {"X": 0.1, "AML": 0.43, "MM": 0.64, "CLL": 0.63, "DLBCL": 0.60}

    async def fake_graphql(query, variables):
        rows = sorted((row(i, scores[i]) for i in variables["ids"] if i in scores),
                      key=lambda r: -r["score"])
        return {"target": {"associatedDiseases": {"count": len(rows), "rows": rows}}}

    monkeypatch.setattr(ot, "_graphql", fake_graphql)
    monkeypatch.setattr(ot, "FOCUS_CHUNK", 2)
    focus = asyncio.run(ot._fetch_focus("ENSG", list(scores)))
    assert focus["count"] == 5
    assert [r["disease"]["name"] for r in focus["rows"]][:3] == ["MM", "CLL", "DLBCL"]


def test_focus_failure_of_one_chunk_fails_the_focus(monkeypatch):
    async def fake_graphql(query, variables):
        return None if "CLL" in variables["ids"] else {"target": {"associatedDiseases": {"rows": []}}}

    monkeypatch.setattr(ot, "_graphql", fake_graphql)
    monkeypatch.setattr(ot, "FOCUS_CHUNK", 1)
    assert asyncio.run(ot._fetch_focus("ENSG", ["AML", "CLL"])) is None


def test_mutation_frequency(monkeypatch):
    async def fake_graphql(query, variables):
        samples = [{"numberSamplesTested": 9331, "numberMutatedSamples": 6582}]
        return {"disease": {"name": "PDAC", "evidences": {"rows": [{"mutatedSamples": samples}]}}}

    monkeypatch.setattr(ot, "_graphql", fake_graphql)
    m = asyncio.run(ot._mutation_frequency("ENSG", "PDAC"))
    assert (m["mutated"], m["tested"], m["frequency"]) == (6582, 9331, 0.705)


def test_mutation_frequency_no_data_vs_failure(monkeypatch):
    async def empty(query, variables):
        return {"disease": {"name": "X", "evidences": {"rows": []}}}

    async def failed(query, variables):
        return None

    monkeypatch.setattr(ot, "_graphql", empty)
    assert asyncio.run(ot._mutation_frequency("ENSG", "X")) is False
    monkeypatch.setattr(ot, "_graphql", failed)
    assert asyncio.run(ot._mutation_frequency("ENSG", "X")) is None


# ---------------------------------------------------------------------------
# Full flow
# ---------------------------------------------------------------------------
def fake_api(monkeypatch, target_found=True):
    """Fake _graphql answering each query of fetch_open_targets()."""
    async def fake_graphql(query, variables):
        if query is ot.DISEASE_QUERY:
            return {"disease": {
                "id": "PDAC", "name": "pancreatic ductal adenocarcinoma", "descendants": ["SUB"],
                "synonyms": [{"relation": "hasExactSynonym", "terms": ["PDAC"]}],
                "parents": [{"id": "PAAD", "name": "pancreatic adenocarcinoma",
                             "dbXRefs": ["ONCOTREE:PAAD"]},
                            {"id": "TUB", "name": "tubular adenocarcinoma", "dbXRefs": ["DOID:1"]}],
            }}
        if query is ot.FOCUS_QUERY:
            ids = variables["ids"]
            rows = [row("pancreatic adenocarcinoma", 0.6, somatic_mutation=0.8)] if ids == ["PAAD"] \
                else [row("PDAC", 0.4, literature=0.99)]
            return {"target": {"associatedDiseases": {"count": len(rows), "rows": rows}}}
        if query is ot.SOMATIC_QUERY:
            return {"disease": {"name": "PDAC", "evidences": {"rows": [{"mutatedSamples": [
                {"numberSamplesTested": 100, "numberMutatedSamples": 70}]}]}}}
        if query is ot.QUERY:
            if not target_found:
                return {"target": None}
            return {"target": {
                "associatedDiseases": {"count": 1, "rows": [row("NSCLC", 0.8, clinical=0.9)]},
                "indication": {"rows": [row("PDAC", 0.4, literature=0.99)]} if variables["hasFocus"] else None,
                "tractability": [], "geneticConstraint": [], "safetyLiabilities": [],
            }}
        raise AssertionError("unexpected query")

    monkeypatch.setattr(ot, "_graphql", fake_graphql)


def test_fetch_with_indication(monkeypatch):
    fake_api(monkeypatch)
    r = asyncio.run(ot.fetch_open_targets("ENSG", "PDAC"))
    ind = r["indication"]
    assert r["found"] is True
    assert ind["synonyms"] == ["PDAC"]
    # only the OncoTree parent is used as fallback, not "tubular adenocarcinoma"
    assert [a["disease_name"] for a in ind["fallback_associations"]] == ["pancreatic adenocarcinoma"]
    assert ind["mutation_frequency"]["frequency"] == 0.7


def test_fetch_without_indication(monkeypatch):
    fake_api(monkeypatch)
    r = asyncio.run(ot.fetch_open_targets("ENSG"))
    assert r["indication"] is None and r["focus_associations"] is None
    assert r["association_count"] == 1


def test_unknown_target_and_missing_id(monkeypatch):
    fake_api(monkeypatch, target_found=False)
    assert asyncio.run(ot.fetch_open_targets("ENSG"))["found"] is False
    assert asyncio.run(ot.fetch_open_targets(None))["found"] is None


def test_disease_options_group_by_area(monkeypatch):
    async def fake_graphql(query, variables):
        rows = [row("CLL", 0.7), row("myeloma", 0.6, areas=("cancer or benign tumor", "immune")),
                row("RA", 0.2, areas=("immune",))]
        return {"target": {"associatedDiseases": {"count": 513, "rows": rows}}}

    monkeypatch.setattr(ot, "_graphql", fake_graphql)
    options = asyncio.run(ot.fetch_disease_options("ENSG"))
    assert options["total_associations"] == 513
    assert [(a["name"], a["disease_count"]) for a in options["therapeutic_areas"]] == \
        [("cancer or benign tumor", 2), ("immune", 2)]


def test_search_keeps_diseases_only(monkeypatch):
    async def fake_graphql(query, variables):
        return {"search": {"hits": [{"id": "D", "name": "d", "entity": "disease"},
                                    {"id": "T", "name": "t", "entity": "target"}]}}

    monkeypatch.setattr(ot, "_graphql", fake_graphql)
    assert asyncio.run(ot.search_diseases("leuk")) == [{"id": "D", "name": "d"}]
    assert asyncio.run(ot.search_diseases("   ")) == []
