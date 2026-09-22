# backend\utils\alias_query.py

# Import libraries
from schemas.target import Target

AMBIGUOUS_ALIASES = {"p53", "met", "akt", "erk", "ras"} # to be complet

def get_clean_aliases(target: Target) -> list[str]:
    # On part du gene_symbol + tous les aliases
    all_terms = [target.gene_symbol] + target.aliases
    
    clean = []
    seen = set()
    
    for alias in all_terms:
        alias_lower = alias.lower()
        # filter : too short, ambiguous, already seen
        if len(alias) < 3:
            continue
        if alias_lower in AMBIGUOUS_ALIASES:
            continue
        if alias_lower in seen:
            continue
        seen.add(alias_lower)
        clean.append(alias)
    
    return clean
    # → ["EGFR", "HER1", "ErbB1"]

def build_pubmed_query(target: Target) -> str:
    aliases = get_clean_aliases(target)  # for example ["EGFR", "HER1", "ErbB1"]
    terms = [f'"{alias}"[tiab]' for alias in aliases] # transform each alias to "EGFR"[tiab] for exmple
    return " OR ".join(terms) # join with ' OR '
    

def build_freetext_query(target: Target) -> str:
    aliases = get_clean_aliases(target)  # for example ["EGFR", "HER1", "ErbB1"]
    terms = [f'"{alias}"' for alias in aliases] # transform each alias to "EGFR" for exmple
    # joindre avec ' OR '
    return " OR ".join(terms) # join with ' OR '