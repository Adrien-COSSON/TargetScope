# tests/test_v1_sources.py
"""Tests of the V1 additions: identity card, Tool Box, competitive landscape,
full HPA profiles, mutation landscape. Network replaced by fakes.

Fixture shapes copy real responses captured by scripts/explore_v1.py (XPO1).
"""

import asyncio

import pytest

from backend.services import (
    alphafold,
    clinicaltrials,
    competition,
    gene_info,
    hpa,
    open_targets,
    uniprot,
)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# UniProt: identity card and structures
# ---------------------------------------------------------------------------
XPO1_XREFS = [
    {
        "database": "PDB",
        "id": "2L1L",
        "properties": [
            {"key": "Method", "value": "NMR"},
            {"key": "Resolution", "value": "-"},
            {"key": "Chains", "value": "B=504-630"},
        ],
    },
    {
        "database": "PDB",
        "id": "1W9C",
        "properties": [
            {"key": "Method", "value": "X-ray"},
            {"key": "Resolution", "value": "2.30 A"},
            {"key": "Chains", "value": "A/B=707-1027"},
        ],
    },
    {
        "database": "PDB",
        "id": "3GB8",
        "properties": [
            {"key": "Method", "value": "X-ray"},
            {"key": "Resolution", "value": "2.90 A"},
            {"key": "Chains", "value": "A=1-1071"},
        ],
    },
    {"database": "Pfam", "id": "PF08767", "properties": [{"key": "EntryName", "value": "CRM1_C"}]},
    {"database": "AlphaFoldDB", "id": "O14980", "properties": []},
]


class TestUniprotCard:
    def test_structures_best_resolution_first_nmr_last(self):
        s = uniprot._parse_structures(XPO1_XREFS)
        assert [x["pdb_id"] for x in s] == ["1W9C", "3GB8", "2L1L"]
        assert (s[0]["resolution"], s[0]["start"], s[0]["end"]) == (2.3, 707, 1027)
        assert s[2]["resolution"] is None and s[2]["url"].endswith("/2L1L")

    def test_family_and_keywords(self):
        comments = [
            {"commentType": "SIMILARITY", "texts": [{"value": "Belongs to the exportin family."}]}
        ]
        assert uniprot._parse_family(comments) == "exportin family"
        kws = [
            {"category": "Molecular function", "name": "RNA-binding"},
            {"category": "Technical term", "name": "3D-structure"},
        ]
        assert uniprot._keywords_by_category(kws) == {"Molecular function": ["RNA-binding"]}

    def test_full_record(self, monkeypatch):
        async def fake(url, params=None, source="", not_found=None):
            return {
                "primaryAccession": "O14980",
                "proteinDescription": {
                    "recommendedName": {
                        "fullName": {"value": "Exportin-1"},
                        "shortNames": [{"value": "Exp1"}],
                    }
                },
                "genes": [{"geneName": {"value": "XPO1"}, "synonyms": [{"value": "CRM1"}]}],
                "comments": [],
                "keywords": [],
                "uniProtKBCrossReferences": XPO1_XREFS,
                "sequence": {"molWeight": 123386, "length": 1071},
            }

        monkeypatch.setattr(uniprot, "get_json", fake)
        r = run(uniprot.fetch_uniprot("O14980"))
        assert (r["protein_name"], r["gene_synonyms"], r["molecular_weight"]) == (
            "Exportin-1",
            ["CRM1"],
            123386,
        )
        assert r["pfam"] == [{"id": "PF08767", "name": "CRM1_C"}] and r["has_alphafold"] is True
        assert len(r["structures"]) == 3


