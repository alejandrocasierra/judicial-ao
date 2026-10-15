"""UT-PG — Process Graph de actuaciones procesales."""
import uuid

import pytest

from app.services import procedural_graph as pg

pytestmark = pytest.mark.unit


def _ev(eid: str, subtype: str, date: str | None, description: str,
        doc_id: str | None = None, page: int | None = None,
        date_type: str = "actuacion", duplicate_of: str | None = None) -> dict:
    return {
        "id": uuid.UUID(eid),
        "event_date": date,
        "event_type": "auto",
        "subtype": subtype,
        "instance": "primera",
        "date_type": date_type,
        "description": description,
        "duplicate_of": uuid.UUID(duplicate_of) if duplicate_of else None,
        "document_id": uuid.UUID(doc_id) if doc_id else None,
        "page_number": page,
    }


class _FakeConn:
    def __init__(self):
        self.deleted_events: list[str] = []

    def execute(self, sql, params=None):
        # Captura DELETE FROM events WHERE id = :i AND case_id = :c
        if "DELETE FROM events" in str(sql) and params:
            eid = params.get("i") or (params[1] if isinstance(params, tuple) else None)
            if eid:
                self.deleted_events.append(str(eid))


def test_ut_pg_01_review_marks_similar_descriptions_as_duplicate(monkeypatch):
    """Dos actuaciones reales del mismo tipo y fecha con descripción casi igual son duplicados."""
    def _one(_c, _s, **_p):
        return {"id": str(uuid.uuid4())}

    dup = _ev(str(uuid.uuid4()), "notificacion", "2020-05-10", "Se notifica al demandado.")
    dup2 = _ev(str(uuid.uuid4()), "notificacion", "2020-05-10", "Se notifica al demandado.")
    monkeypatch.setattr(pg, "rows", lambda _c, _s, **_p: [dup, dup2])
    monkeypatch.setattr(pg, "one", _one)

    result = pg.review_events(_FakeConn(), str(uuid.uuid4()), str(uuid.uuid4()))
    assert result["duplicado"] == 2


def test_ut_pg_02_review_does_not_mark_different_same_day_events(monkeypatch):
    """Un auto con dos notificaciones distintas el mismo día NO son duplicados."""
    def _one(_c, _s, **_p):
        return {"id": str(uuid.uuid4())}

    a = _ev(str(uuid.uuid4()), "notificacion", "2020-05-10",
            "Se notifica a Juan Pérez en la dirección calle 1.", doc_id=str(uuid.uuid4()), page=1)
    b = _ev(str(uuid.uuid4()), "notificacion", "2020-05-10",
            "Se notifica a María López en la dirección carrera 2.", doc_id=str(uuid.uuid4()), page=1)
    monkeypatch.setattr(pg, "rows", lambda _c, _s, **_p: [a, b])
    monkeypatch.setattr(pg, "one", _one)

    result = pg.review_events(_FakeConn(), str(uuid.uuid4()), str(uuid.uuid4()))
    assert result["duplicado"] == 0


def test_ut_pg_03_review_marks_same_doc_page_as_duplicate(monkeypatch):
    """Dos eventos extraídos de la misma página del mismo documento se marcan duplicados."""
    def _one(_c, _s, **_p):
        return {"id": str(uuid.uuid4())}

    doc_id = str(uuid.uuid4())
    a = _ev(str(uuid.uuid4()), "auto", "2020-05-10", "Auto admisorio de la demanda.", doc_id=doc_id, page=1)
    b = _ev(str(uuid.uuid4()), "auto", "2020-05-10", "El juzgado admite la demanda.", doc_id=doc_id, page=1)
    monkeypatch.setattr(pg, "rows", lambda _c, _s, **_p: [a, b])
    monkeypatch.setattr(pg, "one", _one)

    result = pg.review_events(_FakeConn(), str(uuid.uuid4()), str(uuid.uuid4()))
    assert result["duplicado"] == 2


def test_ut_pg_04_review_marks_reference_without_source(monkeypatch):
    """Un evento referenciado sin fuente real se marca sin_fuente_real."""
    def _one(_c, _s, **_p):
        return {"id": str(uuid.uuid4())}

    ref = _ev(str(uuid.uuid4()), "demanda", "2020-01-01", "Se menciona la demanda.", date_type="referenciada")
    monkeypatch.setattr(pg, "rows", lambda _c, _s, **_p: [ref])
    monkeypatch.setattr(pg, "one", _one)

    result = pg.review_events(_FakeConn(), str(uuid.uuid4()), str(uuid.uuid4()))
    assert result["sin_fuente_real"] == 1


def test_ut_pg_05_link_deletes_resolved_reference(monkeypatch):
    """Un referenciado que coincide con un evento real se borra (se une al real)."""
    real_id = str(uuid.uuid4())
    ref_id = str(uuid.uuid4())
    real = _ev(real_id, "demanda", "2020-01-15", "Demanda presentada por el actor.")
    ref = _ev(ref_id, "demanda", "2020-01-15", "Se presentó demanda por el actor.", date_type="referenciada")

    # El mock de rows devuelve siempre la lista de eventos (suficiente para SELECT inicial).
    monkeypatch.setattr(pg, "rows", lambda _c, _s, **_p: [real, ref])
    monkeypatch.setattr(pg, "one", lambda _c, _s, **_p: {"id": str(uuid.uuid4())})
    monkeypatch.setattr(pg, "_absorb_event_citations", lambda _c, _o, _case, _src, _tgt: 0)

    fake = _FakeConn()
    result = pg.link_events(fake, str(uuid.uuid4()), str(uuid.uuid4()))
    assert result["refers_to"] == 1
    assert ref_id in fake.deleted_events


def test_ut_pg_06_review_no_longer_marks_fecha_inconsistente(monkeypatch):
    """El revisor ya no genera la bandera 'fecha_inconsistente'."""
    def _one(_c, _s, **_p):
        return {"id": str(uuid.uuid4())}

    real = _ev(str(uuid.uuid4()), "demanda", "2020-01-15", "Demanda presentada.")
    ref = _ev(str(uuid.uuid4()), "demanda", "2020-01-20", "Se menciona la demanda.",
               date_type="referenciada", duplicate_of=str(real["id"]))
    monkeypatch.setattr(pg, "rows", lambda _c, _s, **_p: [real, ref])
    monkeypatch.setattr(pg, "one", _one)

    result = pg.review_events(_FakeConn(), str(uuid.uuid4()), str(uuid.uuid4()))
    assert "fecha_inconsistente" not in result
