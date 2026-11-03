# frontend/report.py
"""PDF report of an assessment: the same evidence as the web page, on paper.

Built with fpdf2 (pure Python) and matplotlib for the figures, so it installs
with pip on any OS: plotly's own image export (kaleido) needs a Chrome
browser on the machine. The Unicode font (DejaVu Sans) is the one shipped
with matplotlib, so ≥, Å or ★ print correctly without bundling a font.

Entry point: build_pdf(assessment, raw, synthesis, config) -> bytes
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display: render to memory only
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from fpdf import FPDF  # noqa: E402
from fpdf.fonts import FontFace  # noqa: E402

from frontend.content import (  # noqa: E402
    DIMENSION_TITLES,
    DISCLAIMER,
    OT_SCORE_HELP,
    RADAR_LABELS,
    READING_SUMMARY,
    WHAT_WE_LOOK_AT,
    is_oncology,
)
from frontend.links import DATABASE_CITATIONS, cited_publications, key_links  # noqa: E402

NAVY = (29, 78, 137)
RUST = (154, 52, 18)
INK = (27, 31, 36)
MUTED = (74, 82, 96)
LIGHT = (243, 241, 236)
FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
FONT = "DejaVu"
MAX_ROWS = 12  # rows per table: the PDF is a summary, the web page has everything

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


def _fmt(value, digits: int = 2) -> str:
    """Numbers for print: integers with separators, floats rounded, None as a dash."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return (
            f"{value:.{digits}g}" if value < 1 else f"{value:,.{digits}f}".rstrip("0").rstrip(".")
        )
    return str(value)


def _cut(text, n: int) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Figures (matplotlib -> PNG in memory)
# ---------------------------------------------------------------------------
def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf


def _radar_png(scores: list[dict]) -> io.BytesIO:
    """Same layout as the web radar: Genetics on top, clockwise, n/s at 0 and dashed."""
    n = len(scores)
    angles = [2 * np.pi * i / n for i in range(n)]
    values = [s["score"] or 0 for s in scores]
    fig, ax = plt.subplots(figsize=(4.2, 4.2), subplot_kw={"polar": True})
    ax.set_theta_offset(np.pi / 2)  # first dimension on top
    ax.set_theta_direction(-1)  # then clockwise
    ax.plot(angles + angles[:1], values + values[:1], color="#1D4E89", lw=2)
    ax.fill(angles + angles[:1], values + values[:1], color="#1D4E89", alpha=0.16)
    for a, v, s in zip(angles, values, scores, strict=True):
        ax.plot(a, v, "o", color="#1D4E89" if s["score"] is not None else "#C9C4B9", ms=5)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.5, 1])
    ax.set_yticklabels([])
    ax.set_xticks(angles)
    ax.set_xticklabels(
        [
            f"{RADAR_LABELS[s['dimension']]}\n{'n/s' if s['score'] is None else format(s['score'], 'g')}"
            for s in scores
        ],
        fontsize=9,
        color="#1B1F24",
    )
    ax.tick_params(axis="x", pad=14)
    ax.spines["polar"].set_color("#C9C4B9")
    ax.grid(color="#E2DFD8")
    return _png(fig)