# ---------------------------------------------------------------------------
# MyGene: chromosome band, exons
# ---------------------------------------------------------------------------
class TestGeneInfo:
    def test_fetch(self, monkeypatch):
        async def fake(url, params=None, source="", not_found=None):
            return {
                "map_location": "2p15",
                "type_of_gene": "protein-coding",
                "genomic_pos": {"chr": "2", "start": 61476032, "end": 61538753, "strand": -1},
                "exons": [{"transcript": "NM_003400", "position": [[1, 2]] * 25}],
            }

        monkeypatch.setattr(gene_info, "get_json", fake)
        r = run(gene_info.fetch_gene_info(7514))
        assert (r["band"], r["chromosome"], r["exon_count"]) == ("2p15", "2", 25)
        assert r["gene_length_bp"] == 62722

    def test_primary_locus_preferred(self):
        positions = [{"chr": "HSCHR6_MHC_COX_CTG1", "start": 1}, {"chr": "6", "start": 2}]
        assert gene_info._main_position(positions)["chr"] == "6"

    def test_missing_id_and_failure(self, monkeypatch):
        assert run(gene_info.fetch_gene_info(None)) == {"found": None}

        async def fail(url, params=None, source="", not_found=None):
            return None

        monkeypatch.setattr(gene_info, "get_json", fail)
        assert run(gene_info.fetch_gene_info(1)) == {"found": None}


# ---------------------------------------------------------------------------
# AlphaFold
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("answer, found", [([], False), (None, None)])
def test_alphafold_no_model_vs_failure(monkeypatch, answer, found):
    async def fake(url, params=None, source="", not_found=None):
        return answer

    monkeypatch.setattr(alphafold, "get_json", fake)
    assert run(alphafold.fetch_alphafold("O14980"))["found"] is found


def test_alphafold_model(monkeypatch):
    async def fake(url, params=None, source="", not_found=None):
        return [
            {
                "modelEntityId": "AF-O14980-F1",
                "globalMetricValue": 91.44,
                "pdbUrl": "https://alphafold.ebi.ac.uk/files/AF-O14980-F1-model_v6.pdb",
            }
        ]

    monkeypatch.setattr(alphafold, "get_json", fake)
    r = run(alphafold.fetch_alphafold("O14980"))
    assert (r["mean_plddt"], r["model_id"]) == (91.44, "AF-O14980-F1")


# ---------------------------------------------------------------------------
# HPA XML profiles
# ---------------------------------------------------------------------------
HPA_XML = b"""<proteinAtlas><entry>
<proteinClasses><proteinClass name="Transporters"/>
<proteinClass name="Transporters"/></proteinClasses>
<antibody id="HPA042933"/>
<tissueExpression>
 <rnaExpression source="HPA" assayType="consensusTissue">
  <data><tissue organ="Liver">liver</tissue>
   <level type="normalizedRNAExpression" unitRNA="nTPM" expRNA="40.2"/>
   <level type="RNAExpression" unitRNA="TPM" expRNA="30"/></data>
  <data><tissue organ="Bone marrow">bone marrow</tissue>
   <level type="normalizedRNAExpression" unitRNA="nTPM" expRNA="92.1"/></data>
 </rnaExpression>
 <rnaExpression source="HPA" assayType="immuneCell">
  <data><immuneCell lineage="B-cells">naive B-cell</immuneCell>
   <level type="normalizedRNAExpression" unitRNA="nTPM" expRNA="12.5"/></data>
 </rnaExpression>
</tissueExpression>
<cellTypeExpression technology="scRNAseq" assayType="cell">
 <cellTypeSpecificity category="Low cell type specificity"/>
 <singleCellTypeExpression name="Hepatocytes" unitRNA="nCPM" expRNA="10.1"/>
 <singleCellTypeExpression name="B-cells" unitRNA="nCPM" expRNA="55.0"/>
</cellTypeExpression>
</entry></proteinAtlas>"""


