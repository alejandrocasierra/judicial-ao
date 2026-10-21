"""La ficha de partes es la fuente de roles; no las menciones sueltas de páginas."""
from types import SimpleNamespace

import pytest

from app.services import party_answer, query_rewrite

pytestmark = pytest.mark.unit


def test_role_detection_distinguishes_defendants_from_all_parties():
    assert party_answer.requested_roles("¿Quiénes son los demandados?") == ["defendant"]
    assert party_answer.requested_roles("¿Quiénes son los demandantes?") == ["claimant"]
    assert party_answer.requested_roles("¿Quiénes son los apoderados?") == [
        "attorney_claimant", "attorney_defendant"]
    assert party_answer.requested_roles("¿Quiénes son las partes?") == []
    assert party_answer.requested_roles("¿Quiénes son los testigos?") is None


def test_identity_vs_content_question():
    assert party_answer.is_identity_question("¿Cuál es el nombre de la parte demandante?")
    assert party_answer.is_identity_question("¿Quiénes son los demandados?")
    assert not party_answer.is_identity_question("¿Qué dice la parte demandada?")
    assert not party_answer.is_identity_question(
        "En el proceso al demandante se le pagaron intereses")


def test_general_topic_followup_weather():
    history = [
        {"role": "user", "content": "¿Cómo está el clima en Obando?"},
        {"role": "assistant", "content": "Está soleado."},
    ]
    followup = query_rewrite.rewrite(
        "Seguro porque voy a salir y no llevo paraguas", history=history)
    assert any("obando" in variant.lower() for variant in followup)
    assert query_rewrite.needs_history(
        "Seguro porque voy a salir y no llevo paraguas", history)

    longer = history + [
        {"role": "user", "content": "Seguro porque voy a salir y no llevo paraguas"},
        {"role": "assistant", "content": "Sí, sigue soleado."},
    ]
    rain = query_rewrite.rewrite("¿Definitivamente no va a llover?", history=longer)
    assert any("obando" in variant.lower() for variant in rain)

    new_topic = query_rewrite.rewrite("Ahora háblame de los intereses", history=history)
    assert not any("obando" in variant.lower() for variant in new_topic)
    assert not query_rewrite.needs_history("Ahora háblame de los intereses", history)

    summary_history = [{
        "role": "user",
        "content": ("Resumen de la conversación previa:\n"
                    "- Usuario: ¿Cómo está el clima en Obando?\n"
                    "- Asistente: Está soleado."),
    }]
    later = query_rewrite.rewrite("¿Definitivamente no va a llover?", history=summary_history)
    assert any("obando" in variant.lower() for variant in later)


def test_role_followup_inherits_previous_topic():
    variants = query_rewrite.rewrite(
        "¿Qué dice la parte demandada?",
        history=[{"role": "user",
                  "content": "En el proceso al demandante se le pagaron intereses"}],
    )
    assert any("intereses" in variant.lower() for variant in variants)


def test_render_uses_party_roles_and_does_not_invent_defendants(monkeypatch):
    rows = [
        {"name": "Carlos Alfonso Garzón Gutiérrez", "role": "defendant",
         "entity_type": "person"},
        {"name": "Juan Carlos Garzón Gutiérrez", "role": "defendant",
         "entity_type": "person"},
        {"name": "José Rueda Avellaneda", "role": "claimant", "entity_type": "person"},
        {"name": "Alba Lucy Cock Álvarez", "role": "judge", "entity_type": "person"},
    ]
    monkeypatch.setattr(party_answer, "rows", lambda *_a, **_k: rows)

    text = party_answer.render_for_question(SimpleNamespace(), "case", "¿Quiénes son los demandados?")

    assert text is not None
    assert "Carlos Alfonso Garzón Gutiérrez" in text
    assert "Juan Carlos Garzón Gutiérrez" in text
    assert "José Rueda Avellaneda" not in text
    assert "Sujetos Procesales" not in text


def test_render_all_parties_groups_by_role(monkeypatch):
    rows = [
        {"name": "Carlos Alfonso Garzón Gutiérrez", "role": "defendant",
         "entity_type": "person"},
        {"name": "José Rueda Avellaneda", "role": "claimant", "entity_type": "person"},
        {"name": "Alba Lucy Cock Álvarez", "role": "judge", "entity_type": "person"},
        {"name": "Un Tercero", "role": "third_party", "entity_type": "person"},
    ]
    monkeypatch.setattr(party_answer, "rows", lambda *_a, **_k: rows)

    text = party_answer.render_for_question(SimpleNamespace(), "case", "¿Quiénes son las partes?")

    assert text is not None
    assert "Demandado" in text and "Demandante" in text
    assert "Juez" in text and "Tercero" in text
    assert f"({len(rows)} registros)" in text
