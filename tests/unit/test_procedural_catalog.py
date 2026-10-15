"""UT-PROC-CAT — catálogo canónico de subtipos procesales."""
import pytest

from app.domain import procedural_catalog as pc

pytestmark = pytest.mark.unit


def test_normalize_variants():
    """Variantes ortográficas/acentuadas se normalizan al subtipo canónico."""
    cases = [
        ("demanda", "demanda"),
        ("Demanda", "demanda"),
        ("contestación de demanda", "contestacion"),
        ("contestacion_demanda", "contestacion"),
        ("excepción", "excepcion"),
        ("auto admisorio", "auto_admisorio"),
        ("auto_admisorio_demanda", "auto_admisorio"),
        ("auto_admite_demanda", "auto_admisorio"),
        ("auto de pruebas", "auto_de_pruebas"),
        ("decreto_de_pruebas", "auto_de_pruebas"),
        ("fijación audiencia", "auto_de_pruebas"),
        ("recurso de apelación", "apelacion"),
        ("recurso_de_casacion", "casacion"),
        ("interposición recurso", "recurso"),
        ("notificación por aviso", "notificacion_por_estado"),
        ("notificacion electronica", "notificacion_electronica"),
        ("medida cautelar", "medida_cautelar"),
        ("cautelar", "medida_cautelar"),
        ("inspección", "inspeccion_judicial"),
        ("dictamen pericial", "dictamen"),
        ("solicitud de prueba", "solicitud_prueba"),
        ("otros", "otro"),
    ]
    for raw, expected in cases:
        assert pc.normalize_subtype(raw) == expected, f"failed for {raw!r}"


def test_normalize_canonical_returns_same():
    """Un subtipo canónico se devuelve sin cambios y sin raw."""
    for subtype in pc.list_subtypes():
        assert pc.normalize_subtype(subtype) == subtype


def test_normalize_unknown_returns_none():
    """Subtipos no reconocidos devuelven None."""
    assert pc.normalize_subtype("foo_bar") is None
    assert pc.normalize_subtype("xyz") is None
    assert pc.normalize_subtype("") is None
    assert pc.normalize_subtype(None) is None


def test_is_valid_subtype():
    """is_valid_subtype acepta canónicos y None, rechaza el resto."""
    assert pc.is_valid_subtype(None) is True
    assert pc.is_valid_subtype("demanda") is True
    assert pc.is_valid_subtype("auto_admisorio") is True
    assert pc.is_valid_subtype("otro") is True
    assert pc.is_valid_subtype("foo") is False
    assert pc.is_valid_subtype("auto_admite_demanda") is False


def test_subtype_label_spanish():
    """Las etiquetas en español son legibles."""
    assert pc.subtype_label("auto_admisorio", "es") == "Auto admisorio"
    assert pc.subtype_label("medida_cautelar", "es") == "Medida cautelar"


def test_subtype_label_english():
    """Soporta etiquetas en inglés."""
    assert pc.subtype_label("sentencia", "en") == "Judgment"


def test_subtype_label_unknown_returns_key():
    """Un subtipo desconocido devuelve la clave como fallback."""
    assert pc.subtype_label("unknown_subtype") == "unknown_subtype"


def test_list_subtypes_sorted():
    """list_subtypes devuelve la lista ordenada de canónicos."""
    subtypes = pc.list_subtypes()
    assert subtypes == sorted(subtypes)
    assert "demanda" in subtypes
    assert "otro" in subtypes
