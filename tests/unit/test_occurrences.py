"""UT-OCC — ubicaciones: cuándo se calculan, agrupación, tope y dedupe."""
from __future__ import annotations

import pytest

from app.services import occurrences
from app.services.intent import Intent, IntentDecision

pytestmark = pytest.mark.unit


def _decision(intent: Intent) -> IntentDecision:
    return IntentDecision(intent=intent, confidence=0.9, reason="test",
                          nav_term="pago" if intent is Intent.NAVIGATE else None, source="rules")


def _seg(filename: str, start_ms: int, speaker: str = "Alba", end_ms: int | None = None) -> dict:
    return {"source_type": "transcript_segment", "media_id": "m1", "segment_id": f"s{start_ms}",
            "filename": filename, "start_ms": start_ms,
            "end_ms": end_ms if end_ms is not None else start_ms + 3000,
            "speaker": speaker, "text": "se habla del pago"}


def _page(filename: str, page: int) -> dict:
    return {"source_type": "document_page", "document_id": f"d-{filename}", "filename": filename,
            "page_number": page, "folio": None, "text": "pago"}


def test_ut_occ_01_should_locate_only_for_navigation_or_explicit_location():
    assert occurrences.should_locate(_decision(Intent.NAVIGATE), "direct", "¿Dónde se habla de pagos?")
    assert not occurrences.should_locate(_decision(Intent.LOOKUP), "direct", "¿Qué evidencia hay sobre pagos?")
    assert occurrences.should_locate(_decision(Intent.LOOKUP), "direct", "¿En qué minuto se menciona el pago?")
    assert not occurrences.should_locate(_decision(Intent.NAVIGATE), "insufficient", "¿Dónde aparece el pago?")
    assert not occurrences.should_locate(_decision(Intent.NAVIGATE), "correction", "¿Dónde aparece el pago?")


def test_ut_occ_02_build_groups_by_file_and_marks_citations():
    locs = [_seg("audiencia.mp4", 40000), _seg("audiencia.mp4", 600000, speaker="Jorge"),
            _page("demanda.pdf", 4)]
    text, cites = occurrences.build("pago", locs, "es")
    assert text.startswith("Menciones del término «pago». No son la respuesta.")
    assert "**audiencia.mp4** — 2 menciones" in text
    assert "**demanda.pdf** — 1 menciones" in text
    assert [c["marker"] for c in cites] == [1, 2, 3]
    assert {c["group"] for c in cites} == {"audiencia.mp4", "demanda.pdf"}


def test_ut_occ_03_build_respects_the_limit():
    locs = [_seg("audiencia.mp4", i * 60000) for i in range(1, 12)]
    _, cites = occurrences.build("pago", locs, "es", max_items=8)
    assert len(cites) == 8
    assert [c["marker"] for c in cites] == list(range(1, 9))


def test_ut_occ_04_dedupe_by_file_minute_speaker():
    locs = [_seg("audiencia.mp4", 60000), _seg("audiencia.mp4", 61000), _seg("audiencia.mp4", 62000)]
    _, cites = occurrences.build("pago", locs, "es")
    # Mismo minuto y hablante → una sola mención (aun con tres segmentos).
    assert len(cites) == 1


def test_ut_occ_05_contiguous_segments_are_merged():
    locs = [_seg("audiencia.mp4", 60000, end_ms=62000),
            _seg("audiencia.mp4", 63500, end_ms=66000),
            _seg("audiencia.mp4", 120000, end_ms=123000)]
    _, cites = occurrences.build("pago", locs, "es")
    # El primero y el segundo se fusionan (gap 1.5 s); el tercero cae en otro minuto.
    assert len(cites) == 2


def test_ut_occ_06_non_informative_term_returns_no_locations():
    text, cites = occurrences.build("el", [_page("demanda.pdf", 1)], "es")
    assert cites == []
    assert "No encontré" in text
    assert occurrences.build("evidencia", [_page("demanda.pdf", 1)], "es")[1] == []


def test_ut_occ_07_document_citations_have_page_and_file():
    text, cites = occurrences.build("pago", [_page("demanda.pdf", 4)], "es")
    assert cites[0]["source_type"] == "document_page"
    assert cites[0]["document_id"] and cites[0]["page"] == 4
    assert "p. 4" in text


def test_ut_occ_08_media_citations_have_minute_and_speaker():
    _, cites = occurrences.build("pago", [_seg("audiencia.mp4", 5190000, speaker="Alba")], "es")
    assert cites[0]["start_mmss"] == "1:26:30" or cites[0]["start_mmss"] == "86:30"
    assert cites[0]["speaker"] == "Alba"
