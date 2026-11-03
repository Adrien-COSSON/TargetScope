# frontend/tabs/safety.py
"""Models & safety tab: expression profiles (tissues / single cells / immune
cells), genetic constraint, mouse phenotypes, documented liabilities."""

import plotly.graph_objects as go
import streamlit as st

from frontend.components import GRID, RUST, esc, hbar, reading, show_chart, source_footer
from frontend.content import TAB_INTROS

# Same list as the engine's CONFIG["safety"]["critical_tissues"] (substring match)
CRITICAL = ("heart", "liver", "kidney", "brain", "cerebral", "bone marrow", "lung")
TOP_N = 20


def _is_critical(name: str, critical: list[str]) -> bool:
    return any(c in name.lower() for c in critical)


def _expression(profiles: dict, config: dict) -> None:
    st.markdown("#### Figure 1 · Where is the target expressed in a healthy body?")
    if profiles.get("found") is None:
        st.warning("Human Protein Atlas could not be reached: expression profiles unavailable.")
        return
    critical = config.get("safety", {}).get("critical_tissues") or list(CRITICAL)
    view = st.segmented_control(
        "Expression view",
        ["Tissues", "Single cell types", "Immune cells"],
        default="Tissues",
        label_visibility="collapsed",
        key="expr_view",
    )
    if view == "Single cell types":
        sc = profiles.get("single_cells") or {}
        rows = (sc.get("cells") or [])[:TOP_N]
        st.markdown(
            '<p class="ts-small">Which cells express the gene, across all tissues (single-cell '
            f"RNA-seq, top {TOP_N}). Specificity: <b>{esc(sc.get('specificity') or 'n/a')}</b>.</p>",
            unsafe_allow_html=True,
        )
        if rows:
            show_chart(hbar([r["cell"] for r in rows], [r["ncpm"] for r in rows], "nCPM"))
        unit = ("nCPM", "normalized counts per million: the single-cell equivalent of nTPM")
    elif view == "Immune cells":
        rows = profiles.get("immune_cells") or []
        st.markdown(
            '<p class="ts-small">Blood immune cell types, sorted. Useful to compare the cell of '
            "origin of a haematological indication (e.g. B cells for CLL) with other blood cells.</p>",
            unsafe_allow_html=True,
        )
        if rows:
            show_chart(
                hbar(
                    [f"{r['cell']} ({r['lineage']})" for r in rows],
                    [r["ntpm"] for r in rows],
                    "nTPM",
                )
            )
        unit = ("nTPM", "normalized transcripts per million")
    else:
        rows = (profiles.get("tissues") or [])[:TOP_N]
        st.markdown(
            '<p class="ts-small">RNA level per tissue (top ' + str(TOP_N) + "). Bars in rust are "
            "critical tissues for safety: on-target effects there are often dose-limiting.</p>",
            unsafe_allow_html=True,
        )
        if rows:
            colors = [RUST if _is_critical(r["tissue"], critical) else "#6E9BCF" for r in rows]
            show_chart(
                hbar([r["tissue"] for r in rows], [r["ntpm"] for r in rows], "nTPM", colors=colors)
            )
        unit = (
            "nTPM",
            "normalized transcripts per million: the share of the gene's transcripts among all "
            "transcripts of the sample, normalized so tissues and datasets can be compared",
        )
    st.caption(
        f"Unit · {unit[0]}, {unit[1]}. Source · Human Protein Atlas (consensus of HPA and GTEx "
        "RNA-seq for tissues; HPA single-cell and blood atlases)."
    )


