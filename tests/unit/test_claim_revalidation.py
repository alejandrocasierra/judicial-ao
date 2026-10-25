"""Los cuatro errores de fidelidad del verificador, en miniatura.

No prueban el LLM: prueban la parte determinista que decide si una cita
realmente sostiene la afirmación que se va a mostrar.
"""
import json

import pytest

from app.services import answering
from app.services import verification

pytestmark = pytest.mark.unit


def _item(handle: str, source_type: str, text: str, **extra) -> dict:
    return {"handle": handle, "source_type": source_type, "text": text, **extra}


def test_discards_claim_that_attributes_to_recording_but_only_acta_supports_it():
    items = [
        _item("E1", "transcript_segment", "Yo no lo aprovecho y lo hizo, doctora.",
              media_id="m1", segment_id="s1", start_ms=1040750),
        _item("E2", "document_page",
              "No siendo otro el motivo de la presente diligencia se da por terminada "
              "y se procede a elaborar y firmar el acta, la que hará parte del expediente digital. "
              "La juez Alba Lucy Cock Álvarez y el secretario ad hoc hicieron constar "
              "la finalización de la diligencia y la suscripción del acta.",
              document_id="d1", page_number=1),
    ]
    claims = [{
        "text": ("En la grabación de la audiencia del 12 de mayo de 2025 se declaró "
                 "terminada la diligencia y se procedería a elaborar el acta."),
        "citations": ["E1"],
    }]

    result = answering.revalidate_claims(claims, items)

    assert result["claims"] == []
    assert result["unsupported_claims"][0]["reason"] == "not_grounded"


def test_discards_page_142_claim_that_the_page_does_not_contain():
    items = [
        _item("E1", "document_page",
              "JAOTO HT Abogada Titulada. Señor JUEZ 21 CIVIL DEL CIRCUITO DE BOGOTÁ. "
              "ASUNTO. Doy cumplimiento auto de fecha 5 de marzo, aclarando solicitud "
              "de terminación por pago total presentada por la suscrita.",
              document_id="d1", page_number=142),
    ]
    claims = [{
        "text": ("En el expediente obra folio que refiere expresamente a la Fecha del "
                 "pagaré 001 por valor de $1.528.030.000, que representa el capital."),
        "citations": ["E1"],
    }]

    result = answering.revalidate_claims(claims, items)

    assert result["claims"] == []
    assert result["unsupported_claims"][0]["reason"] == "not_grounded"


def test_keeps_claim_when_the_cited_acta_contains_it():
    items = [
        _item("E2", "document_page",
              "No siendo otro el motivo de la presente diligencia se da por terminada "
              "y se procede a elaborar y firmar el acta, la que hará parte del expediente digital. "
              "La juez Alba Lucy Cock Álvarez y el secretario ad hoc hicieron constar "
              "la finalización de la diligencia y la suscripción del acta.",
              document_id="d1", page_number=1),
    ]
    claims = [{
        "text": ("En el acta escrita de esa audiencia se hizo constar la finalización "
                 "de la diligencia y la suscripción del acta."),
        "citations": ["E2"],
    }]

    result = answering.revalidate_claims(claims, items)

    assert len(result["claims"]) == 1
    assert result["unsupported_claims"] == []


def test_clean_limits_removes_page_not_present_in_evidence():
    items = [_item("E1", "document_page", "Memorial de notificación.",
                   document_id="d1", page_number=40)]
    limits = answering.clean_uncertainties(
        ["Solo se cuenta con extractos parciales (páginas 40 y 51) del documento."], items)

    # El límite meta con una página no citada no se publica.
    assert limits == []


def test_clean_limits_keeps_page_that_is_cited():
    items = [_item("E1", "document_page", "Memorial de notificación.",
                   document_id="d1", page_number=40)]
    limits = answering.clean_uncertainties(
        ["Solo se recuperó la página 40 del documento."], items)

    assert limits == ["Solo se recuperó la página 40 del documento."]


