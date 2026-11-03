# frontend/tabs/clinical.py
"""Clinical & competition tab: drugs on the target, tractability by modality,
competitive landscape from ClinicalTrials.gov (sponsors, stops and why)."""

import streamlit as st

from frontend.components import esc, fmt_int, hbar, reading, show_chart, source_footer
from frontend.content import TAB_INTROS

ACTIVE = "Recruiting, active or not yet recruiting"


def _drug_table(drugs: list[dict], programs: list[dict]) -> None:
    # ChEMBL has no company field: the developer is the main industry sponsor
    # of the drug's trials (competitive landscape), when there is one
    developers = {
        p.get("molecule_chembl_id"): p.get("main_industry_sponsor")
        for p in programs
        if p.get("main_industry_sponsor")
    }
    st.dataframe(
        [
            {
                "Drug": d.get("name") or d.get("molecule_chembl_id"),
                "Developer": developers.get(d.get("molecule_chembl_id")),
                "Phase": d.get("phase_label") or "Preclinical",
                "Modality": d.get("molecule_type"),
                "Mechanism": (d.get("action_type") or "").capitalize() or None,
                "On the protein itself": "Yes"
                if d.get("via_target_type") == "SINGLE PROTEIN"
                else f"No ({(d.get('via_target_type') or '?').lower()})",
                "Mutant-selective": d.get("target_variant"),
                "Warnings": ", ".join(
                    w
                    for w, on in (
                        ("withdrawn", d.get("withdrawn")),
                        ("black box", d.get("black_box_warning")),
                    )
                    if on
                )
                or None,
                "ChEMBL": d.get("url"),
            }
            for d in drugs
        ],
        column_config={"ChEMBL": st.column_config.LinkColumn("ChEMBL", display_text="open")},
        hide_index=True,
        use_container_width=True,
    )
    if developers:
        st.caption(
            "Developer: the company sponsoring most of the drug's industry trials on "
            "ClinicalTrials.gov (ChEMBL does not record companies). Licensees in other regions "
            "appear in “Who is active?” below."
        )


def _mechanism_tags(drugs: list[dict], ot: dict) -> None:
    actions: dict[str, int] = {}
    for d in drugs:
        key = (d.get("action_type") or "unknown").capitalize()
        actions[key] = actions.get(key, 0) + 1
    tags = "".join(f'<span class="ts-tag on">{esc(k)} · {v}</span>' for k, v in actions.items())
    for absent in ("Degrader", "Agonist", "Antagonist", "Inhibitor"):
        if absent not in actions:
            tags += f'<span class="ts-tag">{absent} · 0</span>'
    st.markdown(f"<div>{tags}</div>", unsafe_allow_html=True)
    tract = ot.get("tractability") or {}
    if tract:
        st.markdown(
            '<p class="ts-small"><b>Tractability by modality (Open Targets)</b> · '
            "criteria met for each modality, from clinical precedence to structural prediction</p>",
            unsafe_allow_html=True,
        )
        for modality in ("Small molecule", "Antibody", "PROTAC", "Other modalities"):
            buckets = tract.get(modality) or []
            line = ", ".join(buckets) if buckets else "no criterion met"
            st.markdown(
                f'<p class="ts-small"><b>{modality}</b> · {esc(line)}</p>', unsafe_allow_html=True
            )