def _hbar_png(
    labels: list[str],
    values: list[float],
    unit: str,
    colors: list[str] | None = None,
    threshold: float | None = None,
) -> io.BytesIO:
    height = 0.32 * len(labels) + 0.8
    fig, ax = plt.subplots(figsize=(6.4, height))
    ax.barh(range(len(labels)), values, color=colors or "#6E9BCF", height=0.7)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel(unit, fontsize=8, color="#4A5260")
    ax.tick_params(axis="x", labelsize=7, colors="#4A5260")
    top = max(values) if values else 1
    for i, v in enumerate(values):
        label = f"{v:,}" if isinstance(v, int) else f"{v:,.1f}"
        ax.text(v + top * 0.01, i, label, va="center", fontsize=7, color="#1B1F24")
    if threshold is not None:
        ax.axvline(threshold, ls="--", color="#4A5260", lw=0.8)
    ax.set_xlim(0, max(top * 1.12, (threshold or 0) * 1.1))
    if all(isinstance(v, int) for v in values) and top <= 8:
        ax.set_xticks(range(0, int(top) + 1))
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="x", color="#E2DFD8", lw=0.6)
    ax.set_axisbelow(True)
    return _png(fig)


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
class Report(FPDF):
    def __init__(self, title: str):
        super().__init__(format="A4")
        self.report_title = title
        self.set_margins(16, 16, 16)
        self.set_auto_page_break(True, margin=18)
        for style, file in (
            ("", "DejaVuSans.ttf"),
            ("B", "DejaVuSans-Bold.ttf"),
            ("I", "DejaVuSans-Oblique.ttf"),
        ):
            self.add_font(FONT, style, str(FONT_DIR / file))
        self.set_font(FONT, size=9)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font(FONT, "", 7.5)
        self.set_text_color(*MUTED)
        self.cell(0, 5, f"TargetScope · {self.report_title}", align="L")
        self.ln(8)

    def footer(self):
        self.set_y(-12)
        self.set_font(FONT, "", 7)
        self.set_text_color(*MUTED)
        self.cell(0, 5, DISCLAIMER.split(". ")[0] + ".", align="L")
        self.cell(0, 5, f"{self.page_no()}/{{nb}}", align="R")

    # -- building blocks ----------------------------------------------------
    def h1(self, text: str):
        self.set_font(FONT, "B", 15)
        self.set_text_color(*INK)
        self.multi_cell(0, 7.5, text)
        self.ln(1)

    def h2(self, text: str):
        if self.get_y() > self.h - 50:  # never a title alone at the bottom of a page
            self.add_page()
        self.ln(3)
        self.set_font(FONT, "B", 11.5)
        self.set_text_color(*NAVY)
        self.multi_cell(0, 6, text)
        self.set_draw_color(*NAVY)
        self.line(self.l_margin, self.get_y() + 0.5, self.w - self.r_margin, self.get_y() + 0.5)
        self.ln(2.5)

    def h3(self, text: str):
        if self.get_y() > self.h - 40:
            self.add_page()
        self.ln(1.5)
        self.set_font(FONT, "B", 9.5)
        self.set_text_color(*INK)
        self.multi_cell(0, 5, text)
        self.ln(0.5)

    def para(self, text: str, size: float = 8.8, color=INK, style: str = ""):
        self.set_font(FONT, style, size)
        self.set_text_color(*color)
        self.multi_cell(0, size * 0.52, text, markdown=True)
        self.ln(1)

    def note(self, text: str):
        self.para(text, size=7.6, color=MUTED)

    def box(self, text: str):
        """Shaded reading box, like the "Reading" callouts of the web page."""
        self.set_fill_color(*LIGHT)
        self.set_font(FONT, "", 8.3)
        self.set_text_color(*INK)
        self.multi_cell(0, 4.4, text, fill=True, padding=2.5, markdown=True)
        self.ln(1.5)

    def table(
        self,
        header: list[str] | None,
        rows: list[list],
        widths: list[float],
        size: float = 7.4,
    ):
        """header=None: key/value table without a heading row."""
        if not rows:
            self.note("No data.")
            return
        self.set_font(FONT, "", size)
        self.set_text_color(*INK)
        self.set_draw_color(226, 223, 216)
        heading = FontFace(emphasis="BOLD", color=MUTED, fill_color=LIGHT)
        with super().table(
            col_widths=widths,
            headings_style=heading,
            first_row_as_headings=header is not None,
            line_height=size * 0.5,
            text_align="LEFT",
            padding=1.2,
            borders_layout="HORIZONTAL_LINES",
        ) as t:
            if header is not None:
                t.row([str(h) for h in header])
            for row in rows:
                t.row([_fmt(c) if not isinstance(c, str) else c for c in row])
        self.ln(2)

    def figure(self, png: io.BytesIO, width: float, caption: str | None = None):
        if self.get_y() > self.h - 70:
            self.add_page()
        x = (self.w - width) / 2
        self.image(png, x=x, w=width)
        if caption:
            self.note(caption)
        self.ln(1)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def _cover(pdf: Report, assessment: dict, raw: dict, synthesis: dict | None) -> None:
    target = assessment["target"]
    indication = assessment.get("indication")
    uni = raw.get("uniprot") or {}
    gene = raw.get("gene_info") or {}

    pdf.set_font(FONT, "B", 9)
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 5, "TARGETSCOPE · EVIDENCE-GROUNDED TARGET ASSESSMENT")
    pdf.ln(9)
    title = target["gene_symbol"] + (f" in {indication['disease_name']}" if indication else "")
    pdf.h1(title)
    pdf.note(
        f"Generated {assessment['generated_at'][:16].replace('T', ' ')} UTC · evidence engine "
        f"v{assessment['engine_version']} · "
        f"{sum(s['status'] != 'unavailable' for s in assessment['sources'])}/{len(assessment['sources'])} "
        "sources answered"
    )
    if synthesis and synthesis.get("headline"):
        pdf.ln(1)
        pdf.para(synthesis["headline"], size=11, style="I")

    pdf.h2("Identity")
    location = f"chr{gene.get('chromosome')} · {gene['band']}" if gene.get("band") else None
    rows = [
        ["Protein", uni.get("protein_name") or target.get("name")],
        [
            "Synonyms",
            ", ".join((uni.get("gene_synonyms") or []) + (uni.get("short_names") or [])) or None,
        ],
        ["Family", uni.get("family")],
        ["Length", f"{uni['protein_length']:,} aa" if uni.get("protein_length") else None],
        [
            "Gene",
            " · ".join(
                x
                for x in (
                    location,
                    f"{gene['exon_count']} exons" if gene.get("exon_count") else None,
                )
                if x
            )
            or None,
        ],
        [
            "Identifiers",
            " · ".join(
                x
                for x in (target.get("hgnc_id"), target.get("uniprot_id"), target.get("ensembl_id"))
                if x
            ),
        ],
    ]
    pdf.table(None, [[k, v or "—"] for k, v in rows], widths=[30, 148])


