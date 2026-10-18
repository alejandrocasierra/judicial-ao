"""UT-TIMELINE — ordenamiento fallback de la línea de tiempo."""
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.routers import cases
from app.security.deps import Principal

pytestmark = pytest.mark.unit


def test_timeline_uses_fallback_date_order(monkeypatch):
    """El endpoint ordena primero por event_date, luego document_date,
    luego incorporation_date, y devuelve ambas columnas."""
    captured: list[dict] = []

    def _rows(_c, sql, **params):
        captured.append({"sql": sql, "params": params})
        return []

    class _Tx:
        def __enter__(self):
            return MagicMock()
        def __exit__(self, *args):
            return False

    monkeypatch.setattr(cases, "case_access", lambda p, cid, perm: {"id": cid})
    monkeypatch.setattr(cases, "tx", lambda org_id, user_id: _Tx())
    monkeypatch.setattr(cases, "rows", _rows)
    monkeypatch.setattr(cases, "ct_read", MagicMock(event_code_map=lambda _c, _cid: {}))

    p = Principal(user_id="u", org_id="o", org_role="ORG_ADMIN", locale="es", permissions={"case.read"})
    case_id = UUID("12345678-1234-5678-1234-567812345678")
    result = cases.timeline(case_id, p=p)

    assert result == []
    sql = captured[0]["sql"]
    assert "e.document_date" in sql
    assert "e.incorporation_date" in sql
    assert "e.participants" in sql
    assert "COALESCE(e.event_date, e.document_date, e.incorporation_date)" in sql
