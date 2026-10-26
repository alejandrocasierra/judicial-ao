"""UT-CFS — la siembra de hechos llega a ambos caminos del chat y su progreso es honesto.

Contrato F2 · A4 (agent y rag reciben los mismos `fact_items`) y A7 (`by_source`
con las dos claves, incluso con valor 0).
"""
import pytest

from app.routers import chats, query

pytestmark = pytest.mark.unit

FACT = {
    "handle": "F1", "source_type": "document_page", "document_id": "d1", "page_number": 10,
    "filename": "0015 Contestacion.pdf", "text": "El demandante cobró intereses de usura.",
    "seed": "fact", "fact_id": "f1", "fact_number": "7",
    "fact_text": "El demandante cobró intereses de usura.", "proof_status": "EVIDENCE_SUPPORTED",
    "fact_label": "[Hecho #7 · EVIDENCE_SUPPORTED]",
}


def test_cfs_01_merge_seed_keeps_fact_items_and_dedupes():
    """A4: el hecho viaja en la semilla común a los dos caminos, antes del seed propio."""
    retr = [dict(FACT, text="otro texto", seed=None)]
    merged = chats._merge_seed([], [FACT], retr, [])
    assert merged == [FACT]


def test_cfs_02_merge_seed_is_the_same_with_or_without_a_path_specific_seed():
    """El camino rag passa `seed` y el camino agent no: los hechos son idénticos."""
    contradiction = [{"source_type": "document_page", "document_id": "d2", "page_number": 3,
                      "text": "otra versión", "seed": "contradiction"}]
    claim = [{"source_type": "document_page", "document_id": "d3", "page_number": 5,
              "text": "claim", "seed": "claim", "claim_text": "texto"}]
    for seed in (None, contradiction):
        merged = chats._merge_seed([], [FACT], seed, claim)
        assert merged[0] is FACT
        assert claim[0] in merged


def test_cfs_03_merge_seed_dedupes_the_same_page_between_fact_and_claim():
    claim = dict(FACT, seed="claim", claim_text="texto")
    merged = chats._merge_seed([], [FACT], None, [claim])
    assert merged == [FACT]


def test_cfs_06_normalize_seed_items_keeps_fact_fields():
    """R4: los `seed_items` de hecho sobreviven la normalización de query.py."""
    normalized = query._normalize_seed_items([dict(FACT)])
    assert normalized[0]["seed"] == "fact"
    assert normalized[0]["fact_text"] == FACT["fact_text"]
    assert normalized[0]["proof_status"] == "EVIDENCE_SUPPORTED"
    assert normalized[0]["source_type"] == "document_page"
