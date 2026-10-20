"""IT-PIPE — contrato del pipeline de chat (formato, coherencia y streaming)."""
from __future__ import annotations

import json

import pytest

from app.providers.llm import FakeLLM

pytestmark = pytest.mark.integration


def _session(client, h, case_id):
    r = client.post(f"/v1/cases/{case_id}/chats", headers=h, json={})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _send(client, h, case_id, session_id, content, **kw):
    r = client.post(f"/v1/cases/{case_id}/chats/{session_id}/messages", headers=h,
                    json={"content": content, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_it_pipe_01_substantive_question_is_not_navigation(client, auth, ids, owner_db):
    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    body = _send(client, h, ids["pago"], sid, "¿Qué evidencia hay sobre pagos?")
    assert body["answer_kind"] != "navigational", body
    assert body["intent"] == "dato"
    assert (body.get("occurrences") or []) == []
    assert len(body.get("citations") or []) <= 4
    assert "Menciones del término" not in body["answer"]
    # El tipo decidido por el compositor queda persistido para el historial.
    with owner_db.cursor() as row:
        row.execute("SELECT answer_kind, intent FROM chat_messages WHERE id = %s",
                    (body["assistant_message"]["id"],))
        answer_kind, intent = row.fetchone()
    assert answer_kind == body["answer_kind"] and intent == "dato"


def test_it_pipe_02_navigation_returns_grouped_occurrences(client, auth, ids):
    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    body = _send(client, h, ids["pago"], sid, "¿Dónde se habla de pago?")
    assert body["answer_kind"] == "navigational"
    assert body["intent"] == "navegar"
    occ = body["occurrences"]
    assert occ and len(occ) <= 8
    assert all(c.get("group") for c in occ)
    assert body["answer"].startswith("Menciones del término «pago»")
    assert "No son la respuesta" in body["answer"]


def test_it_pipe_03_insufficient_is_one_message_without_sources(client, auth, ids):
    h = auth("abogada.alfa")
    # El expediente sin documentos se identifica por su título (no por "el otro").
    cases = client.get("/v1/cases", headers=h).json()
    empty_id = next((c["id"] for c in cases if "sin documentos" in (c["title"] or "").lower()),
                    ids["vacio"])
    sid = _session(client, h, empty_id)
    body = _send(client, h, empty_id, sid, "¿Quién firmó el contrato de arrendamiento?")
    assert body["answer_kind"] == "insufficient"
    assert body["claims"] == [] and body["citations"] == []
    assert (body.get("occurrences") or []) == []
    assert body["answer"] not in body["uncertainties"]
    assert "**Fuentes**" not in body["answer"]


def test_it_pipe_04_pagination_keeps_the_latest_messages(client, auth, ids, owner_db):
    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    with owner_db.cursor() as cur:
        cur.execute("SELECT organization_id FROM chat_sessions WHERE id = %s", (sid,))
        org_id = cur.fetchone()[0]
        for i in range(101):
            cur.execute("""INSERT INTO chat_messages (organization_id, session_id, role, content, created_at)
                           VALUES (%s, %s, 'assistant', %s,
                                   now() + make_interval(secs => %s))""",
                        (org_id, sid, f"mensaje histórico {i}", i))
        cur.execute("""INSERT INTO chat_messages (organization_id, session_id, role, content, created_at)
                       VALUES (%s, %s, 'assistant', 'EL ÚLTIMO MENSAJE',
                               now() + make_interval(secs => 1000)) RETURNING id""",
                    (org_id, sid))
        last_id = str(cur.fetchone()[0])
    owner_db.commit()
    page = client.get(f"/v1/cases/{ids['pago']}/chats/{sid}/messages?limit=50&offset=0", headers=h).json()
    assert page["messages"][-1]["id"] == last_id
    assert page["messages"][-1]["content"] == "EL ÚLTIMO MENSAJE"
    assert len(page["messages"]) == 50


def test_it_pipe_06_retry_reuses_the_pending_user_message(client, auth, ids, owner_db):
    """El POST de respaldo del SSE no duplica el mensaje del usuario."""
    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    with owner_db.cursor() as cur:
        cur.execute("SELECT organization_id FROM chat_sessions WHERE id = %s", (sid,))
        org_id = cur.fetchone()[0]
        cur.execute("""INSERT INTO chat_messages (organization_id, session_id, role, content)
                       VALUES (%s, %s, 'user', %s)""",
                    (org_id, sid, "pregunta idempotente sobre el pago"))
    owner_db.commit()
    body = _send(client, h, ids["pago"], sid, "pregunta idempotente sobre el pago")
    assert body["assistant_message"]["id"]
    msgs = client.get(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h).json()["messages"]
    users = [m for m in msgs if m["role"] == "user" and m["content"] == "pregunta idempotente sobre el pago"]
    assert len(users) == 1, msgs


def test_it_pipe_05_stream_emits_stage_token_and_done(client, auth, ids):
    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    FakeLLM.script = None
    events: list[tuple[str, dict]] = []
    with client.stream("POST", f"/v1/cases/{ids['pago']}/chats/{sid}/messages/stream",
                       headers=h, json={"content": "¿Qué dice la demanda sobre el pago?"}) as response:
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        event = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: ") and event:
                events.append((event, json.loads(line[6:])))
    names = [name for name, _ in events]
    assert set(names) <= {"stage", "token", "done", "error"}
    assert "stage" in names and "token" in names and "done" in names
    stages = {data.get("stage") for name, data in events if name == "stage"}
    assert {"accepted", "routing", "done"} <= stages
    tokens = "".join(data["text"] for name, data in events if name == "token")
    done = next(data for name, data in events if name == "done")
    assert done["answer"].startswith(tokens)
    assert done["assistant_message"]["id"]
