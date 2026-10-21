"""UT-CMP — compositor determinista: tipo de respuesta, presupuesto y ASR."""
from __future__ import annotations

import pytest

from app.services import compose
from app.services.intent import Intent

pytestmark = pytest.mark.unit


def _doc(handle: str, page: int, text: str, filename: str = "demanda.pdf") -> dict:
    return {"handle": handle, "source_type": "document_page", "document_id": "d1",
            "page_number": page, "filename": filename, "text": text}


def _seg(handle: str, text: str, start_ms: int = 61000, speaker: str = "Alba") -> dict:
    return {"handle": handle, "source_type": "transcript_segment", "media_id": "m1",
            "segment_id": f"s-{handle}", "start_ms": start_ms, "end_ms": start_ms + 4000,
            "speaker": speaker, "filename": "audiencia.mp4", "text": text}


def _render(question: str, claims: list[dict], evidence: list[dict], *, intent=Intent.LOOKUP,
            uncertainties: list[str] | None = None, **kwargs):
    return compose.render(question, intent=intent, claims=claims, evidence=evidence,
                          uncertainties=uncertainties or [], locale="es", **kwargs)


def _claim(text: str, *handles: str) -> dict:
    return {"text": text, "citations": list(handles)}


def test_ut_cmp_01_direct_answer_responds_and_cites():
    ev = [_doc("E1", 4, "La demandante afirma que el pago se hizo el 13/12/2024.")]
    out = _render("¿Qué evidencia hay sobre pagos?", [_claim(ev[0]["text"], "E1")], ev)
    assert out.answer_kind == "direct"
    assert "[1]" in out.answer and "**Fuentes**" in out.answer
    assert "demanda.pdf · p. 4" in out.answer
    assert out.used_handles == ["E1"]


def test_ut_cmp_02_partial_when_material_does_not_answer():
    ev = [_doc("E1", 4, "El juzgado fijó audiencia para el 3 de marzo.")]
    out = _render("¿Qué evidencia hay sobre pagos?", [_claim(ev[0]["text"], "E1")], ev)
    assert out.answer_kind == "partial"
    assert "Encontré material relacionado" in out.answer
    assert out.used_handles == ["E1"]


def test_ut_cmp_03_insufficient_is_a_single_message_without_sources():
    out = _render("¿Qué evidencia hay sobre pagos?", [], [_doc("E1", 1, "otra cosa")],
                  uncertainties=["sin datos"])
    assert out.answer_kind == "insufficient"
    assert out.used_handles == []
    assert "**Fuentes**" not in out.answer and "**Límites**" not in out.answer
    assert out.uncertainties == ["sin datos"]
    assert out.answer not in out.uncertainties


def test_ut_cmp_04_claim_without_valid_handle_is_dropped():
    ev = [_doc("E1", 4, "La demandante afirma el pago.")]
    out = _render("¿Qué dice la demanda del pago?", [_claim("La demandante afirma el pago.", "E9")], ev)
    assert out.answer_kind == "insufficient"
    assert out.used_handles == []


def test_ut_cmp_05_invented_number_goes_to_limits_not_answer():
    ev = [_doc("E1", 4, "La demandante afirma que pagó 80.000.000 el 13/12/2024.")]
    claim = _claim("La demandante pagó 95.000.000.", "E1")
    out = _render("¿Cuánto pagó la demandante?", [claim], ev)
    assert out.answer_kind == "insufficient"
    assert "95.000.000" not in out.answer.replace("**Límites**", "")
    assert any("95.000.000" in u for u in out.uncertainties)


def test_ut_cmp_06_asr_literal_is_degraded_to_audio():
    text = "Perdón un momentico, mire, se me hace que eso fue así, ¿no?"
    ev = [_seg("TR1", text)]
    out = _render("¿Qué evidencia hay sobre pagos?", [_claim(text, "TR1")], ev)
    assert out.answer_kind == "partial"
    assert "**Lo que dice el audio**" in out.answer
    assert "material relacionado" in out.answer.split("**Lo que dice el audio**")[0]


def test_ut_cmp_07_conversational_filler_is_not_a_direct_answer():
    text = "Bueno, entonces aquí estamos reunidos para la diligencia de hoy."
    ev = [_doc("E1", 2, text, filename="acta.pdf")]
    out = _render("¿Cuál es el monto del pago?", [_claim(text, "E1")], ev)
    assert out.answer_kind == "partial"
    assert "**Lo que dice el audio**" in out.answer or "**Evidencia**" in out.answer


def test_ut_cmp_08_budget_limits_visible_citations():
    ev = [_doc(f"E{i}", i, f"El pago número {i} consta en el expediente.") for i in range(1, 7)]
    claims = [_claim(ev[i]["text"], f"E{i+1}") for i in range(6)]
    out = _render("¿Qué evidencia hay sobre el pago?", claims, ev,
                  max_citations=4, max_per_file=10)
    assert len(out.used_handles) == 4
    assert out.answer.count("[") >= 4
    assert out.used_handles == ["E1", "E2", "E3", "E4"]