def _profile(pdf: Report, assessment: dict) -> None:
    pdf.h2("Evidence profile")
    pdf.note(
        "Six dimensions scored from 0 to 1 by explicit, versioned rules; n/s = not scorable "
        "(no data), which is different from a low score. There is deliberately no global score."
    )
    pdf.figure(_radar_png(assessment["scores"]), width=88)
    for s in assessment["scores"]:
        dim = s["dimension"]
        value = "n/s" if s["score"] is None else format(s["score"], "g")
        pdf.h3(f"{DIMENSION_TITLES[dim]} · {value} · {s['label']}")
        pdf.note(
            f"**What we look at** · {WHAT_WE_LOOK_AT[dim]} **Reading** · {READING_SUMMARY[dim]}"
        )
        if s.get("explanation"):
            pdf.para(s["explanation"], size=8.4)
        for fact in s.get("facts", [])[:4]:
            pdf.note(f"· {fact}")


def _summary(pdf: Report, assessment: dict, synthesis: dict | None) -> None:
    pdf.add_page()
    pdf.h2("Summary")
    if synthesis:
        for title, key in (
            ("Strengths", "strengths"),
            ("Risks", "risks"),
            ("Open questions", "open_questions"),
        ):
            claims = synthesis.get(key) or []
            if not claims:
                continue
            pdf.h3(title)
            for c in claims:
                ids = " ".join(f"[{e['id']}]" for e in c["evidence"])
                pdf.para(f"· {c['text']} {ids}", size=8.6)
                for e in c["evidence"]:
                    pdf.note(f"    {e['id']} ({e['kind']}) · {e['text']}")
        pdf.note(
            f"{synthesis.get('disclaimer', '')} Written by {synthesis.get('model', 'an LLM')}."
        )
    else:
        pdf.note(
            "AI summary not generated (no API key or the call failed). Everything below is computed without it."
        )

    pdf.h3("Evidence gaps")
    gaps = assessment["gaps"]
    pdf.table(
        ["Severity", "Gap", "Why it matters"],
        [[g["severity"].capitalize(), g["description"], g["why_it_matters"]] for g in gaps],
        widths=[18, 70, 90],
    ) if gaps else pdf.note("No evidence gap identified among the sources that answered.")

    pdf.h3("Potential safety considerations")
    signals = assessment["safety_signals"]
    pdf.table(
        ["Signal", "Source", "Caveat"],
        [[s["statement"], s["source"], s.get("caveat") or "—"] for s in signals],
        widths=[80, 40, 58],
    ) if signals else pdf.note(
        "No signal in the sources that answered. Absence of a signal never means the target is safe."
    )