class TestHpaProfiles:
    def test_parse(self):
        p = hpa.parse_hpa_xml(HPA_XML)
        assert [t["tissue"] for t in p["tissues"]] == ["bone marrow", "liver"]  # highest first
        assert p["tissues"][1] == {
            "tissue": "liver",
            "organ": "Liver",
            "ntpm": 40.2,
        }  # nTPM, not TPM
        assert p["immune_cells"] == [{"cell": "naive B-cell", "lineage": "B-cells", "ntpm": 12.5}]
        assert p["single_cells"]["specificity"] == "Low cell type specificity"
        assert p["single_cells"]["cells"][0] == {"cell": "B-cells", "ncpm": 55.0}
        assert p["protein_classes"] == ["Transporters"] and p["antibodies"] == ["HPA042933"]

    def test_fetch_failure_and_bad_xml(self, monkeypatch):
        async def fail(url, params=None, source=""):
            return None

        async def broken(url, params=None, source=""):
            return b"<not xml"

        monkeypatch.setattr(hpa, "get_bytes", fail)
        assert run(hpa.fetch_hpa_profiles("ENSG1")) == {"found": None}
        monkeypatch.setattr(hpa, "get_bytes", broken)
        assert run(hpa.fetch_hpa_profiles("ENSG1")) == {"found": None}


# ---------------------------------------------------------------------------
# Competitive landscape
# ---------------------------------------------------------------------------
def trial(
    nct,
    status,
    sponsor="Karyopharm",
    sponsor_class="INDUSTRY",
    why=None,
    conditions=("Multiple Myeloma",),
    phases=("PHASE2",),
    start="2022-01-01",
):
    return {
        "nct_id": nct,
        "title": f"Trial {nct}",
        "status": status,
        "why_stopped": why,
        "phases": list(phases),
        "start_date": start,
        "completion_date": "",
        "conditions": list(conditions),
        "sponsor": sponsor,
        "sponsor_class": sponsor_class,
        "interventions": ["Selinexor"],
        "enrollment": 20,
        "link": f"https://clinicaltrials.gov/study/{nct}",
    }


SELINEXOR = {
    "name": "SELINEXOR",
    "max_phase": 4.0,
    "phase_label": "Approved",
    "molecule_type": "Small molecule",
    "action_type": "INHIBITOR",
    "via_target_type": "SINGLE PROTEIN",
    "molecule_chembl_id": "CHEMBL3545185",
}
ELTANEXOR = {**SELINEXOR, "name": "ELTANEXOR", "max_phase": 2.0, "phase_label": "Phase 2"}


class TestCompetition:
    @pytest.mark.parametrize(
        "reason, category",
        [
            ("Funding Sponsor no longer supporting study", "Business"),
            ("The number of participants in the study did not meet expectations", "Enrollment"),
            ("Karyopharm has decided, given the competitive landscape, not to pursue", "Business"),
            ("Lack of efficacy at interim analysis", "Efficacy"),
            ("Unacceptable toxicity", "Safety"),
            ("PI left the institution", "Other"),
            (None, "Not given"),
        ],
    )
    def test_stop_reasons(self, reason, category):
        assert competition.classify_stop_reason(reason) == category

    def test_build(self):
        trials = {
            "SELINEXOR": {
                "total": 207,
                "trials": [
                    trial("NCT1", "RECRUITING", conditions=["B-Cell Chronic Lymphocytic Leukemia"]),
                    trial(
                        "NCT2",
                        "TERMINATED",
                        why="Funding Sponsor no longer supporting study",
                        sponsor="UTSW",
                        sponsor_class="OTHER",
                        start="2017-01-01",
                    ),
                    trial("NCT3", "COMPLETED"),
                ],
            },
            "ELTANEXOR": {
                "total": 1,
                "trials": [
                    trial("NCT1", "RECRUITING", conditions=["B-Cell Chronic Lymphocytic Leukemia"])
                ],
            },
        }
        land = competition.build_landscape(
            [SELINEXOR, ELTANEXOR], trials, ["chronic lymphocytic leukemia", "CLL"]
        )
        assert land["trial_count"] == 3  # NCT1 counted once (combination trial)
        assert land["approved_count"] == 1 and land["active_trial_count"] == 1
        assert land["programs"][0]["trial_count"] == 207  # registry total, not the sample
        assert land["stop_reasons"] == {"Business": 1}
        assert land["stopped_trials"][0]["phase"] == "Phase 2"
        assert [t["nct_id"] for t in land["indication_trials"]] == ["NCT1"]
        assert land["industry_sponsors"] == [{"name": "Karyopharm", "trials": 2}]
        assert land["incomplete"] is False

    def test_failed_drug_is_unknown_not_zero(self):
        land = competition.build_landscape([SELINEXOR], {"SELINEXOR": None})
        assert land["programs"][0]["trial_count"] is None and land["incomplete"] is True
        assert land["indication_trials"] is None  # no indication chosen

    def test_fetch_without_chembl(self):
        assert run(competition.fetch_landscape(None)) == {"found": None}
        assert run(competition.fetch_landscape({"found": None}))["found"] is None

    def test_fetch_selects_clinical_drugs(self, monkeypatch):
        asked = []

        async def fake(name):
            asked.append(name)
            return {"total": 0, "trials": [], "truncated": False}

        monkeypatch.setattr(competition, "fetch_drug_trials", fake)
        preclinical = {**SELINEXOR, "name": "TOOL-1", "max_phase": None}
        r = run(competition.fetch_landscape({"found": True, "drugs": [SELINEXOR, preclinical]}))
        assert asked == ["SELINEXOR"] and r["found"] is True


