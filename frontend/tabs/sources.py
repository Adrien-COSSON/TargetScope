# frontend/tabs/sources.py
"""Sources tab: status of every source, what it is used for, and citation."""

import streamlit as st

from frontend.content import DISCLAIMER, TAB_INTROS
from frontend.links import DATABASE_CITATIONS, cited_publications, key_links

SOURCES = {
    "open_targets": (
        "Open Targets Platform",
        "Associations, tractability, constraint, safety, mutation frequencies (Cancer Gene Census)",
        "https://platform.opentargets.org",
    ),
    "chembl": ("ChEMBL", "Drugs, mechanisms, measured activities", "https://www.ebi.ac.uk/chembl/"),
    "clinvar": (
        "ClinVar",
        "Germline variants and classifications",
        "https://www.ncbi.nlm.nih.gov/clinvar/",
    ),
    "uniprot": ("UniProt", "Function, family, domains, PDB structures", "https://www.uniprot.org"),
    "pubmed": ("PubMed", "Publication counts", "https://pubmed.ncbi.nlm.nih.gov"),
    "europe_pmc": ("Europe PMC", "Publications per year", "https://europepmc.org"),
    "reactome": ("Reactome", "Pathways", "https://reactome.org"),
    "string": ("STRING", "Interaction partners", "https://string-db.org"),
    "mgi": (
        "MGI / Alliance of Genome Resources",
        "Mouse phenotypes and disease models",
        "https://www.alliancegenome.org",
    ),
    "hpa": (
        "Human Protein Atlas",
        "Expression summary, localisation, cancer data",
        "https://www.proteinatlas.org",
    ),
    "clinicaltrials": (
        "ClinicalTrials.gov",
        "Trial counts and the competitive landscape",
        "https://clinicaltrials.gov",
    ),
    "gene_info": (
        "MyGene.info (NCBI Gene)",
        "Chromosome band, gene size, exons",
        "https://mygene.info",
    ),
    "alphafold": ("AlphaFold DB", "Predicted 3D structure", "https://alphafold.ebi.ac.uk"),
    "hpa_profiles": (
        "Human Protein Atlas (full profiles)",
        "Expression by tissue, single cell type, immune cell",
        "https://www.proteinatlas.org",
    ),
    "ot_extras": (
        "Open Targets (extras)",
        "Chemical probes, target class, essentiality",
        "https://platform.opentargets.org",
    ),
    "competition": (
        "ClinicalTrials.gov (per drug)",
        "Sponsors, statuses, reasons for stopping",
        "https://clinicaltrials.gov",
    ),
}
STATUS = {"ok": "Answered", "no_data": "Answered, no data", "unavailable": "Unavailable"}


def render(assessment: dict, raw: dict) -> None:
    st.markdown("## Where does every number come from?")
    st.markdown(TAB_INTROS["sources"])
    st.markdown("### Sources queried for this assessment")
    st.dataframe(
        [
            {
                "Source": SOURCES.get(s["source"], (s["source"],))[0],
                "Status": STATUS.get(s["status"], s["status"]),
                "Used for": SOURCES.get(s["source"], ("", ""))[1],
                "Link": SOURCES.get(s["source"], ("", "", None))[2],
            }
            for s in assessment["sources"]
        ],
        column_config={"Link": st.column_config.LinkColumn("Website", display_text="open")},
        hide_index=True,
        use_container_width=True,
    )
    st.markdown(f"### Key links for {assessment['target']['gene_symbol']}")
    st.markdown(
        '<p class="ts-small">Where to dig further: each page holds far more than this report '
        "summarises.</p>",
        unsafe_allow_html=True,
    )
    links = key_links(assessment, raw)
    st.dataframe(
        [
            {"Resource": x["resource"], "What you will find": x["use"], "Link": x["url"]}
            for x in links
        ],
        column_config={"Link": st.column_config.LinkColumn("Link", display_text="open")},
        hide_index=True,
        use_container_width=True,
        height=36 * (len(links) + 1) + 4,
    )

    st.markdown("### Publications cited in this report")
    st.markdown(
        '<p class="ts-small">Papers TargetScope points to directly (protein function, mouse '
        "phenotypes, safety). Most scores come from databases that aggregate thousands of papers: "
        "the papers behind a genetics or drug score are on the Open Targets evidence page above.</p>",
        unsafe_allow_html=True,
    )
    pubs = cited_publications(assessment, raw)
    if pubs:
        st.dataframe(
            [
                {"Reference": x["reference"], "Supports": x["supports"], "Link": x["url"]}
                for x in pubs
            ],
            column_config={"Link": st.column_config.LinkColumn("Link", display_text="open")},
            hide_index=True,
            use_container_width=True,
            height=min(36 * (len(pubs) + 1) + 4, 400),
        )
    else:
        st.markdown(
            '<p class="ts-small">No publication cited directly.</p>', unsafe_allow_html=True
        )

    with st.expander("How to cite the databases"):
        for name, citation in DATABASE_CITATIONS:
            st.markdown(f"**{name}** · {citation}")
        st.caption(
            "Check the latest reference on each website: most databases publish an update every year or two."
        )

    st.markdown(
        f"Assessment generated on **{assessment['generated_at'][:16].replace('T', ' ')} UTC** with evidence "
        f"engine **v{assessment['engine_version']}**: the version identifies the scoring rules, so an old "
        "report stays explainable after the thresholds change."
    )
    st.markdown(
        "**Citation and licences** · Open Targets Platform data are CC0; please cite the latest Open Targets "
        "publication. Each source keeps its own licence (for example ChEMBL and the Human Protein Atlas are "
        "CC BY-SA, COSMIC data reach TargetScope through Open Targets). See the README for the full list."
    )
    st.caption(DISCLAIMER)
