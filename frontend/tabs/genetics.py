# frontend/tabs/genetics.py
"""Genetics tab: evidence by disease and type, mutation frequency, ClinVar."""

import plotly.graph_objects as go
import streamlit as st

from frontend.components import (
    GRID,
    NAVY,
    esc,
    fmt_int,
    hbar,
    help_box,
    show_chart,
    source_footer,
)
from frontend.content import OT_SCORE_HELP, OT_SCORE_RULE, genetics_intro, is_oncology

DATATYPE_LABELS = {
    "somatic_mutation": "Somatic",
    "genetic_association": "Germline",
    "genetic_literature": "Genetic literature",
    "clinical": "Clinical / drugs",
    "known_drug": "Known drug",
    "animal_model": "Animal model",
    "affected_pathway": "Affected pathway",
    "rna_expression": "RNA expression",
    "literature": "Literature",
}
MAX_ROWS = 14

CLINVAR_ORDER = [
    ("pathogenic", "Pathogenic", "#9A3412"),
    ("likely_pathogenic", "Likely pathogenic", "#D97706"),
    ("uncertain_significance", "Uncertain", "#C9C4B9"),
    ("benign", "Benign", "#6E9BCF"),
]


def _association_rows(ot: dict) -> list[tuple[str, dict, bool]]:
    """(disease name, datatype scores, is_indication), indication first."""
    rows, seen = [], set()
    ind = ot.get("indication")
    if ind:
        rows.append(
            (
                ind["disease_name"],
                {
                    "overall": ind.get("direct_score") or 0,
                    **(ind.get("direct_datatype_scores") or {}),
                },
                True,
            )
        )
        seen.add(ind["disease_id"])
        for a in ot.get("focus_associations") or []:
            if a["disease_id"] not in seen:
                rows.append(
                    (a["disease_name"], {"overall": a["score"], **a["datatype_scores"]}, True)
                )
                seen.add(a["disease_id"])
            if len(rows) >= 6:
                break
    for a in ot.get("top_associations") or []:
        if len(rows) >= MAX_ROWS:
            break
        if a["disease_id"] not in seen:
            rows.append((a["disease_name"], {"overall": a["score"], **a["datatype_scores"]}, False))
            seen.add(a["disease_id"])
    return rows