def test_parse_and_validate_default_threshold_rejects_weak_overlap():
    raw = json.dumps({"claims": [{
        "text": "El Tribunal Superior de Bogotá declaró inadmisible la apelación.",
        "citations": ["E1"],
    }]})
    items = [_item("E1", "document_page",
                   "El despacho relevó al secuestre y designó a Carlos Alfonso Garzón.",
                   document_id="d1", page_number=4)]

    result = answering.parse_and_validate(raw, items, 0.0)

    assert result["claims"] == []
    assert result["unsupported_claims"][0]["reason"] == "not_grounded"


def test_label_colon_does_not_join_proper_names():
    claim = ("Hallazgo: Según los hechos, Juan Carlos Garzón Gutiérrez suscribió el pagaré "
             "a favor de José Rueda Avellaneda.")
    source = ("Según los hechos de la demanda, Juan Carlos Garzón Gutiérrez suscribió el "
              "pagaré a favor de José Rueda Avellaneda.")
    assert answering._required_supported(claim, source)


def test_tolerates_truncated_ocr_title():
    items = [
        _item("E1", "document_page",
              "DEZ CIVIL DEL CIRCUITO DE FUNZA - CUNDINAMARCA. "
              "JANNETH QUIJANO MORANT informa que JORGE HUMBERTO ROJAS MELO "
              "fue notificado el 27 de febrero de 2019 dentro del proceso 2018-00361.",
              document_id="d1", page_number=40),
    ]
    claims = [{
        "text": ("Contiene un memorial dirigido al Juez Civil del Circuito de Funza por "
                 "Janneth Quijano Morant, en el que informa que Jorge Humberto Rojas Melo "
                 "fue notificado el 27 de febrero de 2019 dentro del proceso 2018-00361."),
        "citations": ["E1"],
    }]

    result = answering.revalidate_claims(claims, items)

    assert len(result["claims"]) == 1
    assert result["unsupported_claims"] == []


def test_reassigns_claim_with_title_to_source_that_names_the_role():
    items = [
        _item("E1", "transcript_segment",
              "La presente diligencia se da por terminada y se procederá a elaborar el acta.",
              media_id="m1", segment_id="s1", start_ms=1139510, speaker="Alba Lucy Cock Alvarez"),
        _item("E2", "document_page",
              "La juez Alba Lucy Cock Álvarez y el secretario ad hoc hicieron constar que la "
              "presente diligencia se da por terminada y se procede a elaborar y firmar el acta.",
              document_id="d1", page_number=1),
    ]
    claims = [{
        "text": ("Dicha manifestación fue pronunciada por la juez Alba Lucy Cock Álvarez al "
                 "concluir la diligencia, señalando que se elaboraría el acta para su envío."),
        "citations": ["E1"],
    }]

    result = answering.revalidate_claims(claims, items)

    assert len(result["claims"]) == 1
    assert result["claims"][0]["citations"] == ["E2"]
    assert result["claims"][0].get("reassigned") is True


def test_discards_invented_manager_name():
    items = [
        _item("E1", "document_page",
              "CERTIFICA: se constituyó la sociedad ADMINISTRACIONES JUDICIALES LTDA. "
              "con objeto social de secuestre y perito judicial.",
              document_id="d1", page_number=105),
    ]
    claims = [{
        "text": ("La gerente inscrita de Administraciones Judiciales Ltda. es "
                 "Argenida Isabel Pacheco Cantero."),
        "citations": ["E1"],
    }]

    result = answering.revalidate_claims(claims, items)

    assert result["claims"] == []
    assert result["unsupported_claims"][0]["reason"] == "not_grounded"


