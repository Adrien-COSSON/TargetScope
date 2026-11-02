# tests/factories.py
"""Builders for fake service results, shaped like the real services' output.

Values for the reference cases come from real runs (2026-11-02):
scripts/check_assessment.py on EGFR, XPO1, KRAS, C9orf72.
"""

from backend.schemas.target import Target


def target(symbol="EGFR", **ids):
    return Target(input_query=symbol, gene_symbol=symbol, name=f"{symbol} protein", **ids)


def assoc(name, therapeutic_areas=("cancer or benign tumor",), score=0.5, **datatypes):
    """One Open Targets association row, as returned by _parse_associations."""
    return {
        "disease_id": name.upper().replace(" ", "_"),
        "disease_name": name,
        "score": score,
        "therapeutic_areas": list(therapeutic_areas),
        "datatype_scores": datatypes,
    }


def indication(name, direct=None, fallback=(), mutation=None, synonyms=(),
               areas=("cancer or benign tumor",), descendants=0, has_evidence=True):
    """The 'indication' block of fetch_open_targets()."""
    return {
        "disease_id": name.upper().replace(" ", "_"),
        "disease_name": name,
        "therapeutic_areas": list(areas),
        "direct_score": 0.5 if direct else 0.0,
        "direct_datatype_scores": dict(direct or {}),
        "associated_descendant_count": descendants,
        "best_descendant": None,
        "has_evidence": has_evidence,
        "synonyms": list(synonyms),
        "fallback_associations": list(fallback),
        "mutation_frequency": mutation,
    }


def open_targets(top=(), ind=None, focus=(), tractability=None, loeuf=None, safety=()):
    return {
        "found": True,
        "association_count": len(top),
        "top_associations": list(top),
        "tractability": tractability or {},
        "genetic_constraint": {"loeuf": loeuf} if loeuf is not None else None,
        "safety_liabilities": list(safety),
        "indication": ind,
        "focus_associations": list(focus) if ind else None,
    }


def drug(name, phase, via="SINGLE PROTEIN", modality="Small molecule", **flags):
    return {"name": name, "max_phase": phase, "via_target_type": via,
            "molecule_type": modality, **flags}


def chembl(drugs=(), activities=0, potent=0, found=True, summary=None):
    drugs = list(drugs)
    return {
        "found": found,
        "drugs": drugs,
        "drug_summary": summary or {"drug_count": len(drugs),
                                    "approved_count": sum(d["max_phase"] == 4.0 for d in drugs)},
        "activity_count": activities,
        "potent_activity_count": potent,
    }


def mgi(symbol="Egfr", phenotypes=0, lethal=(), disease_models=(), found=True):
    return {
        "found": found,
        "mouse_gene": {"symbol": symbol} if found else None,
        "phenotype_count": phenotypes,
        "top_phenotypes": [{"term": "waved hair"}] if phenotypes else [],
        "lethality_phenotypes": [{"term": t, "pmids": ["PMID:1"]} for t in lethal],
        "disease_models": [{"disease": d, "model_count": n} for d, n in disease_models],
    }


def hpa(specificity="Low tissue specificity", distribution="Detected in all", enriched=None,
        cancer_specificity=None):
    return {
        "found": True,
        "expression": {
            "rna_tissue_specificity": specificity,
            "rna_tissue_distribution": distribution,
            "rna_tissue_specific_ntpm": enriched,
        },
        "cancer_expression": {"specificity": cancer_specificity, "distribution": "Detected in all"}
        if cancer_specificity else {},
    }