def _landscape(comp: dict, indication: dict | None) -> None:
    st.markdown("### Competitive landscape")
    if comp.get("found") is None:
        st.warning(
            "Competitive landscape unavailable (ChEMBL or ClinicalTrials.gov did not answer)."
        )
        return
    programs = comp.get("programs") or []
    if not programs:
        reading(
            "No drug on this target has reached the clinic: the field is open (first-in-class "
            "opportunity), but nothing is de-risked in humans."
        )
        return
    cols = st.columns(5)
    cols[0].metric("Approved drugs", comp.get("approved_count", 0))
    cols[1].metric("Clinical programs", len(programs))
    cols[2].metric(
        "Trials analysed",
        fmt_int(comp.get("trial_count")),
        help="Distinct trials read for the clinical drugs (up to 300 per drug)",
    )
    cols[3].metric("Active trials", fmt_int(comp.get("active_trial_count")), help=ACTIVE)
    cols[4].metric("Industry sponsors", len(comp.get("industry_sponsors") or []))
    if comp.get("incomplete"):
        st.caption(
            "ClinicalTrials.gov did not answer for at least one drug: counts are a lower bound."
        )

    st.dataframe(
        [
            {
                "Drug": p["drug"],
                "Max phase": p["max_phase"],
                "Modality": p["modality"],
                "Mechanism": (p.get("action_type") or "").capitalize() or None,
                "Selective": "Yes" if p["selective"] else "Family / complex",
                "Trials": p["trial_count"],
                "Active": p["active_trials"],
                "Stopped": p["stopped_trials"],
                "Main industry sponsor": p["main_industry_sponsor"],
            }
            for p in programs
        ],
        hide_index=True,
        use_container_width=True,
    )

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### Who is active?")
        sponsors = comp.get("industry_sponsors") or []
        if sponsors:
            show_chart(
                hbar([s["name"][:40] for s in sponsors], [s["trials"] for s in sponsors], "trials")
            )
        share = comp.get("academic_trial_share")
        if share is not None:
            st.caption(
                f"{share:.0%} of the trials are sponsored by academic or public institutions."
            )
    with right:
        st.markdown("#### Why did trials stop?")
        reasons = comp.get("stop_reasons") or {}
        if reasons:
            colors = {
                "Safety": "#9A3412",
                "Efficacy": "#D97706",
                "Business": "#1D4E89",
                "Enrollment": "#6E9BCF",
                "Other": "#C9C4B9",
                "Not given": "#E2DFD8",
            }
            items = sorted(reasons.items(), key=lambda kv: -kv[1])
            show_chart(
                hbar(
                    [k for k, _ in items],
                    [v for _, v in items],
                    "stopped trials",
                    colors=[colors.get(k, "#C9C4B9") for k, _ in items],
                )
            )
            st.caption(
                "Categories assigned from the free-text reason by keyword rules: check the "
                "original reason below before drawing conclusions."
            )
        else:
            st.markdown(
                '<p class="ts-small">No terminated, withdrawn or suspended trial.</p>',
                unsafe_allow_html=True,
            )

    stopped = comp.get("stopped_trials") or []
    if stopped:
        with st.expander(
            f"Stopped trials and their reasons ({len(stopped)} most recent)", expanded=True
        ):
            st.dataframe(
                [
                    {
                        "Trial": t["nct_id"],
                        "Drug": ", ".join(t.get("target_drugs") or []) or None,
                        "Status": t["status"].capitalize(),
                        "Phase": t["phase"],
                        "Category": t["reason_category"],
                        "Reason given": t["why_stopped"],
                        "Sponsor": t["sponsor"],
                        "Sponsor type": "Industry"
                        if t.get("sponsor_class") == "INDUSTRY"
                        else "Academic / public",
                        "Start": t["start_date"],
                        "Link": t["link"],
                    }
                    for t in stopped
                ],
                column_config={
                    "Link": st.column_config.LinkColumn("Registry", display_text="open")
                },
                hide_index=True,
                use_container_width=True,
            )
            st.caption(
                "The sponsor runs the trial, it is not always the drug's developer: many trials "
                "are investigator-initiated by hospitals, with the drug supplied by the company."
            )
    if indication:
        trials = comp.get("indication_trials") or []
        st.markdown(f"#### Trials in {esc(indication['disease_name'])}")
        if trials:
            st.dataframe(
                [
                    {
                        "Trial": t["nct_id"],
                        "Title": t["title"],
                        "Status": t["status"].capitalize(),
                        "Phase": t["phase"],
                        "Drugs": ", ".join(t["drugs"]),
                        "Sponsor": t["sponsor"],
                        "Start": t["start_date"],
                        "Link": t["link"],
                    }
                    for t in trials
                ],
                column_config={
                    "Link": st.column_config.LinkColumn("Registry", display_text="open")
                },
                hide_index=True,
                use_container_width=True,
            )
        else:
            reading(
                "None of the drugs on this target has a registered trial mentioning this indication: "
                "either an open space, or an indication others did not consider worth testing."
            )


def render(assessment: dict, raw: dict) -> None:
    chembl = raw.get("chembl") or {}
    ot = raw.get("open_targets") or {}
    comp = raw.get("competition") or {}
    target = assessment["target"]["gene_symbol"]

    st.markdown(f"## Who is drugging {esc(target)}, and how far did they go?")
    st.markdown(TAB_INTROS["clinical"])

    st.markdown("### Drugs acting on the target")
    if chembl.get("found") is None:
        st.warning("ChEMBL could not be reached: drug list unavailable.")
    elif not chembl.get("drugs"):
        st.markdown(
            '<p class="ts-small">No drug with a known mechanism on this target in ChEMBL.</p>',
            unsafe_allow_html=True,
        )
    else:
        _drug_table(chembl["drugs"], comp.get("programs") or [])
        _mechanism_tags(chembl["drugs"], ot)
    st.markdown(
        f'<p class="ts-small"><b>Chemistry</b> · {fmt_int(chembl.get("activity_count"))} measured activities, '
        f"{fmt_int(chembl.get('potent_activity_count'))} with pChEMBL ≥ 7 (≤ 100 nM).</p>",
        unsafe_allow_html=True,
    )

    _landscape(comp, assessment.get("indication"))

    source_footer(
        [
            (
                "ChEMBL",
                f"https://www.ebi.ac.uk/chembl/target_report_card/{chembl.get('target_chembl_id')}/"
                if chembl.get("target_chembl_id")
                else None,
            ),
            ("ClinicalTrials.gov", "https://clinicaltrials.gov"),
            ("Open Targets tractability", None),
        ],
        assessment["generated_at"],
    )
