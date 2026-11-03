# frontend/components.py
"""Reusable display blocks: page style, identity card, radar, score cards,
"Reading" boxes, bar charts, source footers.

Visual direction (maquette B): warm paper background, navy accent, a serif
for titles (Source Serif 4) over a technical sans (IBM Plex Sans).
"""

import html

import plotly.graph_objects as go
import streamlit as st

from frontend.content import (
    CREDIT,
    DIMENSION_TITLES,
    INTRO,
    RADAR_LABELS,
    READING_SUMMARY,
    WHAT_WE_LOOK_AT,
    score_ladder,
)

NAVY = "#1D4E89"
NAVY_LIGHT = "#A9C3E3"
RUST = "#9A3412"
INK = "#1B1F24"
MUTED = "#4A5260"
GRID = "#E2DFD8"

LABEL_STYLES = {
    "Strong": ("#E6F2EB", "#1F6B45"),
    "Moderate": ("#FBF1DC", "#7A4F00"),
    "Limited": ("#F2F0EB", "#4A5260"),
    "None": ("#F2F0EB", "#4A5260"),
    "Unavailable": ("#E6E2D9", "#3A424D"),
}

SEVERITY_STYLES = {
    "high": ("#FBEDE7", RUST),
    "medium": ("#FBF1DC", "#7A4F00"),
    "low": ("#F2F0EB", MUTED),
}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, p, li, label { font-family: 'IBM Plex Sans', system-ui, sans-serif; }
h1, h2, h3 { font-family: 'Source Serif 4', Georgia, serif !important; letter-spacing: -0.01em; }
.block-container, [data-testid='stMainBlockContainer'] { max-width: 1240px; padding-top: 4.5rem !important; }
.ts-kicker { font-size: 0.8rem; letter-spacing: 0.08em; text-transform: uppercase; color: #4A5260; }
.ts-verdict { font-family: 'Source Serif 4', Georgia, serif; font-size: 1.35rem; line-height: 1.4; color: #2F3640; }
.ts-ids { font-family: 'IBM Plex Mono', monospace; font-size: 0.82rem; color: #4A5260; }
.ts-ids span { margin-right: 1rem; }
.ts-card { background: #FFFFFF; border: 1px solid #E2DFD8; border-radius: 8px; padding: 16px 18px; height: 100%; }
.ts-card.dashed { background: #F5F3EE; border-style: dashed; }
.ts-card h4 { margin: 0; font-family: 'IBM Plex Sans', sans-serif !important; font-size: 1rem; }
.ts-score { font-family: 'IBM Plex Mono', monospace; font-weight: 500; font-size: 1.05rem; }
.ts-pill { font-size: 0.7rem; font-weight: 600; padding: 2px 8px; border-radius: 4px; letter-spacing: 0.04em; }
.ts-bar { height: 6px; background: #EFEBE3; border-radius: 3px; margin: 10px 0; }
.ts-bar > div { height: 6px; background: #1D4E89; border-radius: 3px; }
.ts-small, .stMarkdown p.ts-small { font-size: 0.85rem !important; line-height: 1.5; color: #4A5260; margin: 6px 0; }
.ts-small b { color: #1B1F24; }
.ts-top { position: fixed; right: 28px; bottom: 28px; z-index: 1000; background: #1D4E89;
  color: #fff !important; text-decoration: none !important; font: 600 0.85rem 'IBM Plex Sans', sans-serif;
  padding: 9px 14px; border-radius: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.18); }
.ts-top:hover { background: #163C6A; }
.ts-intro { background: #F3F1EC; border-left: 3px solid #1D4E89; border-radius: 6px;
  padding: 12px 16px; margin: 6px 0 14px; font-size: 0.92rem; line-height: 1.55; color: #2B3038; }
.ts-intro .ts-credit { display: block; margin-top: 6px; font-size: 0.8rem; color: #6B7280; }
.ts-fact { font-size: 0.88rem; line-height: 1.5; background: #F5F3EE; border-radius: 6px; padding: 8px 10px; margin-top: 8px; }
.ts-reading { font-size: 0.92rem; line-height: 1.6; background: #F5F3EE; border-radius: 6px; padding: 10px 14px; margin: 6px 0 14px; }
.ts-help { font-size: 0.85rem; line-height: 1.55; background: #F1F5FA; border-radius: 6px; padding: 10px 14px; margin: 6px 0 10px; }
.ts-idcard { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px 22px; background: #FFFFFF; border: 1px solid #E2DFD8; border-radius: 8px; padding: 14px 18px; }
.ts-idcard div span { display: block; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: #4A5260; }
.ts-idcard div b { font-weight: 500; font-size: 0.95rem; }
.ts-footer { border-top: 1px solid #E2DFD8; margin-top: 18px; padding-top: 10px; font-size: 0.8rem; color: #4A5260; }
.ts-footer a { margin-right: 14px; }
.ts-tag { display: inline-block; font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; margin: 2px 4px 2px 0; background: #F2F0EB; color: #4A5260; }
.ts-tag.on { background: #E6F2EB; color: #1F6B45; }
</style>
"""


def inject_style() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def page_anchor() -> None:
    """Invisible anchor at the very top of the page, target of back_to_top()."""
    st.markdown('<div id="ts-top"></div>', unsafe_allow_html=True)


def back_to_top() -> None:
    """Floating button, bottom right, that brings the reader back to the search bar."""
    st.markdown('<a class="ts-top" href="#ts-top" target="_self">↑ Top</a>', unsafe_allow_html=True)


def intro() -> None:
    st.markdown(
        f'<div class="ts-intro">{INTRO}<span class="ts-credit">{CREDIT}</span></div>',
        unsafe_allow_html=True,
    )


def esc(value) -> str:
    """Escape source text before putting it in HTML (it comes from external APIs)."""
    return html.escape(str(value)) if value is not None else ""


def fmt_int(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "—"


def pill(label: str) -> str:
    bg, fg = LABEL_STYLES.get(label, LABEL_STYLES["None"])
    text = "NOT SCORED" if label == "Unavailable" else label.upper()
    return f'<span class="ts-pill" style="background:{bg};color:{fg}">{text}</span>'


def severity_pill(severity: str) -> str:
    bg, fg = SEVERITY_STYLES.get(severity, SEVERITY_STYLES["low"])
    return f'<span class="ts-pill" style="background:{bg};color:{fg}">{severity.upper()}</span>'


def reading(text: str) -> None:
    """The 'Reading' box under a figure: what the figure means for the decision."""
    st.markdown(f'<div class="ts-reading"><b>Reading</b> · {text}</div>', unsafe_allow_html=True)


def help_box(html_text: str) -> None:
    st.markdown(f'<div class="ts-help">{html_text}</div>', unsafe_allow_html=True)


def source_footer(links: list[tuple[str, str | None]], retrieved: str | None = None) -> None:
    """'Sources' line at the bottom of a tab: [(label, url), ...]."""
    parts = [f'<a href="{esc(u)}" target="_blank">{esc(n)}</a>' if u else esc(n) for n, u in links]
    date = f" · retrieved {esc(retrieved[:10])}" if retrieved else ""
    st.markdown(
        f'<div class="ts-footer"><b>Sources</b> · {" ".join(parts)}{date}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Header and identity card
# ---------------------------------------------------------------------------
def header(assessment: dict, verdict: str | None) -> None:
    target = assessment["target"]
    indication = assessment.get("indication")
    title = (
        f"{target['gene_symbol']} in {indication['disease_name']}"
        if indication
        else target["gene_symbol"]
    )
    ok = sum(s["status"] != "unavailable" for s in assessment["sources"])
    st.markdown(
        f'<div class="ts-kicker">Target assessment · {esc(assessment["generated_at"][:10])} · '
        f"engine v{esc(assessment['engine_version'])} · {ok}/{len(assessment['sources'])} sources answered</div>",
        unsafe_allow_html=True,
    )
    st.markdown(f"# {esc(title)}")
    if verdict:
        st.markdown(f'<div class="ts-verdict">{esc(verdict)}</div>', unsafe_allow_html=True)


def identity_card(target: dict, raw: dict) -> None:
    """Five-second summary of what the target is, under the title."""
    uni = raw.get("uniprot") or {}
    gene = raw.get("gene_info") or {}
    hpa = raw.get("hpa_profiles") or {}
    extras = raw.get("ot_extras") or {}

    mw = uni.get("molecular_weight")
    classes = [c["label"] for c in extras.get("target_class") or []]
    functions = (uni.get("keywords") or {}).get("Molecular function") or []
    location = gene.get("band") and f"chr{gene.get('chromosome')} · {gene['band']}"
    size = gene.get("gene_length_bp")
    cells = [
        ("Protein", uni.get("protein_name") or target.get("name")),
        (
            "Also known as",
            ", ".join((uni.get("gene_synonyms") or []) + (uni.get("short_names") or []))[:60]
            or None,
        ),
        ("Family", uni.get("family")),
        ("Class", ", ".join(classes) or ", ".join((hpa.get("protein_classes") or [])[:2]) or None),
        ("Molecular function", ", ".join(functions) or None),
        (
            "Size",
            f"{uni['protein_length']:,} aa · {mw / 1000:.0f} kDa"
            if mw and uni.get("protein_length")
            else None,
        ),
        (
            "Gene",
            " · ".join(
                x
                for x in (
                    location,
                    f"{size / 1000:.0f} kb" if size else None,
                    f"{gene['exon_count']} exons" if gene.get("exon_count") else None,
                )
                if x
            )
            or None,
        ),
        ("Localisation", ", ".join((uni.get("subcellular_location") or [])[:3]) or None),
    ]
    items = "".join(f"<div><span>{esc(k)}</span><b>{esc(v)}</b></div>" for k, v in cells if v)
    ids = [
        target.get("hgnc_id"),
        target.get("uniprot_id"),
        target.get("ensembl_id"),
        f"Entrez {target['entrez_id']}" if target.get("entrez_id") else None,
    ]
    st.markdown(
        f'<div class="ts-ids">{"".join(f"<span>{esc(i)}</span>" for i in ids if i)}</div>',
        unsafe_allow_html=True,
    )
    if items:
        st.markdown(f'<div class="ts-idcard">{items}</div>', unsafe_allow_html=True)
    if gene.get("summary") or uni.get("function"):
        with st.expander("Function (UniProt) and gene summary (RefSeq)"):
            if uni.get("function"):
                st.markdown(f"**Function** · {uni['function']}")
            if gene.get("summary"):
                st.markdown(f"**Gene** · {gene['summary']}")


# ---------------------------------------------------------------------------
# Evidence profile: radar + one card per dimension
# ---------------------------------------------------------------------------
def radar(scores: list[dict]) -> go.Figure:
    labels = [
        f"{RADAR_LABELS[s['dimension']]} {'n/s' if s['score'] is None else format(s['score'], 'g')}"
        for s in scores
    ]
    values = [s["score"] or 0 for s in scores]
    fig = go.Figure(
        go.Scatterpolar(
            r=values + values[:1],
            theta=labels + labels[:1],
            fill="toself",
            fillcolor="rgba(29,78,137,0.16)",
            line={"color": NAVY, "width": 2},
            mode="lines+markers",
            marker={"size": 7, "color": NAVY},
            hovertemplate="%{theta}<extra></extra>",
        )
    )
    fig.update_layout(
        polar={
            "radialaxis": {
                "range": [0, 1],
                "tickvals": [0.5, 1],
                "showticklabels": False,
                "gridcolor": GRID,
            },
            # Genetics on top, then clockwise in the engine's dimension order
            "angularaxis": {
                "gridcolor": GRID,
                "tickfont": {"size": 12, "color": INK},
                "rotation": 90,
                "direction": "clockwise",
            },
            "bgcolor": "rgba(0,0,0,0)",
        },
        showlegend=False,
        height=380,
        margin={"l": 85, "r": 85, "t": 30, "b": 30},
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def score_card(score: dict, config: dict) -> None:
    dim = score["dimension"]
    value = score["score"]
    dashed = " dashed" if value is None else ""
    bar = f'<div class="ts-bar"><div style="width:{(value or 0) * 100:.0f}%"></div></div>'
    explanation = (
        f'<div class="ts-fact">{esc(score.get("explanation"))}</div>'
        if score.get("explanation")
        else ""
    )
    st.markdown(
        f'<div class="ts-card{dashed}">'
        f'<div style="display:flex;align-items:center;gap:10px"><h4 style="flex:1">{DIMENSION_TITLES[dim]}</h4>'
        f'{pill(score["label"])}<span class="ts-score">{"n/s" if value is None else format(value, "g")}</span></div>'
        f"{bar}"
        f'<p class="ts-small"><b>What we look at</b> · {WHAT_WE_LOOK_AT[dim]}</p>'
        f'<p class="ts-small"><b>Reading</b> · {READING_SUMMARY[dim]}</p>'
        f"{explanation}</div>",
        unsafe_allow_html=True,
    )
    ladder = score_ladder(dim, config)
    with st.popover("Scoring ladder", use_container_width=True):
        for s, condition in ladder:
            reached = value is not None and s not in ("—", "n/s") and abs(float(s) - value) < 1e-9
            st.markdown(
                f"{'**' if reached else ''}`{s}` {condition}{' ← this target**' if reached else ''}"
            )
        if score.get("facts"):
            st.markdown("**Facts behind this score**")
            for fact in score["facts"]:
                st.markdown(f"- {fact}")


def score_legend(config: dict) -> None:
    labels = config.get("labels", [])
    bounds = {lbl: t for t, lbl in labels}
    st.markdown(
        '<div class="ts-small"><b>How to read a score</b><br>'
        f'<b style="color:#1F6B45">Strong</b> ≥ {bounds.get("Strong", 0.8):g} · the best evidence type is present<br>'
        f'<b style="color:#7A4F00">Moderate</b> ≥ {bounds.get("Moderate", 0.5):g} · real evidence, with a clear limit<br>'
        "<b>Limited</b> · indirect or weak evidence<br>"
        "<b>n/s</b> · not scored: data unavailable, which is not the same as absent<br>"
        "No global score: a target is judged on its profile, not on an average.</div>",
        unsafe_allow_html=True,
    )


def evidence_profile(assessment: dict, config: dict) -> None:
    scores = assessment["scores"]
    left, right = st.columns([1, 2.2], gap="large")
    with left:
        st.markdown("### Evidence profile")
        # Static: dragging a polar chart rescales its radial axis and there is
        # no visible way back; the scores are written in the labels anyway
        st.plotly_chart(radar(scores), use_container_width=True, config={"staticPlot": True})
        score_legend(config)
    with right:
        for row in range(0, len(scores), 2):
            cols = st.columns(2, gap="medium")
            for col, score in zip(cols, scores[row : row + 2], strict=False):
                with col:
                    score_card(score, config)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def hbar(
    labels: list[str],
    values: list[float],
    unit: str,
    highlight: set[str] | None = None,
    colors: list[str] | None = None,
    height: int | None = None,
    max_x: float | None = None,
    threshold: tuple[float, str] | None = None,
) -> go.Figure:
    """Horizontal bar chart, first item on top. `highlight` bars are drawn darker."""
    highlight = highlight or set()
    colors = colors or [NAVY if lbl in highlight else "#6E9BCF" for lbl in labels]
    fig = go.Figure(
        go.Bar(
            x=values,
            y=labels,
            orientation="h",
            marker_color=colors,
            text=[
                f"{v:,}" if isinstance(v, int) else f"{v:,.1f}" if v < 100 else f"{v:,.0f}"
                for v in values
            ],
            textposition="outside",
            hovertemplate=f"%{{y}}: %{{x}} {unit}<extra></extra>",
            cliponaxis=False,
        )
    )
    if threshold:
        fig.add_vline(
            x=threshold[0],
            line_dash="dash",
            line_color=MUTED,
            annotation_text=threshold[1],
            annotation_position="top",
        )
    fig.update_layout(
        height=height or max(160, 26 * len(labels) + 60),
        margin={"l": 10, "r": 50, "t": 30, "b": 30},
        yaxis={"autorange": "reversed", "tickfont": {"size": 12}},
        xaxis={
            "title": unit,
            "gridcolor": GRID,
            "range": [0, max_x] if max_x else None,
            # counts: no 0.5 tick when the largest count is small
            "dtick": 1
            if values and all(isinstance(v, int) for v in values) and max(values) <= 8
            else None,
        },
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    return fig


def show_chart(fig: go.Figure) -> None:
    """Report charts: hover stays, zoom and pan are off (no way back without
    the toolbar, and a report figure has nothing to zoom into)."""
    fig.update_layout(dragmode=False)
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
    )