def _genetics(pdf: Report, assessment: dict, raw: dict) -> None:
    pdf.add_page()
    pdf.h2("Genetics")
    ot = raw.get("open_targets") or {}
    ind = ot.get("indication")
    if ind:
        scores = {"Overall": ind.get("direct_score")} | {
            DATATYPE_LABELS.get(k, k): v
            for k, v in (ind.get("direct_datatype_scores") or {}).items()
            if v
        }
        pdf.h3(f"Open Targets evidence in {ind['disease_name']}")
        pdf.table(
            list(scores),
            [[_fmt(v) for v in scores.values()]],
            widths=[178 / len(scores)] * len(scores),
        )
        pdf.note(OT_SCORE_HELP)

    if ind and not is_oncology(ind.get("therapeutic_areas")):
        pdf.box(
            f"{ind['disease_name']} is not a cancer: the relevant genetics is germline (ClinVar below, "
            "association studies). Somatic mutation frequencies are context only."
        )
    items = list(raw.get("mutation_landscape") or [])
    if items:
        items.sort(key=lambda i: -i["frequency"])
        pdf.h3("How often is the gene mutated in cancers? (Cancer Gene Census, via Open Targets)")
        pdf.figure(
            _hbar_png(
                [
                    f"{_cut(i['disease_name'], 34)} ({i['mutated']:,}/{i['tested']:,})"
                    for i in items
                ],
                [i["frequency"] * 100 for i in items],
                "% of tumour samples with any mutation of the gene",
                threshold=10,
            ),
            width=150,
            caption="Dashed line: 10%, threshold for a major driver. Hotspot and scattered mutations are not separated.",
        )

    clinvar = raw.get("clinvar") or {}
    pdf.h3("Germline variants in ClinVar")
    if clinvar.get("total_variants") is None:
        pdf.note("ClinVar could not be reached.")
        return
    counts = clinvar.get("classification_counts") or {}
    pdf.note(
        f"{_fmt(clinvar['total_variants'])} variants · pathogenic {_fmt(counts.get('pathogenic'))} · "
        f"likely pathogenic {_fmt(counts.get('likely_pathogenic'))} · uncertain "
        f"{_fmt(counts.get('uncertain_significance'))} · benign {_fmt(counts.get('benign'))}"
    )
    variants = clinvar.get("top_variants") or []
    if variants:
        pdf.table(
            ["Protein change", "Classification", "Condition", "Review status"],
            [
                [
                    v.get("protein_change") or "—",
                    v.get("classification") or "—",
                    _cut(", ".join(c["name"] for c in v.get("conditions") or []), 70) or "—",
                    _cut(v.get("review_status"), 40) or "—",
                ]
                for v in variants[:MAX_ROWS]
            ],
            widths=[26, 32, 80, 40],
        )


