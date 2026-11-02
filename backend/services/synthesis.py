# backend/services/synthesis.py
"""Claude-written synthesis of a TargetAssessment, grounded in its facts.

Claude is used for text generation only (no RAG, no agent): the engine has
already decided everything, Claude turns it into a project-team summary.

Anti-hallucination design:
  1. build_dossier() numbers every fact (F1...), gap (G1...) and safety
     signal (S1...) of the assessment.
  2. Claude receives only the dossier and must cite ids for every claim.
     The answer shape is enforced by structured outputs (JSON schema).
  3. ground() drops any claim that cites nothing or cites an unknown id.
  4. Every remaining claim carries its resolved evidence: that is "Why?".

Without an API key, or if the API fails, synthesize() returns None and the
assessment stays complete without the summary.
"""

import logging

from anthropic import APIError, AsyncAnthropic

from backend.config import settings
from backend.schemas.evidence import TargetAssessment
from backend.schemas.synthesis import (
    Claim,
    ClaimDraft,
    EvidenceRef,
    SynthesisDraft,
    TargetSynthesis,
)

logger = logging.getLogger(__name__)

MAX_TOKENS = 2000  # the synthesis is short; this bounds cost and latency

SYSTEM_PROMPT = """You are a senior drug-discovery project leader writing the \
summary of a target assessment for a project team meeting.

Rules (strict):
- Use ONLY the dossier you are given. Add no fact, number, drug, disease or \
reference that is not in it, even if you know it.
- Every claim must cite the dossier ids it relies on (e.g. ["F3", "S1"]). A \
claim you cannot support with ids must not be written.
- Safety: always write "potential safety consideration", never conclude that \
the target is unsafe or safe. No safety signal does not mean safe.
- Scores are relative evidence levels, not probabilities of success.
- A dimension marked UNAVAILABLE means a data source failed: say the evidence \
is unknown, never that it is absent.
- Be concise and specific: one sentence per claim, 2-5 claims per section, \
no generic statements that would fit any target.
- Write in English."""


# ---------------------------------------------------------------------------
# Dossier
# ---------------------------------------------------------------------------
def build_dossier(assessment: TargetAssessment) -> tuple[str, dict[str, EvidenceRef]]:
    """Turn an assessment into numbered text for Claude, plus the id index.

    Pure function. The index maps every id to its EvidenceRef so citations can
    be checked (ground) and shown to the user ("Why?").
    """
    index: dict[str, EvidenceRef] = {}
    lines: list[str] = []
    counters = {"F": 0, "G": 0, "S": 0, "C": 0}

    def add(prefix: str, kind: str, text: str, dimension: str | None = None,
            sources: list[str] | None = None) -> str:
        counters[prefix] += 1
        ref_id = f"{prefix}{counters[prefix]}"
        index[ref_id] = EvidenceRef(id=ref_id, kind=kind, dimension=dimension,
                                    text=text, sources=sources or [])
        return ref_id

    t = assessment.target
    lines.append(f"TARGET: {t.gene_symbol} ({t.name})")
    if assessment.indication:
        ind = assessment.indication
        cid = add("C", "context",
                  f"Indication: {ind.disease_name}; direct association score "
                  f"{ind.direct_score}; {ind.associated_descendant_count} associated "
                  f"sub-diseases" + (f", best: {ind.best_descendant}" if ind.best_descendant else ""))
        lines.append(f"[{cid}] {index[cid].text}")
    else:
        lines.append("INDICATION: none chosen (target assessed across all diseases)")

    lit = assessment.literature
    cid = add("C", "context", f"Literature: {lit.total_count} publications (PubMed), "
                              f"{lit.recent_count} in the last 5 years, trend {lit.trend}",
              dimension="biological_knowledge", sources=["PubMed", "Europe PMC"])
    lines.append(f"[{cid}] {index[cid].text}")

    lines.append("\nDIMENSIONS (score 0-1, label, explanation, facts):")
    for s in assessment.scores:
        score = "UNAVAILABLE" if s.score is None else f"{s.score:.2f} {s.label}"
        lines.append(f"\n## {s.dimension}: {score}. {s.explanation or ''}")
        for fact in s.facts:
            fid = add("F", "fact", fact, dimension=s.dimension, sources=s.sources)
            lines.append(f"[{fid}] {fact}")

    lines.append("\nEVIDENCE GAPS:")
    for g in assessment.gaps:
        gid = add("G", "gap", f"[{g.severity}] {g.description}. {g.why_it_matters}",
                  dimension=g.dimension)
        lines.append(f"[{gid}] {index[gid].text}")
    if not assessment.gaps:
        lines.append("(none)")

    lines.append("\nPOTENTIAL SAFETY CONSIDERATIONS:")
    for sig in assessment.safety_signals:
        text = sig.statement + (f" Caveat: {sig.caveat}" if sig.caveat else "")
        sid = add("S", "safety", text, sources=[sig.source])
        lines.append(f"[{sid}] {text} ({sig.source})")
    if not assessment.safety_signals:
        lines.append("(none reported; this does not mean the target is safe)")

    down = [s.source for s in assessment.sources if s.status == "unavailable"]
    if down:
        lines.append(f"\nUNAVAILABLE SOURCES (evidence unknown, not absent): {', '.join(down)}")

    return "\n".join(lines), index


