# frontend/tabs/biology.py
"""Biology tab: function, publication trend, pathways, interaction partners."""

import plotly.graph_objects as go
import streamlit as st

from frontend.components import GRID, NAVY, esc, fmt_int, hbar, reading, show_chart, source_footer
from frontend.content import TAB_INTROS

TREND_READING = {
    "Emerging": "Publications are taking off from a low base: a young field, little de-risked "
    "but with room for a first mover.",
    "Growing": "Interest is rising: the biology is getting validated, and competition is likely to follow.",
    "Stable": "A mature, steadily studied target: fewer biological surprises, crowded field.",
    "Declining": "Interest is falling: check whether disappointing results explain it.",
    "Unknown": "Trend not computable (too few publications or a source failed).",
}


def _timeline(timeline: dict) -> go.Figure:
    years = sorted(y for y, v in timeline.items() if v is not None)
    fig = go.Figure(
        go.Bar(
            x=years,
            y=[timeline[y] for y in years],
            marker_color=NAVY,
            hovertemplate="%{x}: %{y} publications<extra></extra>",
        )
    )
    fig.update_layout(
        height=260,
        margin={"l": 10, "r": 10, "t": 10, "b": 30},
        yaxis={"title": "publications per year", "gridcolor": GRID},
        xaxis={"type": "category", "nticks": 12},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def render(assessment: dict, raw: dict) -> None:
    uni = raw.get("uniprot") or {}
    reactome = raw.get("reactome") or {}
    string = raw.get("string") or {}
    lit = assessment.get("literature") or {}
    target = assessment["target"]["gene_symbol"]

    st.markdown(f"## How well is {esc(target)} understood?")
    st.markdown(TAB_INTROS["biology"])

    if uni.get("function"):
        st.markdown("#### Function")
        st.markdown(uni["function"])
        if uni.get("pfam"):
            st.markdown(
                '<p class="ts-small"><b>Domains (Pfam)</b> · '
                + esc(", ".join(d["name"] or d["id"] for d in uni["pfam"]))
                + "</p>",
                unsafe_allow_html=True,
            )

    st.markdown("#### Figure 1 · Publications per year")
    st.markdown(
        '<p class="ts-small">Articles mentioning the gene in their title or abstract (Europe PMC). '
        "The current year is incomplete.</p>",
        unsafe_allow_html=True,
    )
    timeline = lit.get("timeline") or {}
    if timeline:
        show_chart(_timeline(timeline))
    trend = lit.get("trend", "Unknown")
    reading(
        f"<b>{trend}</b> · {TREND_READING.get(trend, '')} PubMed: {fmt_int(lit.get('total_count'))} "
        f"publications in total, {fmt_int(lit.get('recent_count'))} in the last five years."
    )

    left, right = st.columns(2, gap="large")
    with left:
        pathways = reactome.get("pathways")
        st.markdown(f"#### Pathways (Reactome) · {len(pathways) if pathways is not None else '—'}")
        if pathways is None:
            st.warning("Reactome could not be reached.")
        elif pathways:
            st.dataframe(
                [{"Pathway": p["name"], "Link": p["url"]} for p in pathways],
                column_config={
                    "Link": st.column_config.LinkColumn("Reactome", display_text="open")
                },
                hide_index=True,
                use_container_width=True,
                height=min(36 * (len(pathways) + 1) + 4, 360),
            )
        else:
            st.markdown('<p class="ts-small">No curated pathway.</p>', unsafe_allow_html=True)
    with right:
        partners = string.get("interactions")
        st.markdown("#### Figure 2 · Main interaction partners (STRING)")
        if partners is None:
            st.warning("STRING could not be reached.")
        elif partners:
            top = partners[:15]
            show_chart(
                hbar(
                    [p["partner"] for p in top],
                    [p["score"] for p in top],
                    "combined score",
                    max_x=1.1,
                )
            )
            st.caption(
                "Combined score: confidence that the interaction is real (0 to 1), "
                "from experiments, databases and co-expression."
            )
        else:
            st.markdown('<p class="ts-small">No interaction partner.</p>', unsafe_allow_html=True)

    source_footer(
        [
            ("UniProt", uni.get("url")),
            ("Europe PMC", "https://europepmc.org"),
            ("PubMed", f"https://pubmed.ncbi.nlm.nih.gov/?term={target}"),
            ("Reactome", "https://reactome.org"),
            ("STRING", f"https://string-db.org/network/{assessment['target'].get('uniprot_id')}"),
        ],
        assessment["generated_at"],
    )
