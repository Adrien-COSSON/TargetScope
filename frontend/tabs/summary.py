# frontend/tabs/summary.py
"""Summary tab: AI synthesis (with "Why?"), evidence gaps, safety signals."""

import streamlit as st

from frontend.components import esc, severity_pill
from frontend.content import DIMENSION_TITLES

SECTION_COLORS = {"Strengths": "#1F6B45", "Risks": "#9A3412", "Open questions": "#4A5260"}


def _claims(title: str, claims: list[dict]) -> None:
    if not claims:
        return
    st.markdown(
        f'<h4 style="color:{SECTION_COLORS[title]};text-transform:uppercase;'
        f'letter-spacing:0.06em;font-size:0.85rem;margin:18px 0 4px">{title}</h4>',
        unsafe_allow_html=True,
    )
    for claim in claims:
        ids = " ".join(f"[{e['id']}]" for e in claim["evidence"])
        st.markdown(
            f"{claim['text']} <span style='color:#1D4E89;font-family:IBM Plex Mono,monospace;"
            f"font-size:0.8rem'>{esc(ids)}</span>",
            unsafe_allow_html=True,
        )
        with st.expander("Why?"):
            for e in claim["evidence"]:
                where = (
                    f" · {DIMENSION_TITLES.get(e.get('dimension'), e.get('dimension'))}"
                    if e.get("dimension")
                    else ""
                )
                st.markdown(f"**{e['id']}** ({e['kind']}{where}) · {e['text']}")


def render(assessment: dict, synthesis: dict | None, synthesis_error: str | None) -> None:
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("## Summary")
        if synthesis:
            _claims("Strengths", synthesis["strengths"])
            _claims("Risks", synthesis["risks"])
            _claims("Open questions", synthesis["open_questions"])
            note = (
                f" {synthesis['dropped_claims']} uncited claim(s) were removed."
                if synthesis.get("dropped_claims")
                else ""
            )
            st.caption(f"{synthesis['disclaimer']} Written by {synthesis['model']}.{note}")
        else:
            st.info(
                synthesis_error
                or "AI summary unavailable: add ANTHROPIC_API_KEY to the backend .env. "
                "Every score, gap and signal below is computed without it."
            )

    with right:
        st.markdown("#### Evidence gaps")
        gaps = assessment["gaps"]
        if not gaps:
            st.markdown(
                '<p class="ts-small">No evidence gap identified among the sources that answered.</p>',
                unsafe_allow_html=True,
            )
        for gap in gaps:
            st.markdown(
                f"{severity_pill(gap['severity'])} **{esc(gap['description'])}**",
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<p class="ts-small">{esc(gap["why_it_matters"])}</p>', unsafe_allow_html=True
            )

        st.markdown("#### Potential safety considerations")
        signals = assessment["safety_signals"]
        if not signals:
            st.markdown(
                '<p class="ts-small">No signal in the sources that answered. '
                "Absence of a signal never means the target is safe.</p>",
                unsafe_allow_html=True,
            )
        for s in signals:
            st.markdown(f"**{esc(s['statement'])}**")
            caveat = f" · {s['caveat']}" if s.get("caveat") else ""
            st.markdown(
                f'<p class="ts-small">{esc(s["source"])}{esc(caveat)}</p>', unsafe_allow_html=True
            )