def _clinical(pdf: Report, raw: dict) -> None:
    pdf.add_page()
    pdf.h2("Clinical & competition")
    chembl = raw.get("chembl") or {}
    comp = raw.get("competition") or {}
    programs = comp.get("programs") or []
    developers = {p.get("molecule_chembl_id"): p.get("main_industry_sponsor") for p in programs}
    drugs = chembl.get("drugs") or []
    pdf.h3("Drugs acting on the target (ChEMBL)")
    pdf.table(
        ["Drug", "Developer", "Phase", "Modality", "Mechanism", "On the protein itself"],
        [
            [
                d.get("name") or d.get("molecule_chembl_id"),
                developers.get(d.get("molecule_chembl_id")) or "—",
                d.get("phase_label") or "Preclinical",
                d.get("molecule_type") or "—",
                (d.get("action_type") or "—").capitalize(),
                "Yes"
                if d.get("via_target_type") == "SINGLE PROTEIN"
                else f"No ({(d.get('via_target_type') or '?').lower()})",
            ]
            for d in drugs[:MAX_ROWS]
        ],
        widths=[28, 40, 22, 28, 24, 36],
    ) if drugs else pdf.note("No drug with a known mechanism on this target in ChEMBL.")
    pdf.note(
        f"Chemistry · {_fmt(chembl.get('activity_count'))} measured activities, "
        f"{_fmt(chembl.get('potent_activity_count'))} with pChEMBL ≥ 7 (≤ 100 nM). Developer: main "
        "industry sponsor of the drug's trials (ChEMBL does not record companies)."
    )

    if not programs:
        pdf.box(
            "No drug on this target has reached the clinic: the field is open, but nothing is de-risked in humans."
        )
        return
    pdf.h3("Competitive landscape (ClinicalTrials.gov)")
    pdf.table(
        [
            "Approved drugs",
            "Clinical programs",
            "Trials analysed",
            "Active trials",
            "Academic share",
        ],
        [
            [
                comp.get("approved_count", 0),
                len(programs),
                comp.get("trial_count"),
                comp.get("active_trial_count"),
                f"{comp['academic_trial_share']:.0%}"
                if comp.get("academic_trial_share") is not None
                else "—",
            ]
        ],
        widths=[35.6] * 5,
    )
    sponsors = comp.get("industry_sponsors") or []
    if sponsors:
        pdf.figure(
            _hbar_png(
                [_cut(s["name"], 40) for s in sponsors], [s["trials"] for s in sponsors], "trials"
            ),
            width=135,
            caption="Who is active? Industry sponsors by number of trials.",
        )
    reasons = comp.get("stop_reasons") or {}
    if reasons:
        pdf.note(
            "Why did trials stop? "
            + " · ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
            + " (keyword categories from the free-text reason)."
        )
    stopped = comp.get("stopped_trials") or []
    if stopped:
        pdf.h3("Stopped trials and their reasons (most recent)")
        pdf.table(
            ["Trial", "Drug", "Phase", "Category", "Reason given", "Sponsor"],
            [
                [
                    t["nct_id"],
                    ", ".join(t.get("target_drugs") or []) or "—",
                    t["phase"],
                    t["reason_category"],
                    _cut(t["why_stopped"], 70) or "—",
                    _cut(t["sponsor"], 34),
                ]
                for t in stopped[:MAX_ROWS]
            ],
            widths=[22, 20, 17, 19, 60, 40],
            size=7,
        )


def _biology(pdf: Report, raw: dict) -> None:
    pdf.add_page()
    pdf.h2("Biology")
    uni = raw.get("uniprot") or {}
    if uni.get("function"):
        pdf.h3("Function (UniProt)")
        pdf.para(_cut(uni["function"], 1500), size=8.4)
    if uni.get("pfam"):
        pdf.note("Domains (Pfam) · " + ", ".join(d.get("name") or d.get("id") for d in uni["pfam"]))
    pathways = (raw.get("reactome") or {}).get("pathways") or []
    if pathways:
        pdf.h3(f"Pathways (Reactome) · {len(pathways)}")
        pdf.note(" · ".join(p["name"] for p in pathways[:20]))
    partners = (raw.get("string") or {}).get("interactions") or []
    if partners:
        top = partners[:12]
        pdf.figure(
            _hbar_png(
                [p["partner"] for p in top], [p["score"] for p in top], "STRING combined score"
            ),
            width=120,
            caption="Main interaction partners (STRING).",
        )


def _expression(pdf: Report, profiles: dict, config: dict) -> None:
    """The three expression views of the web page, one figure each."""
    if profiles.get("found") is None:
        pdf.note("Human Protein Atlas could not be reached: expression profiles unavailable.")
        return
    pdf.h3("Where is the target expressed in a healthy body? (Human Protein Atlas)")
    tissues = (profiles.get("tissues") or [])[:15]
    critical = [c.lower() for c in config.get("safety", {}).get("critical_tissues") or []]
    if tissues:
        colors = [
            "#9A3412" if any(c in r["tissue"].lower() for c in critical) else "#6E9BCF"
            for r in tissues
        ]
        pdf.figure(
            _hbar_png(
                [r["tissue"] for r in tissues], [r["ntpm"] for r in tissues], "nTPM", colors=colors
            ),
            width=125,
            caption="Tissues (top 15, consensus of HPA and GTEx RNA-seq). nTPM: normalized transcripts "
            "per million. Rust: critical tissues for safety.",
        )
    sc = profiles.get("single_cells") or {}
    cells = (sc.get("cells") or [])[:15]
    if cells:
        pdf.figure(
            _hbar_png([r["cell"] for r in cells], [r["ncpm"] for r in cells], "nCPM"),
            width=125,
            caption=f"Single cell types (top 15, HPA single-cell atlas). Specificity: "
            f"{sc.get('specificity') or 'n/a'}. nCPM: normalized counts per million.",
        )
    immune = profiles.get("immune_cells") or []
    if immune:
        pdf.figure(
            _hbar_png(
                [f"{r['cell']} ({r['lineage']})" for r in immune],
                [r["ntpm"] for r in immune],
                "nTPM",
            ),
            width=125,
            caption="Blood immune cell types (HPA blood atlas), sorted.",
        )
    if not (tissues or cells or immune):
        pdf.note("No expression profile available.")


