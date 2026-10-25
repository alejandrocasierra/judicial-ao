"""F4 · R1 — metadatos de la fuente como contexto verificable.

T1 del contrato: A1–A4 y las variantes de nombre de archivo. Funciones puras:
sin BD y sin red.
"""
from __future__ import annotations

import pytest

from app.services import evidence_meta

pytestmark = pytest.mark.unit


VIDEO = "0065GrabacionAudiencia13Dic2024.mp4"


def _ctx(filename: str = VIDEO, **extra) -> str:
    return evidence_meta.context({"filename": filename, "text": "Fragmento de la audiencia.", **extra})


# --- A1/A2: fechas ----------------------------------------------------------------

def test_a1_date_in_filename_context_is_supported():
    assert evidence_meta.date_supported("13 de diciembre de 2024", _ctx())


def test_a2_date_absent_from_context_is_not_supported():
    assert not evidence_meta.date_supported("13 de diciembre de 2024", "12 de mayo de 2025")


def test_date_not_supported_by_year_alone():
    assert not evidence_meta.date_supported("13 de diciembre de 2024", "diciembre de 2024")


def test_date_supported_tolerates_accents_case_and_separators():
    ctx = "Audiencia del 13 De Diciembre De 2024 en el Juzgado 21."
    assert evidence_meta.date_supported("13 de diciembre de 2024", ctx)
    assert evidence_meta.date_supported("13/12/2024", ctx)
    assert evidence_meta.date_supported("2024-12-13", ctx)


def test_date_supported_accepts_two_digit_year_variant():
    assert evidence_meta.date_supported("13 de diciembre de 2024", "acta del 13/12/24")


def test_date_supported_accepts_month_first_ocr_order():
    """Los pagarés del expediente escriben «Bogotá ABRIL 4 de 2008»."""
    ctx = "PAGARE • Bogota ABRIL 4 de 2008 • PAGARE NUMERO: 0110"
    assert evidence_meta.date_supported("4 de abril de 2008", ctx)
    assert not evidence_meta.date_supported("4 de mayo de 2008", ctx)


# --- A3/A4: nombres ---------------------------------------------------------------

def test_a3_name_with_initials_is_compatible():
    assert evidence_meta.name_compatible("Jorge Humberto Rojas Melo", "JORGE H. ROJAS M.")


def test_a4_different_person_is_not_compatible():
    assert not evidence_meta.name_compatible("Jorge Humberto Rojas Melo",
                                             "Carlos Alfonso Gómez Garcés")


def test_name_compatible_with_surnames_only():
    assert evidence_meta.name_compatible("Jorge Humberto Rojas Melo", "ROJAS MELO")


def test_name_compatible_ignores_corporate_suffix_and_punctuation():
    assert evidence_meta.name_compatible("Inverfast S.A.S.", "INVERFAST SAS")
    assert evidence_meta.name_compatible("Inverfast S.A.S.", "Inverfast")


def test_name_compatible_requires_more_than_a_shared_surname():
    assert not evidence_meta.name_compatible("Jorge Rojas", "Carlos Rojas")


def test_name_compatible_distinguishes_two_parties_with_the_same_surnames():
    """En el expediente hay dos Garzón Gutiérrez: no son la misma persona."""
    assert not evidence_meta.name_compatible("Juan Carlos Garzón Gutiérrez",
                                             "Carlos Alfonso Garzón Gutiérrez")
    assert evidence_meta.name_compatible("Juan Carlos Garzón Gutiérrez",
                                         "JUAN CARLOS GARZON GUTIERREZ")


# --- variantes de filename_context -------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("12Mayo2025.pdf", ["12 mayo 2025", "12/05/2025", "2025-05-12"]),
    ("25Junio2025.pdf", ["25 junio 2025", "25/06/2025", "2025-06-25"]),
    ("0065GrabacionAudiencia13Dic2024.mp4", ["13 diciembre 2024", "13/12/2024", "2024-12-13"]),
])
def test_filename_context_recognizes_compact_dates(filename, expected):
    ctx = evidence_meta.filename_context(filename)
    for value in expected:
        assert value in ctx, (filename, value, ctx)


def test_filename_context_recognizes_case_numbers():
    ctx = evidence_meta.filename_context("0001 Expediente2018-361.pdf")
    for value in ("2018", "361", "2018-361"):
        assert value in ctx


def test_filename_context_recognizes_concatenated_case_number():
    ctx = evidence_meta.filename_context("0001 Expediente201800361.pdf")
    for value in ("2018", "361", "2018-361"):
        assert value in ctx