# ---------------------------------------------------------------------------
# Grounding check
# ---------------------------------------------------------------------------
def ground(draft: SynthesisDraft, index: dict[str, EvidenceRef], model: str) -> TargetSynthesis:
    """Keep only claims whose citations all exist; resolve them for "Why?".

    A claim citing nothing, or citing one unknown id, is dropped entirely:
    a partly invented citation makes the whole sentence untrustworthy.
    """
    dropped = 0

    def keep(claims: list[ClaimDraft]) -> list[Claim]:
        nonlocal dropped
        kept = []
        for c in claims:
            # ids are normalised: Claude may write "f3" or " F3 "
            ids = [i.strip().upper() for i in c.evidence_ids]
            if not ids or any(i not in index for i in ids):
                dropped += 1
                logger.warning("Synthesis: claim dropped (citations %s): %s", c.evidence_ids, c.text)
                continue
            # dict.fromkeys removes duplicate ids while keeping their order
            kept.append(Claim(text=c.text, evidence=[index[i] for i in dict.fromkeys(ids)]))
        return kept

    # keep() runs first (it counts the dropped claims), then the model is built
    strengths = keep(draft.strengths)
    risks = keep(draft.risks)
    questions = keep(draft.open_questions)
    return TargetSynthesis(headline=draft.headline, strengths=strengths, risks=risks,
                           open_questions=questions, model=model, dropped_claims=dropped)


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------
async def synthesize(
    assessment: TargetAssessment,
    api_key: str | None = None,
    client: AsyncAnthropic | None = None,
) -> TargetSynthesis | None:
    """Write the grounded synthesis of an assessment.

    Args:
        assessment: output of evidence_engine.assess().
        api_key: per-request key (bring your own key, e.g. on a public demo);
            defaults to ANTHROPIC_API_KEY from .env.
        client: injected client, for tests.

    Returns:
        TargetSynthesis, or None when no key is configured or the call failed.
    """
    key = api_key or settings.anthropic_api_key
    if client is None:
        if not key:
            logger.info("Synthesis: no Anthropic API key, summary skipped")
            return None
        client = AsyncAnthropic(api_key=key)

    dossier, index = build_dossier(assessment)
    model = settings.anthropic_model
    logger.info("Synthesis: started for %s (%d dossier entries, model %s)",
                assessment.target.gene_symbol, len(index), model)

    try:
        # messages.parse: the API constrains the answer to SynthesisDraft's
        # JSON schema, and the SDK validates it into the Pydantic model
        response = await client.messages.parse(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"DOSSIER\n\n{dossier}\n\nWrite the synthesis."}],
            output_format=SynthesisDraft,
        )
    except APIError as e:
        # The API message says what is wrong (credits, parameter, schema...).
        # It never contains the API key, so it is safe to log.
        logger.error("Synthesis: Anthropic API error %s (status %s): %s",
                     type(e).__name__, getattr(e, "status_code", "?"), getattr(e, "message", e))
        return None

    # refusal / max_tokens: the output may not match the schema or be incomplete
    if response.stop_reason in ("refusal", "max_tokens") or response.parsed_output is None:
        logger.error("Synthesis: unusable answer (stop_reason=%s)", response.stop_reason)
        return None

    synthesis = ground(response.parsed_output, index, model)
    logger.info("Synthesis: done for %s (%d claims dropped by the grounding check)",
                assessment.target.gene_symbol, synthesis.dropped_claims)
    return synthesis