def test_drug_trials_pagination_and_partial_failure(monkeypatch):
    pages = [
        {
            "totalCount": 3,
            "nextPageToken": "p2",
            "studies": [
                {
                    "protocolSection": {
                        "identificationModule": {"nctId": "NCT1"},
                        "statusModule": {"overallStatus": "TERMINATED", "whyStopped": "Business"},
                        "sponsorCollaboratorsModule": {
                            "leadSponsor": {"name": "K", "class": "INDUSTRY"}
                        },
                        "designModule": {"phases": ["PHASE3"]},
                    }
                }
            ],
        },
        None,  # second page fails
    ]

    async def fake(url, params=None, source="", not_found=None):
        return pages.pop(0)

    monkeypatch.setattr(clinicaltrials, "get_json", fake)
    r = run(clinicaltrials.fetch_drug_trials("selinexor"))
    assert r["total"] == 3 and r["truncated"] is True
    assert (
        r["trials"][0]["why_stopped"] == "Business"
        and r["trials"][0]["sponsor_class"] == "INDUSTRY"
    )


# ---------------------------------------------------------------------------
# Open Targets extras
# ---------------------------------------------------------------------------
class TestOtExtras:
    def test_probes_high_quality_first(self):
        probes = open_targets._parse_probes(
            [
                {"id": "P-low", "isHighQuality": False, "probesDrugsScore": 90, "urls": []},
                {
                    "id": "P-high",
                    "isHighQuality": True,
                    "probesDrugsScore": 50,
                    "mechanismOfAction": ["inhibitor"],
                    "urls": [{"niceName": "SGC", "url": "https://x"}],
                },
            ]
        )
        assert [p["name"] for p in probes] == ["P-high", "P-low"]
        assert probes[0]["mechanism"] == "inhibitor" and probes[0]["urls"][0]["name"] == "SGC"

    def test_one_failed_query_keeps_the_other(self, monkeypatch):
        async def fake(query, variables):
            if query is open_targets.PROBES_QUERY:
                return None
            return {
                "target": {
                    "isEssential": True,
                    "targetClass": [{"label": "Transporter", "level": "l1"}],
                }
            }

        monkeypatch.setattr(open_targets, "_graphql", fake)
        r = run(open_targets.fetch_ot_extras("ENSG1"))
        assert r["chemical_probes"] is None  # unknown, not "no probe"
        assert r["is_essential"] is True and r["target_class"][0]["label"] == "Transporter"

    def test_mutation_landscape(self, monkeypatch):
        async def fake(ensembl_id, disease_id):
            return {
                "CLL": {"disease_name": "CLL", "mutated": 180, "tested": 3865, "frequency": 0.047},
                "PMBL": {"disease_name": "PMBL", "mutated": 24, "tested": 100, "frequency": 0.24},
                "AML": False,
            }[disease_id]

        monkeypatch.setattr(open_targets, "_mutation_frequency", fake)
        assoc = [
            {"disease_id": d, "datatype_scores": {"somatic_mutation": 0.5}}
            for d in ("CLL", "PMBL", "AML")
        ]
        assoc.append(
            {"disease_id": "MM", "datatype_scores": {"literature": 0.9}}
        )  # no somatic: skipped
        r = run(open_targets.fetch_mutation_landscape("ENSG1", assoc))
        assert [x["disease_name"] for x in r] == ["PMBL", "CLL"]
        assert run(open_targets.fetch_mutation_landscape("ENSG1", None)) is None


