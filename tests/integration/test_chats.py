"""IT-CHAT — backend multi-turn: sesiones, historial al agente, adjuntos "@"
con file_cards, paginación, archivo de sesiones y presupuesto/rate limits heredados."""
from __future__ import annotations

import json
import re
import uuid

import pytest

from app.providers.llm import FakeLLM

pytestmark = pytest.mark.integration


def _create_session(client, h, case_id, **kw):
    r = client.post(f"/v1/cases/{case_id}/chats", headers=h, json=kw)
    assert r.status_code == 201, r.text
    return r.json()


def _send(client, h, case_id, session_id, content, **kw):
    r = client.post(f"/v1/cases/{case_id}/chats/{session_id}/messages", headers=h,
                    json={"content": content, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _agent_script(captured: list[str]):
    """FakeLLM que juega el loop del agente: tool call → done → respuesta citando E1."""
    def script(system, user):
        captured.append(user)
        n = len(captured)
        if n == 1:
            return json.dumps({"thought": "buscar", "tool": "search_case",
                               "arguments": {"query": "demanda pago", "k": 3}})
        if n == 2:
            return json.dumps({"thought": "listo", "done": True})
        # síntesis: la afirmación usa el propio texto de la evidencia (grounding garantizado)
        m = re.search(r'<evidence id="(E\d+)"[^>]*>\s*(.{10,160}?)[.\n]', user, re.S)
        if m:
            return json.dumps({"claims": [{"text": m.group(2).strip()[:120], "citations": [m.group(1)]}],
                               "uncertainties": []})
        return json.dumps({"claims": [], "uncertainties": []})
    return script


def test_it_chat_01_session_crud(client, auth, ids):
    h = auth("abogada.alfa")
    s = _create_session(client, h, ids["pago"])
    assert s["id"] and s["title"] == ""
    listing = client.get(f"/v1/cases/{ids['pago']}/chats", headers=h).json()
    assert any(x["id"] == s["id"] for x in listing)
    # agente inexistente → 404
    r = client.post(f"/v1/cases/{ids['pago']}/chats", headers=h, json={"agent_id": str(uuid.uuid4())})
    assert r.status_code == 404 and r.json()["error"]["code"] == "AGENT_NOT_FOUND"


def test_it_chat_02_send_message_persists_with_attachments_and_file_cards(client, auth, ids):
    h = auth("abogada.alfa")
    doc = ids["docs"]["01_demanda.pdf"]
    s = _create_session(client, h, ids["pago"])
    captured: list[str] = []
    FakeLLM.script = _agent_script(captured)
    body = _send(client, h, ids["pago"], s["id"], "¿Qué dice la demanda sobre el pago?",
                 attachments=[{"kind": "document", "id": doc["id"], "name": doc["filename"]}])
    assert body["answer"], body
    assert body["user_message"]["id"] and body["assistant_message"]["id"]
    assert any(c.get("document_id") == doc["id"] for c in body["file_cards"]), body["file_cards"]
    # persistencia: ambos mensajes quedan en el historial
    msgs = client.get(f"/v1/cases/{ids['pago']}/chats/{s['id']}/messages", headers=h).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["attachments"][0]["id"] == doc["id"]
    assert msgs[1]["citations"] or msgs[1]["content"]
    # la sesión quedó titulada con la primera pregunta
    session = next(x for x in client.get(f"/v1/cases/{ids['pago']}/chats", headers=h).json() if x["id"] == s["id"])
    assert session["title"].startswith("¿Qué dice la demanda")
    assert session["message_count"] == 2


def test_it_chat_03_followup_receives_conversation_history(client, auth, ids):
    """El follow-up ('¿y quién más estaba?') llega al agente CON los turnos previos."""
    h = auth("abogada.alfa")
    s = _create_session(client, h, ids["pago"])
    captured: list[str] = []
    FakeLLM.script = _agent_script(captured)
    _send(client, h, ids["pago"], s["id"], "¿Quién presentó la demanda de pago?")
    before = len(captured)
    FakeLLM.script = _agent_script(captured)
    _send(client, h, ids["pago"], s["id"], "¿y quién más estaba presente?")
    first_turn_of_second_message = captured[before]
    assert "<conversation_history>" in first_turn_of_second_message
    assert "¿Quién presentó la demanda de pago?" in first_turn_of_second_message


def test_it_chat_04_messages_pagination(client, auth, ids):
    h = auth("abogada.alfa")
    s = _create_session(client, h, ids["pago"])
    captured: list[str] = []
    FakeLLM.script = _agent_script(captured)
    for i in range(3):
        _send(client, h, ids["pago"], s["id"], f"pregunta número {i} sobre la demanda y el pago")
    page1 = client.get(f"/v1/cases/{ids['pago']}/chats/{s['id']}/messages?limit=4&offset=0", headers=h).json()
    page2 = client.get(f"/v1/cases/{ids['pago']}/chats/{s['id']}/messages?limit=4&offset=4", headers=h).json()
    assert len(page1["messages"]) == 4 and len(page2["messages"]) == 2
    assert page1["messages"][-1]["id"] != page2["messages"][0]["id"]


def test_it_chat_05_archive_permissions(client, auth, ids):
    creator = auth("abogada.alfa")
    other = auth("analista.alfa")
    admin = auth("admin.alfa")
    s = _create_session(client, creator, ids["pago"])
    # otro usuario no administrador no puede archivarla
    r = client.delete(f"/v1/cases/{ids['pago']}/chats/{s['id']}", headers=other)
    assert r.status_code == 403
    # el creador sí
    assert client.delete(f"/v1/cases/{ids['pago']}/chats/{s['id']}", headers=creator).status_code == 200
    assert not any(x["id"] == s["id"] for x in client.get(f"/v1/cases/{ids['pago']}/chats", headers=creator).json())
    # y un admin de la org puede archivar cualquier sesión
    s2 = _create_session(client, other, ids["pago"])
    assert client.delete(f"/v1/cases/{ids['pago']}/chats/{s2['id']}", headers=admin).status_code == 200


def test_it_chat_06_unknown_session_is_404(client, auth, ids):
    h = auth("abogada.alfa")
    r = client.get(f"/v1/cases/{ids['pago']}/chats/{uuid.uuid4()}/messages", headers=h)
    assert r.status_code == 404 and r.json()["error"]["code"] == "CHAT_NOT_FOUND"
    r = client.post(f"/v1/cases/{ids['pago']}/chats/{uuid.uuid4()}/messages", headers=h,
                    json={"content": "hola expediente"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "CHAT_NOT_FOUND"


def test_it_chat_07_locate_attaches_all_occurrences(client, auth, ids):
    """«¿Dónde se habla de X?» adjunta TODAS las apariciones (archivo+página/minuto) para que la IA las cite."""
    h = auth("abogada.alfa")
    s = _create_session(client, h, ids["pago"])
    r = client.post(f"/v1/cases/{ids['pago']}/chats/{s['id']}/messages", headers=h,
                    json={"content": "¿Dónde se habla de DEMANDA en los archivos?"})
    assert r.status_code == 201, r.text
    body = r.json()
    occ = body.get("occurrences") or []
    assert len(occ) >= 1, body
    assert all("filename" in c for c in occ)


def test_it_chat_08_response_includes_all_occurrences(client, auth, ids):
    """La respuesta adjunta TODAS las apariciones del término (sin tope por archivo)."""
    h = auth("abogada.alfa")
    s = _create_session(client, h, ids["pago"])
    r = client.post(f"/v1/cases/{ids['pago']}/chats/{s['id']}/messages", headers=h,
                    json={"content": "¿Qué dice la demanda sobre el pago?"})
    assert r.status_code == 201, r.text
    occ = r.json().get("occurrences") or []
    assert len(occ) >= 2, occ
    assert all("filename" in c for c in occ)