def test_filename_context_recognizes_iso_date():
    ctx = evidence_meta.filename_context("auto2024-03-17.pdf")
    for value in ("17 de marzo de 2024", "17/03/2024"):
        assert value in ctx


def test_filename_context_keeps_original_and_stem():
    ctx = evidence_meta.filename_context(VIDEO)
    assert VIDEO in ctx
    assert "0065GrabacionAudiencia13Dic2024" in ctx


def test_filename_context_without_filename_is_empty():
    assert evidence_meta.filename_context(None) == ""
    assert evidence_meta.filename_context("") == ""


# --- context() ---------------------------------------------------------------------

def test_context_concatenates_label_fields_and_filename_context():
    item = {
        "text": "El pago se hizo.",
        "filename": VIDEO,
        "speaker": "JORGE H. ROJAS M.",
        "start_mmss": "01:40",
        "page_number": 12,
        "folio": "F-12",
        "name": "Escrito de demanda",
        "label": "SPEAKER_00",
    }
    ctx = evidence_meta.context(item)
    for value in ("El pago se hizo.", VIDEO, "13 diciembre 2024", "JORGE H. ROJAS M.",
                  "01:40", "12", "F-12", "Escrito de demanda", "SPEAKER_00"):
        assert value in ctx


def test_context_keeps_fact_fields_for_the_composers():
    item = {
        "text": "El demandante cobró intereses de usura.",
        "fact_label": "[Hecho #7 · EVIDENCE_SUPPORTED]",
        "fact_text": "El demandante cobró intereses de usura.",
        "proof_status": "EVIDENCE_SUPPORTED",
        "fact_number": "7",
        "claim_text": "cobro de intereses",
    }
    ctx = evidence_meta.context(item)
    for value in ("[Hecho #7 · EVIDENCE_SUPPORTED]", "El demandante cobró intereses de usura.",
                  "EVIDENCE_SUPPORTED", "7", "cobro de intereses"):
        assert value in ctx


def test_context_of_empty_item_is_empty():
    assert evidence_meta.context({}) == ""


# --- party_present -----------------------------------------------------------------

NAMES = ["Jorge Humberto Rojas Melo", "JORGE H. ROJAS M."]


def test_party_present_when_context_has_the_abbreviated_speaker():
    claim = "Jorge Humberto Rojas Melo rindió su nombre completo ante el despacho."
    ctx = "Fragmento. JORGE H. ROJAS M. 01:56 audiencia.mp4"
    assert evidence_meta.party_present(claim, ctx, NAMES)


def test_party_present_true_when_the_claim_does_not_name_the_party():
    assert evidence_meta.party_present("El pago se hizo en enero.", "texto", NAMES)


def test_party_present_false_when_the_claim_names_a_party_absent_from_context():
    claim = "Jorge Humberto Rojas Melo rindió su nombre completo ante el despacho."
    assert not evidence_meta.party_present(claim, "El pago se hizo en enero.", NAMES)


def test_party_present_does_not_confuse_a_different_person():
    claim = "Carlos Alfonso Gómez Garcés suscribió el pagaré."
    ctx = "JORGE H. ROJAS M. manifestó que el pago se hizo."
    assert not evidence_meta.party_present(
        claim, ctx, NAMES + ["Carlos Alfonso Gómez Garcés"])


def test_mentions_does_not_match_carlos_inside_juan_carlos():
    """El hermano Garzón: «Juan Carlos» no acredita a «Carlos Alfonso»."""
    assert not evidence_meta._mentions(
        "Juan Carlos Garzón Gutiérrez rindió declaración.",
        "Carlos Alfonso Garzón Gutiérrez")
    assert evidence_meta._mentions(
        "Carlos Alfonso Garzón Gutiérrez rindió declaración.",
        "Carlos Alfonso Garzón Gutiérrez")


def test_party_present_surnames_in_mid_sentence():
    assert evidence_meta.party_present(
        "cheques girados a Rojas Melo",
        "cheques a favor del señor Rojas Melo",
        ["Jorge Humberto Rojas Melo"]) is True
    assert evidence_meta.party_present(
        "el demandado Garzón Gutiérrez",
        "declaración del señor Garzón Gutiérrez",
        ["Juan Carlos Garzón Gutiérrez"]) is True


def test_party_present_still_rejects_the_other_garzon_brother():
    assert evidence_meta.party_present(
        "Juan Carlos Garzón Gutiérrez",
        "habló el apoderado Carlos Alfonso Garzón Gutiérrez",
        ["Juan Carlos Garzón Gutiérrez"]) is False
