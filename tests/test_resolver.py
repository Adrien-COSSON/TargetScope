# tests/test_resolver.py
"""Tests of target resolution: search strategies and confidence levels."""

import asyncio

import pytest

from backend.services import resolver


def hit(symbol, aliases=(), coding=True, **extra):
    return {"symbol": symbol, "alias": list(aliases), "name": f"{symbol} protein",
            "type_of_gene": "protein-coding" if coding else "ncRNA", **extra}


@pytest.mark.parametrize("query, first", [
    ("1956", "entrez"), ("ENSG00000146648", "ensembl"), ("P00533", "uniprot"),
    ("A0A024R161", "uniprot"), ("EGFR", "symbol_or_alias"),
])
def test_search_strategies(query, first):
    strategies = resolver._search_strategies(query)
    assert strategies[0][0] == first
    assert strategies[-1][0] == "free_text"  # always the last resort


def test_exact_symbol_is_high_confidence():
    hit_, confidence = resolver._select_hit([hit("TP53"), hit("TP53BP1")], "tp53", "symbol_or_alias")
    assert (hit_["symbol"], confidence) == ("TP53", "high")


def test_unique_coding_alias_is_medium():
    """The bug fixed in September: "p53" must resolve to TP53, not PURPL."""
    hits = [hit("PURPL", ["p53"], coding=False), hit("TP53", ["p53"])]
    hit_, confidence = resolver._select_hit(hits, "p53", "symbol_or_alias")
    assert (hit_["symbol"], confidence) == ("TP53", "medium")


def test_ambiguous_alias_is_low():
    hits = [hit("A", ["X1"]), hit("B", ["X1"])]
    assert resolver._select_hit(hits, "X1", "symbol_or_alias")[1] == "low"


def test_identifier_lookup_is_high():
    assert resolver._select_hit([hit("EGFR")], "1956", "entrez")[1] == "high"


def test_helpers():
    assert resolver._first(["a", "b"]) == "a" and resolver._first([]) is None
    assert resolver._as_list("x") == ["x"] and resolver._as_list(None) == []


def test_unavailable_raises(monkeypatch):
    async def fake(url, params=None, source=""):
        return None

    monkeypatch.setattr(resolver, "get_json", fake)
    with pytest.raises(resolver.ResolutionUnavailableError):
        asyncio.run(resolver.resolve_target("EGFR"))


def test_resolves_with_hgnc_curie(monkeypatch):
    async def fake(url, params=None, source=""):
        return {"hits": [hit("EGFR", entrezgene=1956, HGNC="3236",
                             uniprot={"Swiss-Prot": "P00533"},
                             ensembl={"gene": "ENSG00000146648"})]}

    monkeypatch.setattr(resolver, "get_json", fake)
    t = asyncio.run(resolver.resolve_target("EGFR"))
    assert (t.gene_symbol, t.hgnc_id, t.uniprot_id, t.ensembl_id) == \
        ("EGFR", "HGNC:3236", "P00533", "ENSG00000146648")


def test_no_hit_raises_value_error(monkeypatch):
    async def fake(url, params=None, source=""):
        return {"hits": []}

    monkeypatch.setattr(resolver, "get_json", fake)
    with pytest.raises(ValueError):
        asyncio.run(resolver.resolve_target("NOTAGENE"))
