"""IT-BC — skills y agentes sembrados del chat (Fase 5): idempotencia, enlace de
skills (encapsulamiento siempre presente) y fusión al prompt del agente."""
from __future__ import annotations

import json

import pytest

from app.providers.llm import FakeLLM

pytestmark = pytest.mark.integration

CHAT_SKILLS = ["Encapsulamiento", "Grill-me (interrogatorio jurídico)", "Analista de documento",
               "Analista de video (audiencias)", "Corrector de evidencia", "Cronologista",
               "Cazador de contradicciones", "Relacionador de personas"]
CHAT_AGENTS = ["Asistente del expediente", "Grill-me jurídico", "Cronista probatorio"]


def test_it_bc_01_chat_skills_seeded_in_panel(client, auth):
    r = client.get("/v1/admin/skills", headers=auth("admin.alfa"))
    assert r.status_code == 200
    by_name = {s["name"]: s for s in r.json()}
    for name in CHAT_SKILLS:
        assert name in by_name, name
        assert by_name[name]["is_system"] is True
    # encapsulamiento va enlazada a los 3 agentes del chat
    assert by_name["Encapsulamiento"]["used_by"] >= 3


def test_it_bc_02_chat_agents_seeded_with_links(client, auth):
    skills = {s["name"]: s["id"] for s in client.get("/v1/admin/skills", headers=auth("admin.alfa")).json()}
    agents = client.get("/v1/admin/agents", headers=auth("admin.alfa")).json()
    by_name = {a["name"]: a for a in agents}
    for name in CHAT_AGENTS:
        assert name in by_name, name
        a = by_name[name]
        assert a["is_system"] is True and a["kind"] == "chat"
        assert a["system_prompt"]
        assert skills["Encapsulamiento"] in a["skills"], f"{name} debe llevar encapsulamiento"
    assert skills["Grill-me (interrogatorio jurídico)"] in by_name["Grill-me jurídico"]["skills"]


def test_it_bc_03_seeding_is_idempotent(client, auth):
    h = auth("admin.alfa")
    first_skills = len(client.get("/v1/admin/skills", headers=h).json())
    first_agents = len([a for a in client.get("/v1/admin/agents", headers=h).json() if a["kind"] == "chat"])
    client.get("/v1/admin/skills", headers=h)
    client.get("/v1/admin/agents", headers=h)
    assert len(client.get("/v1/admin/skills", headers=h).json()) == first_skills
    assert len([a for a in client.get("/v1/admin/agents", headers=h).json() if a["kind"] == "chat"]) == first_agents == 3


def test_it_bc_04_user_facing_agents_seed_without_admin_visit(client, auth):
    """El chat (que usa /v1/agents) obtiene los agentes listos sin abrir el panel."""
    r = client.get("/v1/agents", headers=auth("lawyer.beta"))  # org beta, sin visita al panel
    assert r.status_code == 200
    names = {a["name"] for a in r.json()}
    assert set(CHAT_AGENTS) <= names


def test_it_bc_05_grill_agent_merges_skill_prompts_at_query(client, auth, ids):
    """Elegir 'Grill-me jurídico' fusiona sus skills (interrogatorio + encapsulamiento) al agente."""
    h = auth("abogada.alfa")
    agents = client.get("/v1/agents", headers=h).json()
    grill = next(a for a in agents if a["name"] == "Grill-me jurídico")
    session = client.post(f"/v1/cases/{ids['pago']}/chats", headers=h, json={"agent_id": grill["id"]})
    assert session.status_code == 201, session.text
    sid = session.json()["id"]

    captured: dict[str, str] = {}

    def script(system, user):
        captured.setdefault("system", system)
        captured.setdefault("user", user)
        return json.dumps({"thought": "listo", "done": True})

    FakeLLM.script = script
    r = client.post(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h,
                    json={"content": "¿Quién presentó la demanda y qué contradice eso?"})
    assert r.status_code == 201, r.text
    system = captured["system"]
    assert "interrogatorio" in system.lower(), "la skill Grill-me debe fusionarse"
    assert "sin información del expediente" in system.lower() or "nunca busques en internet" in system.lower(), \
        "encapsulamiento debe fusionarse"
    assert "una pregunta" in system.lower()
