# frontend/links.py
"""Useful links, cited publications and database references for one target.

Shared by the Sources tab and the PDF report, so both list the same things.
Links are built from the identifiers already resolved (no extra API call).
"""

from urllib.parse import quote

# Reference publication of each database, as their "how to cite" pages ask.
# Check the latest version on each site before publishing: most databases
# publish an update paper every one or two years (NAR database issue).
DATABASE_CITATIONS = [
    ("Open Targets Platform", "Ochoa D et al. The next-generation Open Targets Platform: reimagined, redesigned, rebuilt. Nucleic Acids Res. 2023."),
    ("ChEMBL", "Zdrazil B et al. The ChEMBL Database in 2023: a drug discovery platform spanning multiple bioactivity data types and time periods. Nucleic Acids Res. 2024."),
    ("UniProt", "The UniProt Consortium. UniProt: the Universal Protein Knowledgebase in 2025. Nucleic Acids Res. 2025."),
    ("ClinVar", "Landrum MJ et al. ClinVar: improving access to variant interpretations and supporting evidence. Nucleic Acids Res. 2018."),
    ("Human Protein Atlas", "Uhlén M et al. Tissue-based map of the human proteome. Science. 2015."),
    ("gnomAD (constraint)", "Karczewski KJ et al. The mutational constraint spectrum quantified from variation in 141,456 humans. Nature. 2020."),
    ("Cancer Gene Census (COSMIC)", "Sondka Z et al. The COSMIC Cancer Gene Census: describing genetic dysfunction across all human cancers. Nat Rev Cancer. 2018."),
    ("STRING", "Szklarczyk D et al. The STRING database in 2023: protein-protein association networks and functional enrichment analyses for any sequenced genome of interest. Nucleic Acids Res. 2023."),
    ("Reactome", "Milacic M et al. The Reactome Pathway Knowledgebase 2024. Nucleic Acids Res. 2024."),
    ("AlphaFold DB", "Varadi M et al. AlphaFold Protein Structure Database in 2024: providing structure coverage for over 214 million protein sequences. Nucleic Acids Res. 2024."),
    ("Alliance of Genome Resources / MGI", "Alliance of Genome Resources Consortium. Updates to the Alliance of Genome Resources central infrastructure. Genetics. 2024."),
    ("MyGene.info", "Xin J et al. High-performance web services for querying gene and variant annotation. Genome Biol. 2016."),
    ("ClinicalTrials.gov, PubMed, Europe PMC", "Cite the website and the date of access."),
]  # fmt: skip


def key_links(assessment: dict, raw: dict) -> list[dict]:
    """[{resource, use, url}] for the target, most useful first. Only links
    whose identifier is known are returned."""
    t = assessment["target"]
    symbol = t["gene_symbol"]
    ensg, uniprot, entrez = t.get("ensembl_id"), t.get("uniprot_id"), t.get("entrez_id")
    indication = assessment.get("indication")
    chembl_target = (raw.get("chembl") or {}).get("target_chembl_id") or t.get("chembl_id")
    mgi_gene = (raw.get("mgi") or {}).get("mouse_gene") or {}
    s = quote(symbol)

    links = [
        ("Open Targets · target", "All evidence, tractability, safety", ensg and f"https://platform.opentargets.org/target/{ensg}"),
        (
            f"Open Targets · {indication['disease_name']}" if indication else None,
            "Every piece of evidence for this target-disease pair, with the papers",
            indication and ensg and f"https://platform.opentargets.org/evidence/{ensg}/{indication['disease_id']}",
        ),
        ("UniProt", "Function, sequence, features, structures", uniprot and f"https://www.uniprot.org/uniprotkb/{uniprot}/entry"),
        ("NCBI Gene", "Gene summary, transcripts, GeneRIFs", entrez and f"https://www.ncbi.nlm.nih.gov/gene/{entrez}"),
        ("Human Protein Atlas", "Tissue, cell and subcellular expression, antibodies", ensg and f"https://www.proteinatlas.org/{ensg}"),
        ("ChEMBL · target", "Compounds and measured activities", chembl_target and f"https://www.ebi.ac.uk/chembl/target_report_card/{chembl_target}/"),
        ("ClinicalTrials.gov", "Trials mentioning the gene", f"https://clinicaltrials.gov/search?term={s}"),
        ("ClinVar", "Germline variants and classifications", f"https://www.ncbi.nlm.nih.gov/clinvar/?term={s}%5Bgene%5D"),
        ("COSMIC", "Somatic mutations in cancer, hotspots", f"https://cancer.sanger.ac.uk/cosmic/gene/analysis?ln={s}"),
        ("gnomAD", "Population variants and constraint (LOEUF)", ensg and f"https://gnomad.broadinstitute.org/gene/{ensg}?dataset=gnomad_r4"),
        ("DepMap", "Dependency of cancer cell lines on the gene (CRISPR)", f"https://depmap.org/portal/gene/{s}"),
        ("AlphaFold DB", "Predicted structure", uniprot and f"https://alphafold.ebi.ac.uk/entry/{uniprot}"),
        ("PDBe-KB", "Experimental structures, ligands and binding sites, by residue", uniprot and f"https://www.ebi.ac.uk/pdbe/pdbe-kb/proteins/{uniprot}"),
        ("STRING", "Interaction network", f"https://string-db.org/cgi/network?identifiers={s}&species=9606"),
        ("Reactome", "Pathways", uniprot and f"https://reactome.org/content/query?q={uniprot}&species=Homo+sapiens"),
        ("MGI", "Mouse alleles and phenotypes", mgi_gene.get("url")),
        ("PubMed", "All publications on the gene", f"https://pubmed.ncbi.nlm.nih.gov/?term={s}"),
    ]  # fmt: skip
    return [{"resource": r, "use": u, "url": url} for r, u, url in links if r and url]


def _pubmed(pmid: str) -> str:
    return f"https://pubmed.ncbi.nlm.nih.gov/{pmid.removeprefix('PMID:').strip()}"


def cited_publications(assessment: dict, raw: dict) -> list[dict]:
    """Papers TargetScope itself points to: [{reference, supports, url}].

    Most scores come from databases that aggregate many papers (Open Targets,
    ChEMBL...): those papers are reachable from the "key links", not listed here.
    """
    seen: dict[str, dict] = {}

    def add(ref: str, supports: str, url: str | None, key: str | None = None) -> None:
        # key: the reference id, so one paper cited for several phenotypes is listed once
        entry = seen.setdefault(key or ref, {"reference": ref, "supports": [], "url": url})
        if supports not in entry["supports"]:
            entry["supports"].append(supports)

    for pmid in (raw.get("uniprot") or {}).get("function_pmids") or []:
        ref = pmid if pmid.startswith("PMID:") else f"PMID:{pmid}"
        add(ref, "Protein function (UniProt)", _pubmed(ref))

    mgi = raw.get("mgi") or {}
    for pheno in mgi.get("top_phenotypes") or []:
        for r in pheno.get("references") or []:
            add(r["citation"], f"Mouse phenotype: {pheno['term']} (MGI)", r.get("url"), key=r["id"])

    for signal in assessment.get("safety_signals") or []:
        for ref in signal.get("references") or []:
            if ref.startswith("PMID:") or ref.isdigit():
                pmid = ref if ref.startswith("PMID:") else f"PMID:{ref}"
                add(pmid, f"Safety: {signal['source']}", _pubmed(pmid))

    rows = list(seen.values())
    for r in rows:
        r["supports"] = "; ".join(r["supports"][:3]) + (" …" if len(r["supports"]) > 3 else "")
    return rows
