# frontend/app.py
"""TargetScope — Streamlit frontend.

Run (from the project root, with the API running on port 8000):
    uvicorn backend.main:app --port 8000
    streamlit run frontend/app.py

Layout (maquette B): search bar -> title, verdict and identity card ->
evidence profile (radar + six explained scores) -> detail tabs.
"""

import sys
from pathlib import Path

# `streamlit run frontend/app.py` puts frontend/ on the path, not the project
# root: add it so `from frontend import ...` works like in the tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from frontend import api  # noqa: E402
from frontend.components import (  # noqa: E402
    back_to_top,
    evidence_profile,
    header,
    identity_card,
    inject_style,
    intro,
    page_anchor,
)
from frontend.content import DISCLAIMER  # noqa: E402
from frontend.tabs import (  # noqa: E402
    biology,
    clinical,
    genetics,
    safety,
    sources,
    summary,
    toolbox,
)

st.set_page_config(page_title="TargetScope", page_icon="◎", layout="wide")
inject_style()
page_anchor()

NO_INDICATION = "None: assess the target alone"
ALL_AREAS = "All therapeutic areas"
MENU_LIMIT = 300  # diseases listed in the indication menu


# ---------------------------------------------------------------------------
# Search bar
# ---------------------------------------------------------------------------
def search_bar() -> tuple[str | None, str | None, bool]:
    """Target, optional indication id, and whether "Assess" was clicked."""
    st.markdown(
        '<span style="font-weight:700;font-size:1.25rem">TargetScope</span> '
        '<span class="ts-small">· from target name to evidence-grounded target assessment</span>',
        unsafe_allow_html=True,
    )
    intro()
    c1, c2, c3 = st.columns([1.2, 1.4, 2.4], vertical_alignment="bottom")
    query = c1.text_input(
        "Target", placeholder="Symbol, alias, UniProt or Ensembl id (e.g. XPO1)"
    ).strip()
    if not query:
        return None, None, False

    try:
        with st.spinner("Resolving the target and its associated diseases…"):
            options = api.indications(query)
    except api.ApiError as e:
        st.error(str(e) if e.status_code != 404 else f"No human gene matches '{query}'.")
        return None, None, False

    areas = options.get("therapeutic_areas") or []
    area_labels = [ALL_AREAS] + [f"{a['name']} ({a['disease_count']})" for a in areas]
    area_choice = c2.selectbox("Therapeutic area", area_labels)
    area_id = None if area_choice == ALL_AREAS else areas[area_labels.index(area_choice) - 1]["id"]

    diseases = [
        d
        for d in options.get("diseases") or []
        if area_id is None or area_id in d["therapeutic_area_ids"]
    ]
    menu = {NO_INDICATION: None} | {
        f"{d['name']} · {d['score']:.2f}": d["id"] for d in diseases[:MENU_LIMIT]
    }
    choice = c3.selectbox("Indication (optional) · Open Targets association score", list(menu))
    indication_id = menu[choice]

    c4, c5 = st.columns([3.4, 1.2], vertical_alignment="bottom")
    with c4.expander(
        "Search another disease (no evidence needed: a missing link is a finding too)"
    ):
        text = st.text_input("Disease name", key="disease_search")
        if len(text.strip()) >= 2:
            try:
                hits = api.search_diseases(text.strip())
            except api.ApiError as e:
                hits = []
                st.error(str(e))
            if hits:
                found = {h["name"]: h["id"] for h in hits}
                picked = st.radio("Results", list(found), index=None)
                if picked:
                    indication_id = found[picked]
                    st.caption(f"Indication set to **{picked}**.")
            else:
                st.caption("No disease found.")
    clicked = c5.button("Assess", type="primary", use_container_width=True)
    return query, indication_id, clicked


def pdf_button(assessment: dict, raw: dict, synthesis: dict | None, config: dict) -> None:
    """Download the whole report as PDF.

    Built once per assessment and kept in the session: Streamlit reruns the
    script on every click, and rebuilding the PDF each time would cost ~1 s.
    """
    try:
        # Imported here, not at the top: without fpdf2 / matplotlib the page
        # must still work, only the PDF button is missing
        from frontend.report import build_pdf, file_name
    except ImportError as e:
        st.caption(f"PDF report unavailable ({e.name} not installed): pip install fpdf2 matplotlib")
        return
    key = ("pdf", assessment["target"]["gene_symbol"], assessment["generated_at"], bool(synthesis))
    if st.session_state.get("pdf_key") != key:
        try:
            st.session_state["pdf_bytes"] = build_pdf(assessment, raw, synthesis, config)
        except Exception as e:  # the report must never break the page
            st.session_state["pdf_bytes"] = None
            st.caption(f"PDF report unavailable: {e}")
        st.session_state["pdf_key"] = key
    data = st.session_state.get("pdf_bytes")
    if data:
        _, right = st.columns([4, 1])
        right.download_button(
            "⬇ Download PDF report",
            data=data,
            file_name=file_name(assessment),
            mime="application/pdf",
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def main() -> None:
    query, indication_id, clicked = search_bar()
    if clicked and query:
        st.session_state["run"] = (query, indication_id)

    run = st.session_state.get("run")
    if not run:
        st.caption(DISCLAIMER)
        return

    try:
        with st.spinner("Querying the public sources in parallel (5 to 45 s on a first search)…"):
            result = api.assess(*run)
        config = api.engine_config()["config"]
    except api.ApiError as e:
        st.error(f"Assessment failed: {e}")
        return
    assessment, raw = result["assessment"], result["raw"]

    synthesis, synthesis_error = None, None
    try:
        with st.spinner("Writing the AI summary from the evidence…"):
            synthesis = api.synthesis(assessment)
    except api.ApiError as e:
        synthesis_error = f"AI summary failed: {e}"

    st.markdown("---")
    header(assessment, synthesis["headline"] if synthesis else None)
    pdf_button(assessment, raw, synthesis, config)
    identity_card(assessment["target"], raw)
    st.markdown("")
    evidence_profile(assessment, config)

    st.markdown("")
    tabs = st.tabs(
        [
            "Summary",
            "Genetics",
            "Clinical & competition",
            "Biology",
            "Models & safety",
            "Tool Box",
            "Sources",
        ]
    )
    with tabs[0]:
        summary.render(assessment, synthesis, synthesis_error)
    with tabs[1]:
        genetics.render(assessment, raw)
    with tabs[2]:
        clinical.render(assessment, raw)
    with tabs[3]:
        biology.render(assessment, raw)
    with tabs[4]:
        safety.render(assessment, raw, config)
    with tabs[5]:
        toolbox.render(assessment, raw)
    with tabs[6]:
        sources.render(assessment, raw)
    st.caption(DISCLAIMER)
    back_to_top()


main()
