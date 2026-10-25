"""UT-ANL — modelo de análisis opcional (F1/R6, A9).

El modo profundo puede usar un modelo propio, pero su ausencia nunca rompe el
turno: se conserva el de la sesión. Sin red ni BD real (dobles de `tx`/`one`).
"""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.routers import query

pytestmark = pytest.mark.unit

ORG = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"


@contextmanager
def _fake_tx(*_args, **_kwargs):
    yield object()


class _LLM:
    def __init__(self, name: str):
        self.model = name


def _settings(name: str = "", provider: str = "") -> SimpleNamespace:
    return SimpleNamespace(ANALYSIS_MODEL_NAME=name, ANALYSIS_MODEL_PROVIDER=provider)


def _patch_db(monkeypatch, row: dict | None, calls: list | None = None):
    def _one(_conn, sql, **params):
        if calls is not None:
            calls.append((sql, params))
        return row

    monkeypatch.setattr(query, "tx", _fake_tx)
    monkeypatch.setattr(query, "one", _one)


def test_anl_01_deep_usa_el_modelo_configurado(monkeypatch):
    """A9: si el nombre existe en `ai_models`, se usa ese modelo."""
    session_llm = _LLM("modelo-de-la-sesion")
    analysis_llm = _LLM("deepseek-chat")
    _patch_db(monkeypatch, {"id": "model-1"})
    used: list[tuple] = []

    def _fake_get(model_id, org_id, user_id):
        used.append((model_id, org_id, user_id))
        return analysis_llm

    monkeypatch.setattr(query, "get_llm_for_model", _fake_get)
    result = query._analysis_llm(_settings("deepseek-chat"), ORG, USER, session_llm, deep=True)
    assert result is analysis_llm
    assert used == [("model-1", ORG, USER)]


def test_anl_02_modelo_inexistente_conserva_el_de_la_sesion(monkeypatch):
    """A9: un nombre que no existe no lanza excepción ni deja el turno sin modelo."""
    session_llm = _LLM("modelo-de-la-sesion")
    _patch_db(monkeypatch, None)
    monkeypatch.setattr(query, "get_llm_for_model",
                        lambda *a, **k: pytest.fail("no debe pedir un modelo inexistente"))
    result = query._analysis_llm(_settings("modelo-inventado"), ORG, USER, session_llm, deep=True)
    assert result is session_llm


def test_anl_03_fallo_del_proveedor_conserva_el_de_la_sesion(monkeypatch):
    session_llm = _LLM("modelo-de-la-sesion")
    _patch_db(monkeypatch, {"id": "model-1"})

    def _boom(*_a, **_k):
        raise RuntimeError("api caida")

    monkeypatch.setattr(query, "get_llm_for_model", _boom)
    result = query._analysis_llm(_settings("deepseek-chat"), ORG, USER, session_llm, deep=True)
    assert result is session_llm


def test_anl_04_nombre_vacio_no_toca_la_base(monkeypatch):
    session_llm = _LLM("modelo-de-la-sesion")

    def _no_db(*_a, **_k):
        pytest.fail("sin ANALYSIS_MODEL_NAME no se consulta ai_models")

    monkeypatch.setattr(query, "tx", _no_db)
    monkeypatch.setattr(query, "one", _no_db)
    monkeypatch.setattr(query, "get_llm_for_model", _no_db)
    result = query._analysis_llm(_settings(), ORG, USER, session_llm, deep=True)
    assert result is session_llm


def test_anl_05_sin_deep_no_cambia_el_modelo(monkeypatch):
    session_llm = _LLM("modelo-de-la-sesion")

    def _no_db(*_a, **_k):
        pytest.fail("el modelo de análisis solo aplica al modo profundo")

    monkeypatch.setattr(query, "tx", _no_db)
    monkeypatch.setattr(query, "one", _no_db)
    monkeypatch.setattr(query, "get_llm_for_model", _no_db)
    result = query._analysis_llm(_settings("deepseek-chat"), ORG, USER, session_llm, deep=False)
    assert result is session_llm


def test_anl_06_filtra_por_proveedor_si_se_indico(monkeypatch):
    session_llm = _LLM("modelo-de-la-sesion")
    calls: list[tuple] = []
    _patch_db(monkeypatch, {"id": "model-1"}, calls)
    monkeypatch.setattr(query, "get_llm_for_model", lambda *a, **k: _LLM("deepseek-chat"))
    query._analysis_llm(_settings("deepseek-chat", "deepseek"), ORG, USER, session_llm, deep=True)
    assert calls, "debe consultar ai_models"
    sql, params = calls[0]
    assert "provider" in sql
    assert params["p"] == "deepseek"
