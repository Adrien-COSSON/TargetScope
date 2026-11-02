# tests/test_synthesis.py
"""Tests of the Claude synthesis: dossier, grounding check, API handling.

The Anthropic client is replaced by a fake: no network, no cost.
"""

import asyncio
from types import SimpleNamespace

from backend.schemas.synthesis import ClaimDraft, SynthesisDraft
from backend.services import evidence_engine, synthesis
from tests import factories as f
from tests.test_evidence_engine import kras_pdac_results


def kras_assessment():
    return evidence_engine.assess(f.target("KRAS"), kras_pdac_results())


class FakeClient:
    """Mimics AsyncAnthropic().messages.parse and records the request."""

    def __init__(self, draft=None, stop_reason="end_turn"):
        self.request = None
        self._draft, self._stop = draft, stop_reason
        self.messages = SimpleNamespace(parse=self._parse)

    async def _parse(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(parsed_output=self._draft, stop_reason=self._stop)


def test_dossier_numbers_every_fact_gap_and_signal():
    a = kras_assessment()
    text, index = synthesis.build_dossier(a)
    n_facts = sum(len(s.facts) for s in a.scores)
    assert sum(1 for k in index if k.startswith("F")) == n_facts
    assert sum(1 for k in index if k.startswith("S")) == len(a.safety_signals)
    assert "[F1]" in text and "TARGET: KRAS" in text
    assert "pancreatic ductal adenocarcinoma" in text


def test_dossier_flags_unavailable_dimensions_and_sources():
    a = evidence_engine.assess(f.target(), {"open_targets": {"found": None}})
    text, _ = synthesis.build_dossier(a)
    assert "UNAVAILABLE" in text
    assert "evidence unknown, not absent" in text


def test_grounding_drops_uncited_and_invented_citations():
    _, index = synthesis.build_dossier(kras_assessment())
    draft = SynthesisDraft(
        headline="KRAS is a validated driver.",
        strengths=[ClaimDraft(text="Mutated in 70% of PDAC.", evidence_ids=["f1", "F1"]),
                   ClaimDraft(text="Invented claim.", evidence_ids=["F999"]),
                   ClaimDraft(text="Uncited claim.", evidence_ids=[])],
        risks=[ClaimDraft(text="Essential gene.", evidence_ids=["S1"])],
        open_questions=[],
    )
    s = synthesis.ground(draft, index, "test-model")
    assert [c.text for c in s.strengths] == ["Mutated in 70% of PDAC."]
    assert [e.id for e in s.strengths[0].evidence] == ["F1"]  # normalised, de-duplicated
    assert s.risks[0].evidence[0].kind == "safety"
    assert s.dropped_claims == 2


def test_no_key_means_no_synthesis(monkeypatch):
    monkeypatch.setattr(synthesis.settings, "anthropic_api_key", None)
    assert asyncio.run(synthesis.synthesize(kras_assessment())) is None


def test_full_call_with_fake_client():
    draft = SynthesisDraft(headline="h", strengths=[ClaimDraft(text="t", evidence_ids=["F1"])],
                           risks=[], open_questions=[])
    client = FakeClient(draft)
    s = asyncio.run(synthesis.synthesize(kras_assessment(), client=client))
    assert s.strengths[0].evidence[0].id == "F1"
    assert client.request["output_format"] is SynthesisDraft  # structured output requested
    assert "DOSSIER" in client.request["messages"][0]["content"]
    assert "ONLY the dossier" in client.request["system"]


def test_refusal_or_truncation_gives_none():
    draft = SynthesisDraft(headline="h", strengths=[], risks=[], open_questions=[])
    for stop in ("refusal", "max_tokens"):
        assert asyncio.run(synthesis.synthesize(kras_assessment(),
                                                client=FakeClient(draft, stop))) is None
