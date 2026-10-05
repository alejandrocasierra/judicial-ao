"""UT-EV — la evidencia que ve el modelo incluye minuto, hablante y archivo.

Sin esto el agente no sabía en qué minuto ni quién lo dijo aunque la cita sí lo
mostraba (bug reportado en el chat)."""
from __future__ import annotations

import pytest

from app.services import agent, answering

pytestmark = pytest.mark.unit


def test_ut_ev_01_source_label_transcript_has_minute_speaker_file():
    it = {"source_type": "transcript_segment", "media_id": "m1", "start_ms": 61080, "end_ms": 66220,
          "speaker": "Álvaro Lúzico Álvarez", "filename": "0077Audiencia.mp4"}
    label = answering._source_label(it)
    assert "0077Audiencia.mp4" in label
    assert "01:01" in label              # 61080 ms = 01:01
    assert "Álvaro Lúzico Álvarez" in label


def test_ut_ev_02_source_label_document_has_page_and_file():
    it = {"source_type": "document_page", "document_id": "d1", "page_number": 12, "folio": "F-12",
          "filename": "demanda.pdf"}
    label = answering._source_label(it)
    assert "demanda.pdf" in label and "page:12" in label and "F-12" in label


def test_ut_ev_03_evidence_hint_summarizes_transcript():
    it = {"handle": "TR1", "source_type": "transcript_segment", "start_mmss": "01:01",
          "speaker": "Álvaro Lúzico Álvarez", "filename": "0077Audiencia.mp4",
          "text": "No podía asistir, se encontraba hospitalizado"}
    hint = agent._evidence_hint(it)
    assert "minuto 01:01" in hint and "Álvaro Lúzico Álvarez" in hint and "0077Audiencia.mp4" in hint


def test_ut_ev_04_evidence_hint_summarizes_document_page():
    it = {"handle": "P1", "source_type": "document_page", "page_number": 3, "filename": "demanda.pdf",
          "text": "JURISDICCIÓN: Civil"}
    hint = agent._evidence_hint(it)
    assert "demanda.pdf" in hint and "página 3" in hint


def test_ut_ev_05_grounding_accepts_minute_and_speaker_from_labels():
    """Una afirmación que cita «minuto 01:58 · hablante» debe validarse aunque ese
    minuto/hablante estén en la etiqueta y no en el texto del segmento."""
    import json

    items = [{"handle": "TR1", "source_type": "transcript_segment", "text": "¿Quién es Jorge?",
              "start_mmss": "01:58", "speaker": "Alba Lucy Cock Alvarez", "filename": "0077.mp4"}]
    raw = json.dumps({"claims": [{"text": "En el minuto 01:58, Alba Lucy Cock Alvarez preguntó «¿Quién es Jorge?».",
                                  "citations": ["TR1"]}], "uncertainties": []})
    out = answering.parse_and_validate(raw, items, min_overlap=0.5)
    assert out["claims"], out["unsupported_claims"]


def test_ut_ev_06_grounding_ignores_reporting_words():
    """Una lista de minutos con palabras de reporte («según las marcas de tiempo…») no se
    rechaza; los números siguen exigiéndose."""
    ratio, missing = answering.grounding(
        "Según las marcas de tiempo de las intervenciones atribuidas a Paola Ibanez, habló en los minutos 01:26 y 01:28.",
        "Paola Ibanez 01:26 Gracias, doctora. Paola Ibanez 01:28 El día sábado…")
    assert ratio >= 0.5 and missing == []


def test_ut_ev_07_grounding_still_rejects_invented_number():
    ratio, missing = answering.grounding(
        "El señor Jorge pagó 999.999 pesos el 5 de mayo.",
        "Paola Ibanez 01:26 Gracias, doctora.")
    assert missing, "un monto inventado debe detectarse"