def test_rejects_claim_cited_to_search_chunk_without_visible_source():
    raw = json.dumps({"claims": [{
        "text": "En el expediente se listan el auto y los oficios 630 y 974.",
        "citations": ["E1"],
    }]})
    # Un chunk de búsqueda sin document_id/página no se puede mostrar como cita.
    items = [_item("E1", "chunk", "Oficio 630 del Juzgado 4 Civil del Circuito.")]

    result = answering.parse_and_validate(raw, items, 0.34)

    assert result["claims"] == []
    assert result["unsupported_claims"][0]["reason"] == "invalid_citation"


def test_revalidate_rejects_non_citable_chunk():
    items = [_item("E1", "chunk", "Oficio 630 del Juzgado 4 Civil del Circuito.")]
    claims = [{"text": "Se listan el auto y los oficios 630 y 974.", "citations": ["E1"]}]

    result = answering.revalidate_claims(claims, items)

    assert result["claims"] == []
    assert result["unsupported_claims"][0]["reason"] == "invalid_citation"


def test_apply_verdicts_removes_claim_even_if_judge_truncates_text():
    validated = {
        "claims": [{
            "text": ("Decisión frente a la cual el Tribunal Superior de Bogotá "
                     "declaró inadmisible la apelación."),
            "citations": ["E1"],
        }],
        "unsupported_claims": [],
        "uncertainties": [],
    }
    verdicts = [{
        "claim": "Decisión frente a la cual el Tribunal Superior de Bogotá declaró inadmisible la apelación",
        "status": "not_supported",
        "reason": "La evidencia no menciona al Tribunal Superior ni la inadmisibilidad.",
    }]

    result = verification.apply_verdicts(validated, verdicts)

    assert result["claims"] == []
    assert result["unsupported_claims"][0]["reason"] == "not_supported"

# --- F4 · R4: revisión, no borrado -------------------------------------------------

def _review_items() -> list[dict]:
    return [_item("E1", "document_page", "Obra el pagaré en el expediente.",
                  document_id="d1", page_number=4)]


def test_f4_low_overlap_with_valid_citation_is_kept_for_review():
    claims = [{"text": "El pagaré fue entregado a la sociedad demandante.", "citations": ["E1"]}]

    result = answering.revalidate_claims(claims, _review_items())

    assert result["unsupported_claims"] == []
    assert len(result["claims"]) == 1
    assert result["claims"][0]["needs_review"] is True
    assert result["claims"][0]["overlap"] == 0.25


def test_f4_low_overlap_with_missing_number_stays_unsupported():
    """La regla de cifras no se relaja: una cifra ausente sigue bloqueando."""
    claims = [{"text": "El pagaré fue entregado a la sociedad demandante por $9.400.000.",
               "citations": ["E1"]}]

    result = answering.revalidate_claims(claims, _review_items())

    assert result["claims"] == []
    assert result["unsupported_claims"][0]["missing_numbers"] == ["9.400.000"]


def test_f4_overlap_below_review_threshold_stays_unsupported():
    claims = [{"text": "El documento fue firmado por la sociedad demandante en Bogotá.",
               "citations": ["E1"]}]

    result = answering.revalidate_claims(claims, _review_items())

    assert result["claims"] == []
    assert result["unsupported_claims"][0]["reason"] == "not_grounded"


def test_f4_parse_and_validate_keeps_low_overlap_claim_for_review():
    raw = json.dumps({"claims": [{
        "text": "El pagaré fue entregado a la sociedad demandante.", "citations": ["E1"]}]})

    result = answering.parse_and_validate(raw, _review_items())

    assert result["unsupported_claims"] == []
    assert len(result["claims"]) == 1
    assert result["claims"][0]["needs_review"] is True


def test_f4_parse_and_validate_discards_low_overlap_without_numbers_rule():
    raw = json.dumps({"claims": [{
        "text": "El pagaré fue entregado por $9.400.000.", "citations": ["E1"]}]})

    result = answering.parse_and_validate(raw, _review_items())

    assert result["claims"] == []
    assert result["unsupported_claims"][0]["missing_numbers"] == ["9.400.000"]