def _safety(pdf: Report, raw: dict, config: dict) -> None:
    pdf.add_page()
    pdf.h2("Models & safety")
    profiles = raw.get("hpa_profiles") or {}
    _expression(pdf, profiles, config)
    ot = raw.get("open_targets") or {}
    loeuf = (ot.get("genetic_constraint") or {}).get("loeuf")
    if loeuf is not None:
        threshold = config.get("safety", {}).get("loeuf_intolerant", 0.35)
        reading = (
            "among the most constrained human genes: full or prolonged inhibition may not be tolerated"
            if loeuf < threshold
            else "moderately constrained"
            if loeuf < 1
            else "tolerant to loss of one copy"
        )
        pdf.box(f"**Tolerance to loss in humans (LOEUF, gnomAD)** · {loeuf:.2f}: {reading}.")

    mgi = raw.get("mgi") or {}
    if mgi.get("found"):
        lethal = mgi.get("lethality_phenotypes") or []
        others = [p for p in mgi.get("top_phenotypes") or [] if p not in lethal]
        pdf.h3(f"Mouse knockout phenotypes ({(mgi.get('mouse_gene') or {}).get('symbol', '')})")
        pdf.table(
            ["Phenotype", "Lethal", "Refs", "First reference"],
            [
                [
                    p["term"],
                    "Yes" if p in lethal else "",
                    p.get("reference_count", len(p.get("pmids") or [])),
                    (p.get("references") or [{}])[0].get("citation") or "—",
                ]
                for p in [*lethal, *others][:MAX_ROWS]
            ],
            widths=[78, 14, 12, 74],
        )
        models = mgi.get("disease_models") or []
        pdf.note(
            "Mouse models of disease · "
            + (
                ", ".join(f"{m['disease']} ({m['model_count']})" for m in models[:8])
                or "none annotated"
            )
        )

    liabilities = ot.get("safety_liabilities") or []
    if liabilities:
        pdf.h3("Documented safety liabilities (Open Targets)")
        pdf.table(
            ["Event", "Sources"],
            [[e["event"], ", ".join(e.get("datasources") or [])] for e in liabilities[:MAX_ROWS]],
            widths=[110, 68],
        )


def _toolbox(pdf: Report, raw: dict) -> None:
    pdf.h2("Tool Box")
    uni = raw.get("uniprot") or {}
    af = raw.get("alphafold") or {}
    structures = uni.get("structures") or []
    length = uni.get("protein_length")

    def cover(s):
        return (
            (s["end"] - s["start"] + 1) / length
            if length and s.get("start") and s.get("end")
            else None
        )

    # full-length structures first (like the web viewer), then by resolution
    ranked = sorted(structures, key=lambda s: ((cover(s) or 0) < 0.9, s.get("resolution") or 99))
    if structures:
        pdf.h3(f"Experimental structures (PDB) · {len(structures)}")
        pdf.table(
            ["PDB", "Method", "Resolution (Å)", "Residues", "Coverage"],
            [
                [
                    s["pdb_id"],
                    s.get("method") or "—",
                    s.get("resolution"),
                    f"{s['start']}-{s['end']}" if s.get("start") else "—",
                    f"{cover(s):.0%}" if cover(s) is not None else "—",
                ]
                for s in ranked[:MAX_ROWS]
            ],
            widths=[24, 30, 34, 50, 40],
        )
    if af.get("found"):
        pdf.note(
            f"AlphaFold model {af.get('model_id') or ''} · mean pLDDT {af['mean_plddt']:.0f}/100, "
            f"{(af.get('fraction_very_high') or 0):.0%} of residues with very high confidence."
        )
    extras = raw.get("ot_extras") or {}
    probes = extras.get("chemical_probes")
    if probes:
        pdf.h3("Chemical probes")
        pdf.table(
            ["Probe", "High quality", "Mechanism", "Control"],
            [
                [
                    p.get("name") or "—",
                    "Yes" if p.get("high_quality") else "No",
                    p.get("mechanism") or "—",
                    p.get("control") or "—",
                ]
                for p in probes[:MAX_ROWS]
            ],
            widths=[50, 25, 63, 40],
        )
    elif probes is not None:
        pdf.note("Chemical probes · none listed by Open Targets.")
    antibodies = (raw.get("hpa_profiles") or {}).get("antibodies") or []
    if antibodies:
        pdf.note("Antibodies used by the Human Protein Atlas · " + ", ".join(antibodies[:10]))


