# tests/test_evidence_engine.py
"""Tests of the evidence engine (pure functions, no network).

Three groups:
  1. one class per dimension: tiers, fallbacks, gaps
  2. cross-cutting rules: three states (evidence / gap / unavailable), safety
  3. reference cases from real runs, frozen as non-regression tests:
     if a CONFIG change moves them, the change must be deliberate
"""

from datetime import date

import pytest

from backend.services import evidence_engine as E
from tests import factories as f


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
class TestHelpers:
    @pytest.mark.parametrize(
        "score, label",
        [(None, "Unavailable"), (1.0, "Strong"), (0.8, "Strong"), (0.6, "Moderate"),
         (0.2, "Limited"), (0.0, "None")],
    )
    def test_label_for(self, score, label):
        assert E.label_for(score) == label

    def test_name_key_ignores_word_order(self):
        assert E._name_key("lung non-small cell carcinoma") == E._name_key("non-small cell lung carcinoma")
        assert E._name_key("lung cancer") != E._name_key("non-small cell lung carcinoma")

    def test_tiered(self):
        tiers = [(10, 1.0), (1, 0.5)]
        assert (E._tiered(20, tiers), E._tiered(3, tiers), E._tiered(0, tiers), E._tiered(None, tiers)) \
            == (1.0, 0.5, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Tractability
# ---------------------------------------------------------------------------
class TestTractability:
    def test_approved_selective_drug_is_strong(self):
        s, gaps = E.score_tractability(f.chembl([f.drug("ERLOTINIB", 4.0)], 21342, 11544),
                                       f.open_targets())
        assert (s.score, s.label, gaps) == (1.0, "Strong", [])

    def test_selective_clinical_candidate(self):
        s, _ = E.score_tractability(f.chembl([f.drug("X", 2.0)]), f.open_targets())
        assert s.score == 0.8

    def test_non_selective_drug_ranks_below_potent_chemistry(self):
        family_only = f.chembl([f.drug("VANDETANIB", 4.0, via="PROTEIN FAMILY")], 50, 5)
        s, _ = E.score_tractability(family_only, f.open_targets())
        assert s.score == 0.5

        family_and_potent = f.chembl([f.drug("VANDETANIB", 4.0, via="PROTEIN FAMILY")], 900, 300)
        s, _ = E.score_tractability(family_and_potent, f.open_targets())
        assert s.score == 0.6
        assert "only via a protein family" in s.explanation

    def test_predicted_only_creates_medium_gap(self):
        s, gaps = E.score_tractability(f.chembl(found=False),
                                       f.open_targets(tractability={"Small molecule": ["Structure with Ligand"]}))
        assert s.score == 0.2
        assert [g.severity for g in gaps] == ["medium"]

    def test_chembl_down_uses_open_targets_clinical_buckets(self):
        s, gaps = E.score_tractability({"found": None},
                                       f.open_targets(tractability={"Small molecule": ["Approved Drug"]}))
        assert s.score == 1.0
        assert gaps == []  # no gap when a source is down

    def test_both_sources_down_is_unavailable_not_zero(self):
        s, gaps = E.score_tractability({"found": None}, {"found": None})
        assert (s.score, s.label, gaps) == (None, "Unavailable", [])

    def test_selective_vs_family_fact(self):
        s, _ = E.score_tractability(
            f.chembl([f.drug("A", 4.0), f.drug("B", 4.0, via="PROTEIN COMPLEX")]), f.open_targets())
        assert "1 drug acting on the protein itself, 1 drug only via a family or complex" in s.facts


# ---------------------------------------------------------------------------
# Human genetics
# ---------------------------------------------------------------------------
class TestHumanGenetics:
    def test_no_indication_uses_best_overall(self):
        ot = f.open_targets(top=[f.assoc("CLL", somatic_mutation=0.77)])
        s, gaps = E.score_human_genetics(ot, None, None)
        assert (s.score, gaps) == (0.7, [])

    def test_mutation_frequency_lifts_a_major_driver(self):
        ind = f.indication("pancreatic ductal adenocarcinoma", direct={"literature": 0.99},
                           mutation={"disease_name": "PDAC", "mutated": 6582, "tested": 9331,
                                     "frequency": 0.705})
        s, _ = E.score_human_genetics(f.open_targets(ind=ind), None, None)
        assert s.score == 1.0
        assert "70%" in s.explanation

    def test_parent_tumour_type_is_a_fallback(self):
        ind = f.indication("pancreatic ductal adenocarcinoma", direct={"literature": 0.99},
                           fallback=[f.assoc("pancreatic adenocarcinoma", somatic_mutation=0.80)])
        s, _ = E.score_human_genetics(f.open_targets(ind=ind), None, None)
        assert s.score == 1.0
        assert any("[parent tumour type]" in fact for fact in s.facts)

    def test_genetics_elsewhere_only_is_a_high_gap(self):
        ot = f.open_targets(top=[f.assoc("CLL", somatic_mutation=0.77)],
                            ind=f.indication("cystic fibrosis", has_evidence=False))
        s, gaps = E.score_human_genetics(ot, None, None)
        assert s.score == 0.2
        assert [g.severity for g in gaps] == ["high"]

    def test_clinvar_only_when_open_targets_down(self):
        clinvar = {"total_variants": 100, "classification_counts": {"pathogenic": 3}}
        s, gaps = E.score_human_genetics({"found": None}, clinvar, None)
        assert s.score == 0.4
        assert gaps == []
        assert "underestimated" in s.explanation


# ---------------------------------------------------------------------------
# Clinical precedence
# ---------------------------------------------------------------------------
class TestClinicalPrecedence:
    def test_approved_without_indication(self):
        s, _ = E.score_clinical_precedence(f.chembl([f.drug("SELINEXOR", 4.0)]), f.open_targets(), None)
        assert s.score == 1.0

    def test_clinical_evidence_in_indication(self):
        ot = f.open_targets(ind=f.indication("CLL", direct={"clinical": 0.08}))
        s, _ = E.score_clinical_precedence(f.chembl([f.drug("SELINEXOR", 4.0)]), ot, None)
        assert s.score == 0.7

    def test_approved_elsewhere_is_repurposing(self):
        ot = f.open_targets(ind=f.indication("cystic fibrosis"))
        s, _ = E.score_clinical_precedence(f.chembl([f.drug("SELINEXOR", 4.0)]), ot, None)
        assert s.score == 0.6
        assert "repurposing" in s.explanation

    def test_novel_target_is_a_low_gap(self):
        s, gaps = E.score_clinical_precedence(f.chembl(), f.open_targets(), {"total": None})
        assert s.score == 0.0
        assert [g.severity for g in gaps] == ["low"]

    def test_trial_mentions_only(self):
        s, _ = E.score_clinical_precedence(f.chembl(), f.open_targets(), {"total": 12})
        assert s.score == 0.1


# ---------------------------------------------------------------------------
# Biological knowledge and literature trend
# ---------------------------------------------------------------------------
class TestBiologicalKnowledge:
    def test_well_studied_target(self):
        s, gaps = E.score_biological_knowledge(
            {"total_count": 31800, "recent_count": 14693, "recent_window": "2022-2026"},
            {"pathways": [{"name": "p"}] * 37},
            {"interactions": [{"partner": "GRB2", "score": 0.999}] * 10},
            {"found": True, "function": "Receptor tyrosine kinase"},
        )
        assert (s.score, gaps) == (1.0, [])
        assert "crowded" in s.explanation

    def test_obscure_target_is_a_low_gap(self):
        s, gaps = E.score_biological_knowledge(
            {"total_count": 12, "recent_count": 5, "recent_window": "x"},
            {"pathways": []}, {"interactions": []}, {"found": True, "function": None})
        assert s.score < 0.25
        assert [g.severity for g in gaps] == ["low"]

    def test_failed_sources_are_excluded_and_reported(self):
        s, _ = E.score_biological_knowledge(
            {"total_count": 800, "recent_count": 5, "recent_window": "x"}, None, None, {"found": None})
        assert s.score == 0.5  # literature tier only, weights renormalised
        assert "Missing" in s.explanation

    def test_all_down_is_unavailable(self):
        s, _ = E.score_biological_knowledge(None, None, None, None)
        assert s.score is None


class TestLiteratureTrend:
    @staticmethod
    def timeline(previous, recent):
        year = date.today().year
        t = {str(y): previous for y in range(year - 10, year - 5)}
        t.update({str(y): recent for y in range(year - 5, year)})
        t[str(year)] = 1  # current, partial year: must be ignored
        return {"timeline": t}

    @pytest.mark.parametrize(
        "previous, recent, trend",
        [(7000, 8000, "Stable"), (100, 300, "Growing"), (2, 30, "Emerging"),
         (0, 10, "Emerging"), (500, 300, "Declining"), (0, 0, "Unknown")],
    )
    def test_trend(self, previous, recent, trend):
        assert E.literature_trend(None, self.timeline(previous, recent)).trend == trend

    def test_failed_year_gives_no_trend(self):
        t = self.timeline(10, 10)
        t["timeline"][str(date.today().year - 2)] = None
        assert E.literature_trend(None, t).trend == "Unknown"


# ---------------------------------------------------------------------------
# Animal models
# ---------------------------------------------------------------------------
class TestAnimalModels:
    def test_mouse_model_of_the_indication(self):
        ind = f.indication("non-small cell lung carcinoma")
        mouse = f.mgi("Kras", 205, disease_models=[("lung non-small cell carcinoma", 9)])
        s, _ = E.score_animal_models(mouse, f.open_targets(ind=ind))
        assert s.score == 1.0  # word order differs between ontologies

    def test_synonym_match(self):
        ind = f.indication("pancreatic ductal adenocarcinoma", synonyms=["PDAC"])
        mouse = f.mgi("Kras", 205, disease_models=[("PDAC", 3)])
        s, _ = E.score_animal_models(mouse, f.open_targets(ind=ind))
        assert s.score == 1.0

    def test_well_characterised_but_not_mapped(self):
        ind = f.indication("pancreatic ductal adenocarcinoma")
        s, _ = E.score_animal_models(f.mgi("Kras", 205), f.open_targets(ind=ind))
        assert s.score == 0.6

    def test_no_ortholog_is_a_medium_gap(self):
        s, gaps = E.score_animal_models(f.mgi(found=False), f.open_targets())
        assert s.score == 0.0
        assert [g.severity for g in gaps] == ["medium"]


# ---------------------------------------------------------------------------
# Disease expression
# ---------------------------------------------------------------------------
class TestDiseaseExpression:
    def test_needs_an_indication(self):
        s, gaps = E.score_disease_expression(f.open_targets(), None)
        assert (s.score, gaps) == (None, [])

    def test_scored_when_data_exist(self):
        ot = f.open_targets(ind=f.indication("CLL", direct={"rna_expression": 0.35}))
        s, _ = E.score_disease_expression(ot, None)
        assert s.score == 0.6

    def test_missing_data_is_not_scored_and_not_a_gap(self):
        ot = f.open_targets(ind=f.indication("NSCLC", direct={"clinical": 0.9}))
        s, gaps = E.score_disease_expression(ot, None)
        assert (s.score, gaps) == (None, [])

    def test_hpa_cancer_context_only_in_oncology(self):
        context = f.hpa(cancer_specificity="Low cancer specificity")
        onco = f.open_targets(ind=f.indication("CLL", direct={"rna_expression": 0.3}))
        immuno = f.open_targets(ind=f.indication("rheumatoid arthritis", direct={"rna_expression": 0.3},
                                                 areas=("immune system disorder",)))
        assert "HPA" in E.score_disease_expression(onco, context)[0].sources
        assert "HPA" not in E.score_disease_expression(immuno, context)[0].sources


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
class TestSafety:
    def test_clinical_liabilities_are_grouped_inhibition_first(self):
        ot = f.open_targets(safety=[
            {"event": "heart disease", "datasources": ["B"], "effects": []},
            {"event": "rash", "datasources": ["A"], "effects": [{"direction": "Inhibition/Decrease"}]},
        ])
        signals = E.safety_signals(ot, None, None, None)
        assert len(signals) == 1
        assert signals[0].statement.endswith("rash (inhibition), heart disease")

    def test_essential_and_broad_is_one_combined_signal(self):
        signals = E.safety_signals(f.open_targets(loeuf=0.08), f.mgi(lethal=["embryonic lethality"]),
                                   None, f.hpa())
        categories = [s.category for s in signals]
        assert categories == ["genetic_constraint", "mouse_phenotype", "tissue_expression"]
        assert "LoF-intolerant in humans, lethal in mouse models" in signals[-1].statement

    def test_tolerant_gene_has_no_constraint_signal(self):
        signals = E.safety_signals(f.open_targets(loeuf=1.1), None, None, None)
        assert signals == []

    def test_restricted_expression_is_not_a_signal_but_a_favourable_fact(self):
        restricted = f.hpa(specificity="Tissue enriched", distribution="Detected in single",
                           enriched={"pancreas": 900.0})
        assert E.safety_signals(None, None, None, restricted) == []
        assert "wider therapeutic window" in E.expression_note(restricted)

    def test_enrichment_in_a_critical_tissue(self):
        liver = f.hpa(specificity="Tissue enriched", distribution="Detected in single",
                      enriched={"liver": 412.0})
        signals = E.safety_signals(None, None, None, liver)
        assert [(s.category, s.tissue) for s in signals] == [("tissue_expression", "liver")]

    def test_drug_label_flags(self):
        signals = E.safety_signals(None, None, f.chembl([f.drug("CETUXIMAB", 4.0, black_box_warning=True)]),
                                   None)
        assert signals[0].category == "drug_label"
        assert "CETUXIMAB" in signals[0].statement


# ---------------------------------------------------------------------------
# Assembly and the three-state rule
# ---------------------------------------------------------------------------
class TestAssess:
    def test_all_sources_down_gives_no_gap(self):
        down = {"open_targets": {"found": None}, "chembl": {"found": None}, "uniprot": {"found": None},
                "pubmed": {"total_count": None}, "clinicaltrials": {"total": None}}
        a = E.assess(f.target(), down)
        assert all(s.score is None for s in a.scores)
        assert a.gaps == []
        assert {s.status for s in a.sources} == {"unavailable"}

    def test_radar_order_and_engine_version(self):
        a = E.assess(f.target(), {})
        assert [s.dimension for s in a.scores] == E.DIMENSIONS
        assert a.engine_version == E.ENGINE_VERSION

    def test_gaps_sorted_by_severity(self):
        ot = f.open_targets(top=[f.assoc("CLL", somatic_mutation=0.77)],
                            ind=f.indication("cystic fibrosis", has_evidence=False))
        a = E.assess(f.target(), {"open_targets": ot, "chembl": f.chembl(found=False),
                                  "mgi": f.mgi(found=False)})
        severities = [g.severity for g in a.gaps]
        assert severities == sorted(severities, key=["high", "medium", "low"].index)

    @pytest.mark.parametrize(
        "result, status",
        [(None, "unavailable"), ({"found": None}, "unavailable"), ({"found": False}, "no_data"),
         ({"found": True}, "ok"), ({"pathways": []}, "no_data"), ({"total": None}, "unavailable")],
    )
    def test_source_status(self, result, status):
        assert E._source_status("x", result).status == status


# ---------------------------------------------------------------------------
# Reference cases (real runs, 2026-11-02): non-regression
# ---------------------------------------------------------------------------
def kras_pdac_results():
    ind = f.indication(
        "pancreatic ductal adenocarcinoma", direct={"literature": 0.99},
        fallback=[f.assoc("pancreatic adenocarcinoma", somatic_mutation=0.80, clinical=0.06)],
        mutation={"disease_name": "pancreatic ductal adenocarcinoma", "mutated": 6582,
                  "tested": 9331, "frequency": 0.705},
    )
    return {
        "open_targets": f.open_targets(top=[f.assoc("Noonan syndrome", genetic_association=0.94)],
                                       ind=ind, loeuf=0.23,
                                       tractability={"Small molecule": ["Approved Drug"]}),
        "chembl": f.chembl([f.drug("SOTORASIB", 4.0), f.drug("ADAGRASIB", 4.0),
                            f.drug("SALIRASIB", 2.0, via="PROTEIN FAMILY")], 5089, 1453),
        "mgi": f.mgi("Kras", 205, lethal=["embryonic lethality during organogenesis"],
                     disease_models=[("pancreatic ductal adenocarcinoma", 12)]),
        "hpa": f.hpa(),
        "pubmed": {"total_count": 32259, "recent_count": 9000, "recent_window": "2022-2026"},
    }


def xpo1_results(indication_block):
    return {
        "open_targets": f.open_targets(top=[f.assoc("CLL", somatic_mutation=0.77)],
                                       ind=indication_block, loeuf=0.08,
                                       safety=[{"event": "liver injury", "datasources": ["X"], "effects": []}]),
        "chembl": f.chembl([f.drug("SELINEXOR", 4.0)], 500, 120),
        "mgi": f.mgi("Xpo1", 30, lethal=["embryonic lethality prior to organogenesis"]),
        "hpa": f.hpa(),
    }


class TestReferenceCases:
    def scores(self, results):
        return {s.dimension: s.score for s in E.assess(f.target(), results).scores}

    def test_kras_pdac_is_a_validated_driver_with_essential_gene_safety(self):
        a = E.assess(f.target("KRAS"), kras_pdac_results())
        scores = {s.dimension: s.score for s in a.scores}
        assert scores["human_genetics"] == 1.0  # 70% mutated
        assert scores["animal_models"] == 1.0  # KPC-type model of the indication
        assert scores["tractability"] == 1.0  # sotorasib / adagrasib
        assert {s.category for s in a.safety_signals} >= {"genetic_constraint", "mouse_phenotype"}

    def test_xpo1_cll_moderate_genetics_minor_driver(self):
        ind = f.indication("CLL", direct={"somatic_mutation": 0.77, "genetic_association": 0.48,
                                          "clinical": 0.08},
                           mutation={"disease_name": "CLL", "mutated": 180, "tested": 3865,
                                     "frequency": 0.047})
        scores = self.scores(xpo1_results(ind))
        assert scores["human_genetics"] == 0.7  # 4.7%: real but minor driver
        assert scores["clinical_precedence"] == 0.7

    def test_xpo1_cystic_fibrosis_is_a_high_genetic_gap(self):
        ind = f.indication("cystic fibrosis", areas=("respiratory or thoracic disease",),
                           has_evidence=False)
        a = E.assess(f.target("XPO1"), xpo1_results(ind))
        assert {s.dimension: s.score for s in a.scores}["human_genetics"] == 0.2
        assert a.gaps[0].severity == "high"
