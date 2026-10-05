"""UT-ABS — detector de abstención."""
from unittest.mock import MagicMock

import pytest

from app.services import abstention

pytestmark = pytest.mark.unit


def test_ut_abs_01_extract_terms_filters_stopwords():
    terms = abstention._extract_terms("¿Cuál es la capital de Francia?")
    assert "la" not in terms
    assert "es" not in terms
    assert "de" not in terms
    assert "capital" in terms
    assert "francia" in terms


def test_ut_abs_02_should_abstain_with_no_coverage():
    conn = MagicMock()
    conn.execute = lambda _s, _p=None: MagicMock(scalar=lambda: 0)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(abstention, "term_coverage", lambda _c, _case, _q: 0.0)
    assert abstention.should_abstain(conn, "case", "xyz nonsense", min_coverage=0.6) is True
    monkeypatch.undo()


def test_ut_abs_03_should_not_abstain_with_high_coverage():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(abstention, "term_coverage", lambda _c, _case, _q: 0.8)
    assert abstention.should_abstain(MagicMock(), "case", "pago contrato", min_coverage=0.6) is False
    monkeypatch.undo()