def _heatmap(rows: list[tuple[str, dict, bool]]) -> go.Figure:
    present = [k for k in DATATYPE_LABELS if any(r[1].get(k) for r in rows)]
    columns = ["overall", *present]
    names = [("▶ " if is_ind else "") + name[:45] for name, _, is_ind in rows]
    z = [[r[1].get(c) for c in columns] for r in rows]
    text = [["—" if v is None else f"{v:.2f}" for v in line] for line in z]
    fig = go.Figure(
        go.Heatmap(
            z=[[v if v is not None else 0 for v in line] for line in z],
            x=["Overall", *[DATATYPE_LABELS[c] for c in present]],
            y=names,
            text=text,
            texttemplate="%{text}",
            zmin=0,
            zmax=1,
            xgap=3,
            ygap=3,
            colorscale=[[0, "#F2F0EB"], [0.2, "#D6E2F0"], [0.5, "#6E9BCF"], [1, NAVY]],
            colorbar={"title": "score", "thickness": 10, "len": 0.6},
            hovertemplate="%{y}<br>%{x}: %{text}<extra></extra>",
        )
    )
    fig.update_layout(
        height=60 + 30 * len(rows),
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        yaxis={"autorange": "reversed"},
        xaxis={"side": "top"},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _clinvar(clinvar: dict) -> None:
    counts = clinvar.get("classification_counts") or {}
    total = clinvar.get("total_variants")
    st.markdown("#### Figure 3 · Germline variants in ClinVar")
    st.markdown(
        '<p class="ts-small">Variants submitted for the gene, by clinical classification. '
        "Few pathogenic variants is expected for an essential gene: carriers rarely survive.</p>",
        unsafe_allow_html=True,
    )
    if total is None:
        st.warning("ClinVar could not be reached.")
        return
    fig = go.Figure()
    for key, label, color in CLINVAR_ORDER:
        value = counts.get(key)
        if value:
            fig.add_trace(
                go.Bar(
                    x=[value],
                    y=["variants"],
                    orientation="h",
                    name=f"{label} · {value:,}",
                    marker_color=color,
                    hovertemplate=f"{label}: %{{x}}<extra></extra>",
                )
            )
    fig.update_layout(
        barmode="stack",
        height=120,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        showlegend=True,
        legend={"orientation": "h", "y": -0.4},
        yaxis={"visible": False},
        xaxis={"gridcolor": GRID},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    show_chart(fig)
    st.caption(
        f"{fmt_int(total)} variants in total. Review: ★ one submitter, ★★ several concordant, "
        "★★★ expert panel, ★★★★ practice guideline."
    )
    stars = {
        "practice guideline": "★★★★",
        "reviewed by expert panel": "★★★",
        "criteria provided, multiple submitters, no conflicts": "★★",
        "criteria provided, single submitter": "★",
    }
    variants = clinvar.get("top_variants") or []
    if variants:
        st.dataframe(
            [
                {
                    "Protein change": v.get("protein_change"),
                    "Variant (HGVS)": v.get("title"),
                    "Classification": v.get("classification"),
                    "Condition": ", ".join(c["name"] for c in v.get("conditions") or [])[:80],
                    "Review": stars.get(v.get("review_status"), "—"),
                    "Link": v.get("url"),
                }
                for v in variants
            ],
            column_config={"Link": st.column_config.LinkColumn("ClinVar", display_text="open")},
            hide_index=True,
            use_container_width=True,
        )


def render(assessment: dict, raw: dict) -> None:
    ot = raw.get("open_targets") or {}
    score = next(s for s in assessment["scores"] if s["dimension"] == "human_genetics")
    target = assessment["target"]["gene_symbol"]
    indication = assessment.get("indication")

    left, right = st.columns([3, 2], gap="large")
    with left:
        question = f"Does human genetics support {target}" + (
            f" in {indication['disease_name']}?" if indication else "?"
        )
        st.markdown(f"## {esc(question)}")
        areas = (ot.get("indication") or {}).get("therapeutic_areas")
        st.markdown(genetics_intro(indication and indication["disease_name"], areas))
    with right:
        with st.container(border=True):
            st.markdown('<span class="ts-kicker">Key findings</span>', unsafe_allow_html=True)
            st.markdown(
                f"### {'n/s' if score['score'] is None else format(score['score'], 'g')} · {score['label']}"
            )
            if score.get("explanation"):
                st.markdown(score["explanation"])
            for fact in score.get("facts", [])[:4]:
                st.markdown(f'<p class="ts-small">{esc(fact)}</p>', unsafe_allow_html=True)

    # Figure 1
    st.markdown("#### Figure 1 · Evidence by disease and evidence type")
    st.markdown(
        '<p class="ts-small">Each row is a disease, each column a type of evidence. Read across a row '
        "to see what supports the link; read down a column to see where the genetics is. "
        "Rows marked ▶ are the indication and its sub-diseases.</p>",
        unsafe_allow_html=True,
    )
    help_box(
        f"<b>What a value means</b> · {OT_SCORE_HELP}<br><b>Rule of thumb (TargetScope's)</b> · "
        + " · ".join(f"{a}: {b}" for a, b in OT_SCORE_RULE)
    )
    rows = _association_rows(ot) if ot.get("found") else []
    if rows:
        show_chart(_heatmap(rows))
    else:
        st.warning("Open Targets could not be reached: associations unavailable.")

    # Figure 2
    col2, col3 = st.columns(2, gap="large")
    with col2:
        st.markdown("#### Figure 2 · How often is the gene mutated in cancers?")
        st.markdown(
            '<p class="ts-small">Share of tumour samples carrying <b>any</b> mutation of the gene '
            "(Cancer Gene Census). The dashed line is the 10% threshold for a major driver. "
            "Hotspot versus scattered mutations is not separated yet.</p>",
            unsafe_allow_html=True,
        )
        ind_areas = (ot.get("indication") or {}).get("therapeutic_areas")
        if indication and not is_oncology(ind_areas):
            st.caption(
                f"{indication['disease_name']} is not a cancer: this figure is context, "
                "not evidence for the indication."
            )
        landscape = raw.get("mutation_landscape")
        freq_ind = (ot.get("indication") or {}).get("mutation_frequency")
        items = list(landscape or [])
        if freq_ind and all(i["disease_name"] != freq_ind["disease_name"] for i in items):
            items.append(freq_ind)
        if items:
            items.sort(key=lambda i: -i["frequency"])
            labels = [f"{i['disease_name'][:32]} ({i['mutated']:,}/{i['tested']:,})" for i in items]
            hl = {
                labels[k]
                for k, i in enumerate(items)
                if freq_ind and i["disease_name"] == freq_ind["disease_name"]
            }
            show_chart(
                hbar(
                    labels,
                    [i["frequency"] * 100 for i in items],
                    "% of samples",
                    highlight=hl,
                    threshold=(10, "10%"),
                )
            )
        else:
            st.info(
                "No somatic mutation frequency available (no cancer association with somatic evidence)."
            )
    with col3:
        _clinvar(raw.get("clinvar") or {})

    source_footer(
        [
            (
                "Open Targets",
                ot.get("provenance", {}).get("url")
                if isinstance(ot.get("provenance"), dict)
                else f"https://platform.opentargets.org/target/{assessment['target'].get('ensembl_id')}",
            ),
            ("Cancer Gene Census (COSMIC, via Open Targets)", None),
            ("ClinVar", f"https://www.ncbi.nlm.nih.gov/clinvar/?term={target}[gene]"),
        ],
        assessment["generated_at"],
    )
