# frontend/tabs/toolbox.py
"""Tool Box tab: 3D structures, chemical probes, compounds, antibodies, reagents.

The 3D viewer uses 3Dmol.js inside a Streamlit HTML component. The structure
file is downloaded by the frontend (cached) and embedded, so the browser does
not need to reach AlphaFold or RCSB itself.
"""

import json

import streamlit as st
import streamlit.components.v1 as components

from frontend import api
from frontend.components import esc, fmt_int, reading, source_footer
from frontend.content import TAB_INTROS

VIEWER_JS = "https://3Dmol.org/build/3Dmol-min.js"  # official 3Dmol.js build
RCSB_FILE = "https://files.rcsb.org/download/{pdb_id}.pdb"


def _viewer(pdb_text: str, color_by_confidence: bool) -> None:
    """Cartoon view; AlphaFold models are coloured by pLDDT (stored in the B-factor)."""
    style = (
        "{cartoon: {colorfunc: function(atom) { var b = atom.b;"
        " return b > 90 ? '#1D4E89' : b > 70 ? '#6E9BCF' : b > 50 ? '#F2C14E' : '#E07A3F'; }}}"
        if color_by_confidence
        else "{cartoon: {color: 'spectrum'}}"
    )
    html = f"""
    <div id="v" style="width:100%;height:440px;position:relative;border:1px solid #E2DFD8;border-radius:8px"></div>
    <script src="{VIEWER_JS}"></script>
    <script>
      const viewer = $3Dmol.createViewer(document.getElementById('v'), {{backgroundColor: '#FBFAF7'}});
      viewer.addModel({json.dumps(pdb_text)}, 'pdb');
      viewer.setStyle({{}}, {style});
      viewer.setStyle({{hetflag: true, not: {{resn: 'HOH'}}}}, {{stick: {{colorscheme: 'greenCarbon'}}}});
      viewer.zoomTo(); viewer.render();
    </script>"""
    components.html(html, height=460)


FULL_LENGTH = 0.9  # an experimental structure covering >= 90% of the sequence


def _coverage(s: dict, length: int | None) -> float | None:
    if not (length and s.get("start") and s.get("end")):
        return None
    return (s["end"] - s["start"] + 1) / length


def _structure_options(structures: list[dict], alphafold: dict, length: int | None) -> dict:
    """{label: (kind, url)}, the default (first) being the most complete protein.

    Order: experimental full-length structures (best resolution first), then the
    AlphaFold model (always full length, but predicted), then partial structures
    from the largest to the smallest fragment.
    """

    def label(s: dict) -> str:
        res = f"{s['resolution']:g} Å" if s.get("resolution") else ""
        cov = _coverage(s, length)
        span = f", residues {s['start']}-{s['end']}" if s.get("start") else ""
        share = f" ({cov:.0%} of the protein)" if cov is not None else ""
        return f"{s['pdb_id']} · {s.get('method')} {res}{span}{share}"

    full = [s for s in structures if (_coverage(s, length) or 0) >= FULL_LENGTH]
    partial = [s for s in structures if s not in full]
    full.sort(key=lambda s: s.get("resolution") or 99)
    partial.sort(key=lambda s: -(_coverage(s, length) or 0))
    options = {label(s): ("pdb", RCSB_FILE.format(pdb_id=s["pdb_id"])) for s in full[:10]}
    if alphafold.get("found"):
        options[
            f"AlphaFold model · full length, predicted (mean pLDDT {alphafold.get('mean_plddt'):.0f})"
        ] = ("af", alphafold["pdb_url"])
    for s in partial[: 30 - len(options)]:
        options[label(s)] = ("pdb", RCSB_FILE.format(pdb_id=s["pdb_id"]))
    return options


def _structures(uni: dict, alphafold: dict) -> None:
    st.markdown("### 3D structure")
    structures = uni.get("structures") or []
    length = uni.get("protein_length")
    options = _structure_options(structures, alphafold, length)
    left, right = st.columns([3, 2], gap="large")
    with right:
        n_xray = sum(1 for s in structures if s.get("resolution"))
        best = next((s for s in structures if s.get("resolution")), None)
        st.markdown(
            f'<p class="ts-small"><b>{len(structures)}</b> experimental structures in the PDB '
            f"({n_xray} with a resolution)"
            + (f", best {best['resolution']:g} Å ({best['pdb_id']})" if best else "")
            + ".</p>",
            unsafe_allow_html=True,
        )
        if alphafold.get("found"):
            st.markdown(
                f'<p class="ts-small"><b>AlphaFold</b> · mean pLDDT {alphafold["mean_plddt"]:.0f}/100; '
                f"{(alphafold.get('fraction_very_high') or 0):.0%} of residues predicted with very high "
                "confidence. Colours: dark blue &gt; 90, light blue 70 to 90, yellow 50 to 70, "
                "orange &lt; 50 (often disordered).</p>",
                unsafe_allow_html=True,
            )
        if structures:
            reading(
                "Experimental structures, especially with a bound ligand, make structure-based design "
                "possible from day one."
                if n_xray
                else "Only NMR or predicted structures: "
                "structure-based design will need new structural work."
            )
        elif alphafold.get("found"):
            reading(
                "No experimental structure: design would rely on the AlphaFold model, to be confirmed."
            )
        if structures:
            st.dataframe(
                [
                    {
                        "PDB": s["pdb_id"],
                        "Method": s["method"],
                        "Resolution (Å)": s["resolution"],
                        "Residues": f"{s['start']}-{s['end']}" if s.get("start") else None,
                        "Coverage": f"{_coverage(s, length):.0%}"
                        if _coverage(s, length) is not None
                        else None,
                        "Link": s["url"],
                    }
                    for s in structures
                ],
                column_config={"Link": st.column_config.LinkColumn("RCSB", display_text="open")},
                hide_index=True,
                use_container_width=True,
                height=min(36 * (len(structures) + 1) + 4, 300),
            )
    with left:
        if not options:
            st.info("No structure available.")
            return
        choice = st.selectbox(
            "Structure",
            list(options),
            key="structure_choice",
            help="Full-length experimental structures come first, then the AlphaFold model, "
            "then fragments (often a single domain).",
        )
        kind, url = options[choice]
        if st.toggle("Show the 3D viewer", value=True, key="show_viewer"):
            with st.spinner("Downloading the structure…"):
                text = api.structure_file(url)
            if text:
                _viewer(text, color_by_confidence=(kind == "af"))
            else:
                st.warning("The structure file could not be downloaded.")


