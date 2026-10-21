"""Instrumentación del router: esquema del veredicto y migración (sin DB).

El veredicto es la única etiqueta VERDADERA del router. Si el esquema acepta un
`wrong_intent` sin intención, o deja colar campos como `organization_id`, el
corpus se contamina desde el primer clic.
"""
from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from app.schemas import IntentFeedbackIn, IntentValue
from app.services import intent

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "apps" / "api" / "db" / "sql" / "051_intent_instrumentation.sql"


def test_acepta_un_clic_sin_intencion():
    assert IntentFeedbackIn(verdict="accepted").corrected_intent is None
    assert IntentFeedbackIn(verdict="rejected", note="pedí otra cosa").verdict == "rejected"


def test_wrong_intent_exige_la_intencion_correcta():
    with pytest.raises(ValidationError):
        IntentFeedbackIn(verdict="wrong_intent")
    ok = IntentFeedbackIn(verdict="wrong_intent", corrected_intent="navegar")
    assert ok.corrected_intent == "navegar"


def test_corrected_intent_solo_aplica_a_wrong_intent():
    with pytest.raises(ValidationError):
        IntentFeedbackIn(verdict="accepted", corrected_intent="dato")


def test_rechaza_intenciones_inventadas():
    with pytest.raises(ValidationError):
        IntentFeedbackIn(verdict="wrong_intent", corrected_intent="resumen")


def test_bloquea_mass_assignment():
    with pytest.raises(ValidationError):
        IntentFeedbackIn(verdict="accepted", organization_id="00000000-0000-0000-0000-000000000000")
    with pytest.raises(ValidationError):
        IntentFeedbackIn(verdict="accepted", message_id="00000000-0000-0000-0000-000000000000")


def test_las_intenciones_del_esquema_son_las_del_router():
    """schemas no importa servicios; este test evita que las listas se separen."""
    assert set(get_args(IntentValue)) == {i.value for i in intent.Intent}


def test_router_version_es_una_etiqueta_estable():
    assert intent.ROUTER_VERSION
    assert " " not in intent.ROUTER_VERSION


def test_migracion_protege_la_tabla_como_el_resto_del_tenant():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "tenant_isolation" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON intent_feedback" in sql
    assert "UNIQUE (message_id, user_id)" in sql


def test_migracion_guarda_la_decision_completa():
    sql = MIGRATION.read_text(encoding="utf-8")
    for column in ("intent_confidence", "intent_reason", "intent_source", "router_version"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in sql
