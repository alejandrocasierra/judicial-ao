"""UT-GRX — expansión de consultas por grafo (contrato §1.3, fase 3)."""
import pytest

from app.services import graph_expand

pytestmark = pytest.mark.unit


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    @property
    def mappings(self):
        return _Mappings(self._rows)


class _Conn:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        self.calls.append((str(clause), dict(params or {})))
        if self.fail:
            raise RuntimeError("no graph")
        return _Result(self.rows)


def test_grx_01_adds_labels_from_the_graph():
    conn = _Conn([{"label": "Pago de honorarios", "metadata": {}}])
    out = graph_expand.expand_queries(conn, "case", "¿Qué evidencia hay sobre pagos?", ["¿Qué evidencia hay sobre pagos?"])
    assert "Pago de honorarios" in out
    assert out[0] == "¿Qué evidencia hay sobre pagos?"


def test_grx_02_adds_aliases_from_json_metadata():
    conn = _Conn([{"label": "Pago", "metadata": {"aliases": '["abono", "consignación"]'}}])
    out = graph_expand.expand_queries(conn, "case", "pagos", ["pagos"])
    assert "Pago" in out
    assert "abono" in out
    assert "consignación" in out


def test_grx_03_adds_aliases_from_comma_separated_metadata():
    conn = _Conn([{"label": "Pago", "metadata": {"aliases": "abono; transferencia"}}])
    out = graph_expand.expand_queries(conn, "case", "pagos", ["pagos"])
    assert "abono" in out
    assert "transferencia" in out


def test_grx_04_dedupes_preserving_order():
    conn = _Conn([{"label": "pagos", "metadata": {}}, {"label": "PAGOS", "metadata": {}}])
    out = graph_expand.expand_queries(conn, "case", "pagos", ["pagos", "pagos"])
    assert sum(1 for value in out if value.lower() == "pagos") == 1


def test_grx_05_caps_the_result_at_eight_queries():
    conn = _Conn([{"label": f"etiqueta {i}", "metadata": {}} for i in range(20)])
    out = graph_expand.expand_queries(conn, "case", "pagos", ["pagos"])
    assert len(out) <= graph_expand.MAX_QUERIES == 8


def test_grx_06_without_terms_returns_originals():
    conn = _Conn([{"label": "no debería usarse", "metadata": {}}])
    out = graph_expand.expand_queries(conn, "case", "¿de qué?", ["original"])
    assert out == ["original"]
    assert conn.calls == []


def test_grx_07_graph_failure_returns_originals():
    conn = _Conn(fail=True)
    out = graph_expand.expand_queries(conn, "case", "pagos", ["original"])
    assert out == ["original"]


def test_grx_08_uses_bound_parameters_not_string_interpolation():
    conn = _Conn([{"label": "Pago", "metadata": {}}])
    graph_expand.expand_queries(conn, "case", "pago contrato", ["pago contrato"])
    sql, params = conn.calls[0]
    assert "%pago%" in params.values()
    assert "pago contrato" not in sql
    assert ":t0" in sql


def test_grx_09_limits_terms_to_five():
    conn = _Conn([])
    graph_expand.expand_queries(conn, "case", "uno dos tres cuatro cinco seis siete", ["original"])
    _sql, params = conn.calls[0]
    terms = [key for key in params if key.startswith("t")]
    assert len(terms) <= graph_expand.MAX_TERMS


def test_grx_10_metadata_as_json_string_is_supported():
    conn = _Conn([{"label": "Pago", "metadata": '{"aliases": ["abono"]}'}])
    out = graph_expand.expand_queries(conn, "case", "pagos", ["pagos"])
    assert "abono" in out