SOURCE_NAMES = {
    "open_targets": "Open Targets Platform", "chembl": "ChEMBL", "clinvar": "ClinVar", "uniprot": "UniProt",
    "pubmed": "PubMed", "europe_pmc": "Europe PMC", "reactome": "Reactome", "string": "STRING",
    "mgi": "MGI / Alliance of Genome Resources", "hpa": "Human Protein Atlas", "clinicaltrials": "ClinicalTrials.gov",
    "gene_info": "MyGene.info (NCBI Gene)", "alphafold": "AlphaFold DB", "hpa_profiles": "Human Protein Atlas (profiles)",
    "ot_extras": "Open Targets (extras)", "competition": "ClinicalTrials.gov (per drug)",
}  # fmt: skip
STATUS = {"ok": "Answered", "no_data": "Answered, no data", "unavailable": "Unavailable"}


def _sources(pdf: Report, assessment: dict, raw: dict) -> None:
    pdf.h2("Sources")
    pdf.table(
        ["Source", "Status"],
        [
            [SOURCE_NAMES.get(s["source"], s["source"]), STATUS.get(s["status"], s["status"])]
            for s in assessment["sources"]
        ],
        widths=[110, 68],
    )
    pdf.note(
        "A source marked unavailable did not answer: its dimensions may be underestimated. Open Targets "
        "Platform data are CC0; each source keeps its own licence (see the README). " + DISCLAIMER
    )
    pdf.h3(f"Key links for {assessment['target']['gene_symbol']}")
    for x in key_links(assessment, raw):
        pdf.set_font(FONT, "", 7.4)
        pdf.set_text_color(*INK)
        pdf.multi_cell(
            0,
            3.8,
            f"**{x['resource']}** · {x['use']}",
            markdown=True,
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_text_color(*NAVY)
        pdf.multi_cell(0, 3.8, x["url"], link=x["url"], new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.6)
    pubs = cited_publications(assessment, raw)
    pdf.h3("Publications cited in this report")
    if pubs:
        pdf.table(
            ["Reference", "Supports"],
            [[x["reference"], x["supports"]] for x in pubs[:25]],
            widths=[70, 108],
        )
    else:
        pdf.note("No publication cited directly.")
    pdf.note(
        "Most scores come from databases that aggregate many papers: the papers behind a genetics or "
        "drug score are on the Open Targets evidence page (key links above)."
    )
    pdf.h3("How to cite the databases")
    for name, citation in DATABASE_CITATIONS:
        pdf.note(f"**{name}** · {citation}")


# ---------------------------------------------------------------------------
def build_pdf(assessment: dict, raw: dict, synthesis: dict | None, config: dict) -> bytes:
    """The full report as PDF bytes, ready for st.download_button."""
    indication = assessment.get("indication")
    title = assessment["target"]["gene_symbol"] + (
        f" × {indication['disease_name']}" if indication else ""
    )
    pdf = Report(title)
    pdf.set_title(f"TargetScope · {title}")
    pdf.set_author("TargetScope")
    pdf.set_creation_date(datetime.now())
    pdf.add_page()
    _cover(pdf, assessment, raw, synthesis)
    _profile(pdf, assessment)
    _summary(pdf, assessment, synthesis)
    _genetics(pdf, assessment, raw)
    _clinical(pdf, raw)
    _biology(pdf, raw)
    _safety(pdf, raw, config)
    _toolbox(pdf, raw)
    _sources(pdf, assessment, raw)
    return bytes(pdf.output())


def file_name(assessment: dict) -> str:
    indication = assessment.get("indication")
    parts = [assessment["target"]["gene_symbol"]]
    if indication:
        parts.append("".join(c if c.isalnum() else "_" for c in indication["disease_name"])[:40])
    return "TargetScope_" + "_".join(parts) + f"_{assessment['generated_at'][:10]}.pdf"