def _loeuf(constraint: dict | None, threshold: float) -> None:
    st.markdown("#### Figure 2 · Tolerance to loss in humans")
    st.markdown(
        '<p class="ts-small">LOEUF (gnomAD): how many loss-of-function variants healthy people carry, '
        "relative to what is expected. Low means people cannot tolerate losing one copy.</p>",
        unsafe_allow_html=True,
    )
    loeuf = (constraint or {}).get("loeuf")
    if loeuf is None:
        st.info("No constraint metric available.")
        return
    fig = go.Figure()
    for x0, x1, color in (
        (0, threshold, "#E8B7A6"),
        (threshold, 1.0, "#F1DDB4"),
        (1.0, 2.0, "#D6E2F0"),
    ):
        # layer="below": shapes are drawn above the traces by default and hid the marker
        fig.add_shape(
            type="rect", x0=x0, x1=x1, y0=0, y1=1, fillcolor=color, line_width=0, layer="below"
        )
    fig.add_trace(
        go.Scatter(
            x=[min(loeuf, 2)],
            y=[0.5],
            mode="markers+text",
            text=[f"{loeuf:.2f}"],
            textposition="top center",
            marker={"size": 16, "color": "#1B1F24", "symbol": "diamond"},
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        height=130,
        margin={"l": 40, "r": 40, "t": 10, "b": 30},
        showlegend=False,
        xaxis={
            "range": [0, 2],
            "tickvals": [0, threshold, 1, 2],
            "ticktext": ["0 intolerant", f"{threshold:g}", "1", "2 tolerant"],
            "gridcolor": GRID,
        },
        yaxis={"visible": False, "range": [0, 1.4]},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    show_chart(fig)
    if loeuf < threshold:
        reading(
            "Among the most constrained human genes: full or prolonged inhibition may not be tolerated."
        )
    elif loeuf < 1:
        reading("Moderately constrained: loss of one copy is under some selection.")
    else:
        reading(
            "Tolerant to loss of one copy in healthy people: a reassuring, not conclusive, sign."
        )


def render(assessment: dict, raw: dict, config: dict) -> None:
    profiles = raw.get("hpa_profiles") or {}
    hpa = raw.get("hpa") or {}
    mgi = raw.get("mgi") or {}
    ot = raw.get("open_targets") or {}
    target = assessment["target"]["gene_symbol"]
    animal = next(s for s in assessment["scores"] if s["dimension"] == "animal_models")

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown(f"## What happens when {esc(target)} is lost, and where is it expressed?")
        st.markdown(TAB_INTROS["safety"])
    with right:
        with st.container(border=True):
            st.markdown('<span class="ts-kicker">Key findings</span>', unsafe_allow_html=True)
            for s in assessment["safety_signals"]:
                st.markdown(
                    f'<p class="ts-small"><b style="color:{RUST}">Potential safety consideration</b> · '
                    f"{esc(s['statement'])}</p>",
                    unsafe_allow_html=True,
                )
            st.markdown(
                f'<p class="ts-small">Animal models {animal["label"].lower()} · '
                f"{esc(animal.get('explanation') or '')}</p>",
                unsafe_allow_html=True,
            )
            st.markdown(
                '<p class="ts-small">Absence of a signal never means the target is safe.</p>',
                unsafe_allow_html=True,
            )

    col1, col2 = st.columns([3, 2], gap="large")
    with col1:
        _expression(profiles, config)
    with col2:
        _loeuf(ot.get("genetic_constraint"), config.get("safety", {}).get("loeuf_intolerant", 0.35))
        cancer = hpa.get("cancer_expression") or {}
        if cancer.get("specificity"):
            st.markdown(
                f'<p class="ts-small"><b>Expression across cancers (HPA, TCGA)</b> · '
                f"{esc(cancer['specificity'])}, {esc((cancer.get('distribution') or '').lower())}. "
                "HPA works on median expression per cancer type: biomarker-defined subgroups are "
                "invisible here.</p>",
                unsafe_allow_html=True,
            )

    col3, col4 = st.columns(2, gap="large")
    with col3:
        st.markdown("#### Mouse knockout phenotypes")
        if mgi.get("found") is None:
            st.warning("MGI / Alliance could not be reached.")
        elif not mgi.get("found"):
            st.info("No mouse ortholog found.")
        else:
            gene = (mgi.get("mouse_gene") or {}).get("symbol")
            st.markdown(
                f'<p class="ts-small">Mouse gene <b>{esc(gene)}</b> · {mgi.get("phenotype_count", 0)} '
                "distinct phenotypes. Lethal phenotypes first.</p>",
                unsafe_allow_html=True,
            )
            lethal = mgi.get("lethality_phenotypes") or []
            others = [p for p in mgi.get("top_phenotypes") or [] if p not in lethal]
            st.dataframe(
                [
                    {
                        "Phenotype": p["term"],
                        "Lethal": "Yes" if p in lethal else "",
                        "References": p.get("reference_count", len(p.get("pmids") or [])),
                        "First reference": (p.get("references") or [{}])[0].get("citation"),
                        "Link": (p.get("references") or [{}])[0].get("url"),
                    }
                    for p in [*lethal, *others][:15]
                ],
                column_config={
                    "Link": st.column_config.LinkColumn(
                        "Link",
                        display_text="open",
                        help="PubMed when the paper has a PMID, otherwise the MGI reference page",
                    )
                },
                hide_index=True,
                use_container_width=True,
            )
            models = mgi.get("disease_models") or []
            if models:
                st.markdown(
                    '<p class="ts-small"><b>Mouse models of disease</b> · '
                    + esc(", ".join(f"{m['disease']} ({m['model_count']})" for m in models[:8]))
                    + "</p>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<p class="ts-small"><b>Mouse models of disease</b> · none annotated.</p>',
                    unsafe_allow_html=True,
                )
    with col4:
        st.markdown("#### Documented safety liabilities")
        st.markdown(
            '<p class="ts-small">Adverse events linked to modulating this target, curated by Open Targets '
            "from literature and drug labels. Duplicates merged.</p>",
            unsafe_allow_html=True,
        )
        liabilities = ot.get("safety_liabilities")
        if liabilities:
            st.dataframe(
                [
                    {
                        "Event": e["event"],
                        "Direction": ", ".join(
                            sorted(
                                {
                                    (x.get("direction") or "").split("/")[0]
                                    for x in e.get("effects") or []
                                    if x.get("direction")
                                }
                            )
                        )
                        or "—",
                        "Sources": ", ".join(e.get("datasources") or []),
                    }
                    for e in liabilities
                ],
                hide_index=True,
                use_container_width=True,
            )
        elif liabilities is not None:
            st.markdown('<p class="ts-small">None documented.</p>', unsafe_allow_html=True)

    source_footer(
        [
            ("Human Protein Atlas", profiles.get("url")),
            ("gnomAD (via Open Targets)", None),
            ("MGI / Alliance of Genome Resources", "https://www.alliancegenome.org"),
            ("Open Targets safety", None),
        ],
        assessment["generated_at"],
    )
