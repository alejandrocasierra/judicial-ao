"""UT-EVC — consolidación de actuaciones procesales duplicadas."""
import uuid

import pytest

from app.services import event_consolidation as ec

pytestmark = pytest.mark.unit


def _ev(eid, subtype, date, description, instance=None, doc_id=None, page=None,
        authority=None, participants=None, effect=None):
    return {
        "id": uuid.UUID(eid),
        "event_date": date,
        "subtype": subtype,
        "instance": instance,
        "authority": authority,
        "actor": None,
        "description": description,
        "document_id": uuid.UUID(doc_id) if doc_id else None,
        "page_number": page,
        "participants": participants or [],
        "procedural_effect": effect,
    }


class _FakeConn:
    def __init__(self):
        self.calls: list[str] = []

    def execute(self, sql, params=None):
        self.calls.append(str(sql))


def _run(monkeypatch, events):
    monkeypatch.setattr(ec, "rows", lambda _c, _s, **_p: events)
    fake = _FakeConn()
    result = ec.consolidate_case_events(fake, str(uuid.uuid4()), str(uuid.uuid4()))
    return fake, result


def test_evc_01_merges_identical_same_day(monkeypatch):
    """Dos actuaciones con mismo subtipo, mismo día y texto casi igual se fusionan."""
    a = _ev(str(uuid.uuid4()), "notificacion", None, "Se notifica al demandado la admisión de la demanda.")
    b = _ev(str(uuid.uuid4()), "notificacion", None, "Se notifica al demandado la admisión de la demanda")
    fake, result = _run(monkeypatch, [a, b])
    assert result["merged"] == 1
    assert result["deleted"] == 1
    assert any("DELETE FROM events" in s for s in fake.calls)
    assert any("UPDATE citations" in s for s in fake.calls)


def test_evc_02_keeps_different_same_day(monkeypatch):
    """Dos notificaciones distintas el mismo día NO se fusionan."""
    a = _ev(str(uuid.uuid4()), "notificacion", None,
            "Se notifica a Juan Pérez en la calle 1 número 2 de Bogotá.")
    b = _ev(str(uuid.uuid4()), "notificacion", None,
            "Se notifica a María López en la carrera 45 sur de Medellín.")
    _fake, result = _run(monkeypatch, [a, b])
    assert result["merged"] == 0


def test_evc_03_merges_same_doc_page(monkeypatch):
    """Misma página del mismo documento => mismo acto aunque el texto difiera."""
    doc = str(uuid.uuid4())
    a = _ev(str(uuid.uuid4()), "auto", None, "El juzgado admite la demanda.", doc_id=doc, page=3)
    b = _ev(str(uuid.uuid4()), "auto", None, "Auto admisorio de la demanda presentada.", doc_id=doc, page=3)
    _fake, result = _run(monkeypatch, [a, b])
    assert result["merged"] == 1


def test_evc_04_does_not_merge_across_subtypes(monkeypatch):
    """Eventos de subtipos distintos viven en cubos distintos y no se mezclan."""
    from datetime import date
    a = _ev(str(uuid.uuid4()), "demanda", date(2020, 1, 1), "Presentación de la demanda.")
    b = _ev(str(uuid.uuid4()), "sentencia", date(2020, 1, 1), "Presentación de la demanda.")
    _fake, result = _run(monkeypatch, [a, b])
    assert result["merged"] == 0


def test_evc_05_merges_near_dates_high_similarity(monkeypatch):
    """Fechas a ±3 días con texto muy similar se fusionan."""
    from datetime import date
    a = _ev(str(uuid.uuid4()), "auto_de_pruebas", date(2020, 5, 10),
            "Se decreta la práctica de la prueba testimonial solicitada por la parte demandante.")
    b = _ev(str(uuid.uuid4()), "auto_de_pruebas", date(2020, 5, 12),
            "Se decreta la práctica de la prueba testimonial solicitada por la parte demandante.")
    _fake, result = _run(monkeypatch, [a, b])
    assert result["merged"] == 1


def test_evc_06_keeps_near_dates_low_similarity(monkeypatch):
    """Fechas cercanas pero textos distintos NO se fusionan."""
    from datetime import date
    a = _ev(str(uuid.uuid4()), "auto_de_pruebas", date(2020, 5, 10), "Se decreta prueba documental.")
    b = _ev(str(uuid.uuid4()), "auto_de_pruebas", date(2020, 5, 11), "Se fija fecha para audiencia inicial.")
    _fake, result = _run(monkeypatch, [a, b])
    assert result["merged"] == 0


def test_evc_07_merge_unions_participants_and_effects(monkeypatch):
    """Al fusionar se unen participantes y efectos sin perder información."""
    p1, p2 = str(uuid.uuid4()), str(uuid.uuid4())
    a = _ev(str(uuid.uuid4()), "auto", None, "Auto admisorio de la demanda.",
            participants=[p1], effect="Se admite la demanda.")
    b = _ev(str(uuid.uuid4()), "auto", None, "Auto admisorio de la demanda",
            participants=[p2], effect="Se ordena notificar al demandado.")
    fake, result = _run(monkeypatch, [a, b])
    assert result["merged"] == 1
    updates = [s for s in fake.calls if "participants" in s]
    assert updates, "debe actualizar participantes/efecto del canónico"