def _probes(extras: dict) -> None:
    st.markdown("### Chemical probes")
    st.markdown(
        '<p class="ts-small">Selective, well-characterised compounds to test the biology in cells or '
        "animals, ideally with an inactive control. High-quality probes are flagged by the "
        "Probes &amp; Drugs portal, the Chemical Probes Portal or the SGC.</p>",
        unsafe_allow_html=True,
    )
    probes = extras.get("chemical_probes")
    if probes is None:
        st.warning("Open Targets could not be reached: probe list unavailable.")
    elif not probes:
        reading(
            "No chemical probe referenced: target validation will need new tool compounds, or genetic "
            "tools (CRISPR, siRNA) instead."
        )
    else:
        st.dataframe(
            [
                {
                    "Probe": p["name"],
                    "High quality": "Yes" if p["high_quality"] else "",
                    "Mechanism": p["mechanism"],
                    "Control": p["control"],
                    "Cells score": p["score_in_cells"],
                    "Organisms score": p["score_in_organisms"],
                    "Origin": p["origin"],
                    "Link": (p["urls"][0]["url"] if p["urls"] else None),
                }
                for p in probes
            ],
            column_config={"Link": st.column_config.LinkColumn("Source", display_text="open")},
            hide_index=True,
            use_container_width=True,
        )


def render(assessment: dict, raw: dict) -> None:
    uni = raw.get("uniprot") or {}
    alphafold = raw.get("alphafold") or {}
    extras = raw.get("ot_extras") or {}
    chembl = raw.get("chembl") or {}
    profiles = raw.get("hpa_profiles") or {}
    target = assessment["target"]
    symbol = target["gene_symbol"]

    st.markdown(f"## What do we have to start working on {esc(symbol)}?")
    st.markdown(TAB_INTROS["toolbox"])

    _structures(uni, alphafold)
    _probes(extras)

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("### Compounds")
        st.markdown(
            f'<p class="ts-small">{fmt_int(chembl.get("potent_activity_count"))} potent activities '
            f"(pChEMBL ≥ 7) out of {fmt_int(chembl.get('activity_count'))} measured in ChEMBL. "
            "The ChEMBL target page lists the compounds, sortable by potency.</p>",
            unsafe_allow_html=True,
        )
        if chembl.get("target_chembl_id"):
            st.link_button(
                "Compounds in ChEMBL",
                f"https://www.ebi.ac.uk/chembl/target_report_card/{chembl['target_chembl_id']}/",
            )
        essential = extras.get("is_essential")
        if essential is not None:
            st.markdown(
                f'<p class="ts-small"><b>Core essential gene (DepMap, via Open Targets)</b> · {"yes" if essential else "no"}. '
                "An essential gene is hard to knock out in cell lines: prefer inducible or partial "
                "knock-down.</p>",
                unsafe_allow_html=True,
            )
    with right:
        st.markdown("### Antibodies and reagents")
        antibodies = profiles.get("antibodies") or []
        st.markdown(
            f'<p class="ts-small"><b>{len(antibodies)}</b> antibodies used by the Human Protein Atlas '
            "(validated for its own stainings): "
            + esc(", ".join(antibodies[:8]))
            + ("…" if len(antibodies) > 8 else "")
            + "</p>",
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p class="ts-small">Commercial reagents (knock-out lines, shRNA, recombinant protein) have '
            "no open data source: these links open pre-filled searches.</p>",
            unsafe_allow_html=True,
        )
        links = [
            ("DepMap gene page", f"https://depmap.org/portal/gene/{symbol}"),
            ("Plasmids (Addgene)", f"https://www.addgene.org/search/catalog/plasmids/?q={symbol}"),
            ("HPA antibodies", profiles.get("url")),
        ]
        for label, url in links:
            if url:
                st.link_button(label, url)

    source_footer(
        [
            ("UniProt / PDB", uni.get("url")),
            ("AlphaFold DB", alphafold.get("url")),
            ("Open Targets (chemical probes)", None),
            ("ChEMBL", None),
            ("Human Protein Atlas", profiles.get("url")),
        ],
        assessment["generated_at"],
    )
