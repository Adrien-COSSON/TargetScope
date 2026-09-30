# tests/test_hpa.py

# Import libraries
import httpx
import pytest
 
from backend.services import hpa
 
# Trimmed-down HPA payload based on the real TP53 response (ENSG00000141510)
TP53_PAYLOAD = {
    "Gene": "TP53",
    "Uniprot": ["P04637"],
    "RNA tissue specificity": "Low tissue specificity",
    "RNA tissue distribution": "Detected in all",
    "RNA tissue specific nTPM": None,
    "RNA single cell type specificity": "Low cell type specificity",
    "RNA single cell type specific nCPM": None,
    "Protein tissue specificity": "Not detected",
    "Reliability (IH)": "Enhanced",
    "Subcellular main location": ["Nucleoplasm"],
    "Subcellular additional location": ["Vesicles", "Cytosol"],
    "Secretome location": None,
    "Cancer prognostics - Breast Invasive Carcinoma (TCGA)": {
        "prognostic type": "",
        "prognostic": "unprognostic",
        "is_prognostic": False,
        "p_val": "2.13e-3",
    },
    "Cancer prognostics - Breast Invasive Carcinoma (validation)": {
        "prognostic type": "favorable",
        "prognostic": "potential prognostic",
        "is_prognostic": True,
        "p_val": "5.06e-4",
    },
    "Cancer prognostics - Colon Adenocarcinoma (validation)": {
        "prognostic type": "favorable",
        "prognostic": "potential prognostic",
        "is_prognostic": True,
        "p_val": "5.72e-4",
    },
}
 
 
# ---------- _to_float_dict ----------
 
def test_to_float_dict_converts_strings():
    assert hpa._to_float_dict({"liver": "120.5", "heart": "3"}) == {"liver": 120.5, "heart": 3.0}
 
 
def test_to_float_dict_none_when_null():
    assert hpa._to_float_dict(None) is None
 
 
def test_to_float_dict_skips_invalid_values():
    assert hpa._to_float_dict({"liver": "12.0", "bad": "n/a"}) == {"liver": 12.0}
 
 
def test_to_float_dict_none_when_all_invalid():
    assert hpa._to_float_dict({"bad": "n/a"}) is None
 
 
# ---------- _parse_expression / _parse_localization ----------
 
def test_parse_expression_low_specificity_keeps_null_ntpm():
    expr = hpa._parse_expression(TP53_PAYLOAD)
    assert expr["rna_tissue_specificity"] == "Low tissue specificity"
    assert expr["rna_tissue_specific_ntpm"] is None
    assert expr["reliability_ih"] == "Enhanced"
 
 
def test_parse_expression_enriched_tissue():
    data = {"RNA tissue specificity": "Tissue enriched", "RNA tissue specific nTPM": {"pancreas": "25000.1"}}
    expr = hpa._parse_expression(data)
    assert expr["rna_tissue_specific_ntpm"] == {"pancreas": 25000.1}
 
 
def test_parse_expression_missing_keys_do_not_crash():
    expr = hpa._parse_expression({})
    assert all(v is None for v in expr.values())
 
 
def test_parse_localization():
    loc = hpa._parse_localization(TP53_PAYLOAD)
    assert loc == {"main": ["Nucleoplasm"], "additional": ["Vesicles", "Cytosol"], "secretome": None}
 
 
def test_parse_localization_empty_payload():
    assert hpa._parse_localization({}) == {"main": [], "additional": [], "secretome": None}
 
 
# ---------- _parse_prognostics ----------
 
def test_parse_prognostics_keeps_only_significant_and_sorts():
    progs = hpa._parse_prognostics(TP53_PAYLOAD)
    assert len(progs) == 2
    assert progs[0] == {
        "cancer": "Breast Invasive Carcinoma",
        "cohort": "validation",
        "type": "favorable",
        "p_value": 5.06e-4,
    }
    assert progs[1]["cancer"] == "Colon Adenocarcinoma"
 
 
def test_parse_prognostics_key_without_cohort():
    data = {"Cancer prognostics - Glioma": {"is_prognostic": True, "prognostic type": "unfavorable", "p_val": "1e-5"}}
    progs = hpa._parse_prognostics(data)
    assert progs == [{"cancer": "Glioma", "cohort": None, "type": "unfavorable", "p_value": 1e-5}]
 
 
def test_parse_prognostics_invalid_pval_sorted_last():
    data = {
        "Cancer prognostics - A (TCGA)": {"is_prognostic": True, "prognostic type": "favorable", "p_val": None},
        "Cancer prognostics - B (TCGA)": {"is_prognostic": True, "prognostic type": "favorable", "p_val": "0.01"},
    }
    progs = hpa._parse_prognostics(data)
    assert [p["cancer"] for p in progs] == ["B", "A"]
    assert progs[1]["p_value"] is None
 
 
def test_parse_prognostics_empty_type_becomes_none():
    data = {"Cancer prognostics - A (TCGA)": {"is_prognostic": True, "prognostic type": "", "p_val": "0.01"}}
    assert hpa._parse_prognostics(data)[0]["type"] is None
 
 
# ---------- _confidence_from_reliability ----------
 
@pytest.mark.parametrize(
    ("reliability", "expected"),
    [
        ("Enhanced", "high"),
        ("Supported", "medium"),
        ("Approved", "low"),
        ("Uncertain", "low"),
        (None, "low"),
        ("Unknown value", "low"),
    ],
)
def test_confidence_from_reliability(reliability, expected):
    assert hpa._confidence_from_reliability(reliability) == expected
 
 
# ---------- fetch_hpa (get_json mocked) ----------
 
@pytest.fixture
def mock_provenance(monkeypatch):
    monkeypatch.setattr(hpa, "make_provenance", lambda **kwargs: kwargs)
 
 
@pytest.mark.asyncio
async def test_fetch_hpa_builds_url_and_result(monkeypatch, mock_provenance):
    called_urls = []
 
    async def fake_get_json(url, *args, **kwargs):
        called_urls.append(url)
        return TP53_PAYLOAD
 
    monkeypatch.setattr(hpa, "get_json", fake_get_json)
 
    result = await hpa.fetch_hpa("ENSG00000141510")
 
    assert called_urls == [f"{hpa.HPA_BASE_URL}/ENSG00000141510.json"]
    assert result["gene"] == "TP53"
    assert result["uniprot_ids"] == ["P04637"]
    assert result["localization"]["main"] == ["Nucleoplasm"]
    assert len(result["prognostics"]) == 2
    assert result["provenance"] == {
        "source": "HPA",
        "source_id": "ENSG00000141510",
        "url": f"{hpa.HPA_BASE_URL}/ENSG00000141510.json",
        "confidence": "high",  # TP53 reliability_ih = Enhanced
    }
 
 
@pytest.mark.asyncio
async def test_fetch_hpa_propagates_404(monkeypatch, mock_provenance):
    async def fake_get_json(url, *args, **kwargs):
        request = httpx.Request("GET", url)
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("Not Found", request=request, response=response)
 
    monkeypatch.setattr(hpa, "get_json", fake_get_json)
 
    with pytest.raises(httpx.HTTPStatusError):
        await hpa.fetch_hpa("ENSG_FAKE")