"""UT-TERMS — vocabulario compartido del pipeline (sin DB)."""
from __future__ import annotations

import pytest

from app.services import text_terms as tt

pytestmark = pytest.mark.unit


def test_ut_terms_01_question_words_are_not_content():
    assert tt.content_terms("¿Qué evidencia hay sobre pagos?") == ["pagos"]
    assert tt.content_terms("¿Dónde se habla de la demanda?") == ["demanda"]


def test_ut_terms_02_specific_term_is_not_the_whole_question():
    assert tt.specific_term("¿Qué evidencia hay sobre pagos?") == "pago"
    assert tt.specific_term("¿Dónde se habla de DEMANDA en los archivos?") == "demanda"
    # Identificadores y nombres concretos ganan a palabras genéricas.
    assert tt.specific_term("¿Dónde aparece el oficio número 2834?") == "2834"
    assert tt.specific_term("¿Dónde aparece INVERFAST S.A.S. en la demanda acumulada?") == "inverfast"
    assert tt.specific_term("¿Dónde aparece el PAGARE en la demanda acumulada?") == "pagare"
    assert tt.specific_term("¿Dónde aparece el pagaré 001?") == "pagare 001"
    assert tt.specific_term("¿Dónde aparece el NIT 900 521.606-3?") == "nit 521.606"
    assert tt.specific_term("¿En qué minuto menciona Paola Ibáñez los pagos?") == "pago"


def test_ut_terms_03_stem_matches_plural_and_singular():
    assert tt.stem("pagos") == tt.stem("pago") == "pago"
    assert tt.stem("demandas") == "demanda"
    assert tt.stem("jueces") == "juez"
    assert tt.stem("poderes") == "poder"
    assert tt.overlap_with_question("La demanda afirma que se pagó", "¿Qué evidencia hay sobre pagos?") > 0.9


def test_ut_terms_04_coverage_uses_stems():
    assert tt.coverage("se pagó el saldo", "el pago fue parcial") == pytest.approx(1 / 2)
    assert tt.coverage("pago", "pago") == 1.0
    assert tt.coverage("", "cualquier cosa") == 1.0


def test_ut_terms_05_numbers_must_appear():
    assert tt.missing_numbers("pago de $80.000.000", "se pagaron 80000000 pesos") == []
    assert tt.missing_numbers("pago de $80.000.000", "no consta pago") == ["80000000"]


def test_ut_terms_06_expand_query_keeps_original_first_and_dedupes():
    variants = tt.expand_query("pagos")
    assert variants[0] == "pagos"
    assert "comprobantes" in variants
    assert len(variants) == len({tt.norm(v) for v in variants})


def test_ut_terms_07_mmss():
    assert tt.mmss(0) == "00:00"
    assert tt.mmss(40 * 60 * 1000 + 26 * 1000) == "40:26"
    assert tt.mmss(3600 * 1000 + 26 * 60 * 1000 + 48 * 1000) == "1:26:48"
    assert tt.mmss(None) == ""


def test_ut_terms_08_norm_removes_accents_and_case():
    assert tt.norm("APELACIÓN") == "apelacion"
    assert tt.norm("Pagaré") == "pagare"


def test_ut_terms_09_usura_expands_to_legal_basis_and_interrogation():
    """Una pregunta de usura debe buscar el 886 y el interrogatorio, no solo la palabra."""
    variants = {tt.norm(v) for v in tt.expand_query("¿Se cobraron intereses de usura?")}
    assert "886" in variants
    assert "capitalizacion" in variants
    typo = {tt.norm(v) for v in tt.expand_query("revisa los interregatorios y hechos")}
    assert "interrogatorios" in typo and "confesion" in typo
