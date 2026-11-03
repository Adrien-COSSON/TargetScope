# backend/services/gene_info.py
"""Gene-level facts for the identity card: chromosome band, position, size, exons.

Source: MyGene.info (already used by the resolver), gene endpoint by Entrez id.
The cytogenetic band (e.g. "2p15") lets the reader connect the gene to known
chromosomal abnormalities (XPO1 and the 2p gain in CLL).
"""

import logging

from backend.utils.http_client import MYGENE_BASE_URL, get_json

logger = logging.getLogger(__name__)

FIELDS = "symbol,name,map_location,genomic_pos,type_of_gene,summary,exons"
NCBI_GENE_URL = "https://www.ncbi.nlm.nih.gov/gene/{entrez_id}"


def _main_position(genomic_pos) -> dict | None:
    """MyGene gives one dict, or a list when the gene maps to alternate loci.

    The primary assembly entry is the one on a plain chromosome name ("2",
    "X"), not on a patch or alternate haplotype ("HSCHR2_1_CTG...").
    """
    positions = genomic_pos if isinstance(genomic_pos, list) else [genomic_pos]
    positions = [p for p in positions if isinstance(p, dict)]
    primary = [
        p for p in positions if str(p.get("chr", "")).isalnum() and len(str(p.get("chr"))) <= 2
    ]
    return (primary or positions or [None])[0]


def _main_transcript(exons) -> dict | None:
    """The RefSeq mRNA (NM_) with the most exons, as a stand-in for the canonical one.

    MyGene lists one entry per RefSeq transcript, each with its exon positions.
    """
    transcripts = [t for t in (exons or []) if isinstance(t, dict) and t.get("position")]
    if not transcripts:
        return None
    coding = [
        t for t in transcripts if str(t.get("transcript", "")).startswith("NM_")
    ] or transcripts
    best = max(coding, key=lambda t: len(t["position"]))
    return {"transcript": best.get("transcript"), "exon_count": len(best["position"])}


async def fetch_gene_info(entrez_id: int | str | None) -> dict:
    """Chromosome, band, position, gene length and exon count of a human gene.

    Returns found=None if MyGene could not be reached or the id is missing,
    found=False if the gene is unknown (404 is treated as failure by
    get_json, which is fine here: the resolver already validated the id).
    """
    if not entrez_id:
        return {"found": None}
    data = await get_json(
        f"{MYGENE_BASE_URL}/gene/{entrez_id}", params={"fields": FIELDS}, source="MyGene"
    )
    if data is None:
        return {"found": None}

    position = _main_position(data.get("genomic_pos")) or {}
    start, end = position.get("start"), position.get("end")
    transcript = _main_transcript(data.get("exons"))

    return {
        "found": True,
        "chromosome": position.get("chr"),
        "band": data.get("map_location"),  # e.g. "2p15"
        "start": start,
        "end": end,
        "strand": position.get("strand"),
        "gene_length_bp": (end - start + 1) if start and end else None,
        "exon_count": transcript["exon_count"] if transcript else None,
        "reference_transcript": transcript["transcript"] if transcript else None,
        "gene_type": data.get("type_of_gene"),
        "summary": data.get("summary"),  # RefSeq gene summary
        "url": NCBI_GENE_URL.format(entrez_id=entrez_id),
    }