# ---------------------------------------------------------------------------
# Review fixes: MGI references, ClinVar protein change, drugs of stopped trials
# ---------------------------------------------------------------------------
from backend.services.clinvar import _protein_change  # noqa: E402
from backend.services.mgi import _parse_phenotypes  # noqa: E402


def test_mgi_pmid_from_cross_references():
    record = {
        "primaryAnnotations": [
            {
                "evidenceItem": {
                    "curie": "AGRKB:1",
                    "crossReferences": [
                        {"referencedCurie": "DOI:10.1/x"},
                        {"referencedCurie": "MGI:3810445"},
                        {"referencedCurie": "PMID:17669881"},
                    ],
                },
                "phenotypeTerms": [{"curie": "MP:0001", "name": "embryonic lethality"}],
            }
        ]
    }
    [pheno] = _parse_phenotypes([record])
    assert pheno["pmids"] == ["PMID:17669881"]
    assert pheno["reference_count"] == 1


def test_mgi_only_reference_like_xpo1():
    """Real Xpo1 payload shape: MGI cross-references only, a short citation."""
    record = {
        "primaryAnnotations": [
            {
                "evidenceItem": {
                    "curie": "AGRKB:1",
                    "crossReferences": [{"referencedCurie": "MGI:3810445"}],
                    "referenceID": "MGI:3810445",
                    "pubModID": "MGI:3810445",
                    "shortCitation": "Smith J, et al. (2009)",
                },
                "phenotypeTerms": [{"curie": "MP:0001", "name": "embryonic lethality"}],
            }
        ]
    }
    [pheno] = _parse_phenotypes([record])
    assert pheno["pmids"] == []
    assert pheno["reference_count"] == 1
    assert pheno["references"] == [
        {
            "id": "MGI:3810445",
            "citation": "Smith J, et al. (2009)",
            "url": "https://www.informatics.jax.org/reference/MGI:3810445",
        }
    ]


def test_protein_change_prefers_esummary_field():
    assert _protein_change({"protein_change": "E571K, E554K"}) == "E571K"


def test_protein_change_from_hgvs_title():
    title = "NM_003400.4(XPO1):c.1711G>A (p.Glu571Lys)"
    assert _protein_change({"protein_change": "", "title": title}) == "E571K"
    assert _protein_change({"title": "NM_1.1(G):c.10C>T (p.Arg4Ter)"}) == "R4*"


def test_protein_change_none_for_non_coding():
    assert _protein_change({"title": "NM_003400.4(XPO1):c.100-5A>G"}) is None


def test_stopped_trials_name_the_drug():
    drugs = [
        {"name": "SELINEXOR", "max_phase": 4, "via_target_type": "SINGLE PROTEIN"},
        {"name": "ELTANEXOR", "max_phase": 2, "via_target_type": "SINGLE PROTEIN"},
    ]
    trial = {
        "nct_id": "NCT1",
        "title": "t",
        "status": "TERMINATED",
        "why_stopped": "sponsor decision",
        "phases": ["PHASE2"],
        "start_date": "2024",
        "conditions": [],
        "sponsor": "Hospital",
        "sponsor_class": "OTHER",
        "interventions": [],
        "link": "l",
    }
    landscape = competition.build_landscape(
        drugs,
        {
            "SELINEXOR": {"total": 1, "trials": [trial]},
            "ELTANEXOR": {"total": 1, "trials": [trial]},
        },
    )
    [stopped] = landscape["stopped_trials"]
    assert stopped["target_drugs"] == ["Selinexor", "Eltanexor"]
    assert stopped["sponsor_class"] == "OTHER"