def test_ut_cmp_09_budget_keeps_all_sources_for_a_claim():
    ev = [_doc("E1", 1, "El pago consta en la página uno."),
          _doc("E2", 2, "El pago consta en la página dos."),
          _doc("E3", 3, "El pago consta en la página tres.")]
    claims = [_claim(ev[i]["text"], f"E{i+1}") for i in range(3)]
    out = _render("¿Qué dice el expediente sobre el pago?", claims, ev,
                  max_citations=4, max_per_file=2)
    # El tope por archivo ya no recorta una cita: las tres páginas se muestran.
    assert out.used_handles == ["E1", "E2", "E3"]
    assert "p. 3" in out.answer


def test_ut_cmp_09b_global_budget_omits_claim_instead_of_partial_citation():
    ev = [_doc("E1", 1, "El pago consta en la página uno."),
          _doc("E2", 2, "El pago consta en la página dos."),
          _doc("E3", 3, "El pago consta en la página tres.")]
    claims = [_claim(ev[i]["text"], f"E{i+1}") for i in range(3)]
    out = _render("¿Qué dice el expediente sobre el pago?", claims, ev,
                  max_citations=2, max_per_file=10)
    assert out.used_handles == ["E1", "E2"]
    assert "p. 3" not in out.answer


def test_ut_cmp_10_duplicate_page_is_not_cited_twice():
    ev = [_doc("E1", 4, "La demanda afirma el pago."),
          _doc("E2", 4, "La demanda afirma el pago (OCR alterno).")]
    claims = [_claim("La demanda afirma el pago.", "E1"),
              _claim("La demanda afirma el pago en el mismo folio.", "E2")]
    out = _render("¿Qué dice la demanda del pago?", claims, ev)
    assert out.used_handles == ["E1"]
    assert out.answer.count("demanda.pdf · p. 4") == 1


def test_ut_cmp_11_sources_only_include_referenced_handles():
    ev = [_doc("E1", 1, "El pago consta."), _doc("E2", 2, "Otra página sin uso.")]
    out = _render("¿Qué dice el expediente del pago?", [_claim("El pago consta.", "E1")], ev)
    assert out.used_handles == ["E1"]
    assert "p. 2" not in out.answer


def test_ut_cmp_12_navigation_is_not_an_answer():
    out = _render("¿Dónde se habla de pagos?", [], [_doc("E1", 1, "pago")], intent=Intent.NAVIGATE)
    assert out.answer_kind == "navigational"
    assert out.used_handles == []
    assert "**Fuentes**" not in out.answer


def test_ut_cmp_13_uncertainties_are_deduplicated():
    ev = [_doc("E1", 4, "La demandante afirma el pago.")]
    out = _render("¿Qué dice la demanda del pago?", [_claim(ev[0]["text"], "E1")], ev,
                  uncertainties=["Dato de parte.", "Dato de parte."])
    assert out.uncertainties == ["Dato de parte."]
    assert out.answer.count("Dato de parte.") == 1


def test_ut_cmp_14_multiline_claims_are_normalized():
    ev = [_doc("E1", 4, "La demanda afirma que el pago se hizo el 13/12/2024.")]
    out = _render("¿Qué dice la demanda del pago?",
                  [_claim("La demanda\n\n   afirma   que el pago se hizo el 13/12/2024.", "E1")], ev)
    assert "\n\n   afirma" not in out.answer
    assert "La demanda afirma que el pago" in out.answer


def test_ut_cmp_15_markers_follow_first_reference_order():
    ev = [_doc("E1", 1, "El pago consta en el folio uno."),
          _doc("E2", 2, "El pago consta en el folio dos.")]
    claims = [_claim("El pago consta en el folio uno y dos.", "E2", "E1")]
    out = _render("¿Qué dice el expediente del pago?", claims, ev)
    assert out.used_handles == ["E2", "E1"]
    assert out.answer.index("[1]") < out.answer.index("[2]")


def test_ut_cmp_16_direct_claim_is_numbered_before_supporting_material():
    ev = [_doc("E1", 2, "El juzgado fijó audiencia para el 3 de marzo."),
          _doc("E2", 1, "El demandado pagó 80.000.000 el 13/12/2024.")]
    claims = [_claim(ev[0]["text"], "E1"), _claim(ev[1]["text"], "E2")]
    out = _render("¿Cuánto pagó el demandado?", claims, ev)
    assert out.answer_kind == "direct"
    assert out.used_handles == ["E2", "E1"]
    assert out.answer.index("[1]") < out.answer.index("[2]")
