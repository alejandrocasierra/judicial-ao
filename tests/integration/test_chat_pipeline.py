"""IT-PIPE — contrato del pipeline de chat (formato, coherencia y streaming)."""
from __future__ import annotations

import json
from uuid import uuid4

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


def test_it_pipe_07_persists_the_whole_routing_decision(client, auth, ids, owner_db):
    """Sin `reason`/`confidence` no se puede auditar ni aprender del router."""
    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    body = _send(client, h, ids["pago"], sid, "¿Qué evidencia hay sobre pagos?")
    with owner_db.cursor() as cur:
        cur.execute("""SELECT intent, intent_confidence, intent_reason, intent_source, router_version
                         FROM chat_messages WHERE id = %s""",
                    (body["assistant_message"]["id"],))
        intent, confidence, reason, source, version = cur.fetchone()
    assert intent == "dato"
    assert 0.0 <= float(confidence) <= 1.0
    assert reason == "default_lookup"  # la pregunta que disparó todo el rediseño
    assert source == "rules"
    assert version


def test_it_pipe_08_feedback_is_one_verdict_per_user_and_message(client, auth, ids, owner_db):
    """El veredicto es la etiqueta verdadera: se actualiza, no se acumula."""
    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    body = _send(client, h, ids["pago"], sid, "¿En qué minuto se habla de pagos?")
    mid = body["assistant_message"]["id"]
    url = f"/v1/cases/{ids['pago']}/chats/{sid}/messages/{mid}/feedback"

    assert client.post(url, headers=h, json={"verdict": "rejected"}).status_code == 201
    # `wrong_intent` sin intención no aporta etiqueta: se rechaza.
    assert client.post(url, headers=h, json={"verdict": "wrong_intent"}).status_code == 422
    # Y `corrected_intent` no vale para un simple «me gusta».
    assert client.post(url, headers=h, json={"verdict": "accepted",
                                             "corrected_intent": "dato"}).status_code == 422

    r = client.post(url, headers=h, json={"verdict": "wrong_intent", "corrected_intent": "dato"})
    assert r.status_code == 201, r.text
    assert r.json()["corrected_intent"] == "dato"

    with owner_db.cursor() as cur:
        cur.execute("""SELECT count(*), max(verdict), max(corrected_intent), max(router_intent),
                              max(router_version)
                         FROM intent_feedback WHERE message_id = %s""", (mid,))
        total, verdict, corrected, router_intent, version = cur.fetchone()
    assert total == 1  # upsert: un veredicto por usuario y mensaje
    assert verdict == "wrong_intent" and corrected == "dato"
    assert router_intent == "navegar" and version

    # Un mensaje que no es de esta sesión no se puede valorar.
    missing = f"/v1/cases/{ids['pago']}/chats/{sid}/messages/{uuid4()}/feedback"
    assert client.post(missing, headers=h, json={"verdict": "accepted"}).status_code == 404


def test_it_pipe_09_feedback_becomes_exemplar_and_profile(client, auth, ids, owner_db):
    """El circuito completo: veredicto → ejemplo → perfil, sin intervención."""
    from app.services import intent_learning

    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    question = "¿En qué minuto se habla de pagos?"
    body = _send(client, h, ids["pago"], sid, question)
    mid = body["assistant_message"]["id"]
    url = f"/v1/cases/{ids['pago']}/chats/{sid}/messages/{mid}/feedback"

    with owner_db.cursor() as cur:
        cur.execute("SELECT organization_id, created_by FROM chat_sessions WHERE id = %s", (sid,))
        org_id, user_id = cur.fetchone()

    assert client.post(url, headers=h, json={"verdict": "accepted"}).status_code == 201
    report = intent_learning.rebuild(str(org_id), user_id=str(user_id))
    assert len(report) == 1
    # Con embeddings `fake` no hay espacio semántico: se mide, pero NUNCA se activa.
    assert report[0]["status"] == "shadow"
    assert report[0]["reason"] == "embedding_provider_not_semantic"

    fingerprint = intent_learning.question_hash(question)
    with owner_db.cursor() as cur:
        cur.execute("""SELECT label, source, weight FROM intent_exemplar
                        WHERE user_id = %s AND question_hash = %s""", (user_id, fingerprint))
        exemplar = cur.fetchone()
        cur.execute("SELECT status, version, exemplars FROM intent_profile WHERE user_id = %s",
                    (user_id,))
        profile = cur.fetchone()
    assert exemplar is not None
    assert exemplar[0] == "navegar" and exemplar[1] == "feedback"
    assert float(exemplar[2]) == 1.0
    assert profile[0] == "shadow" and profile[1] >= 1 and profile[2] >= 1

    # El veredicto vuelve en el GET: al recargar, el botón recuerda la elección.
    listed = client.get(f"/v1/cases/{ids['pago']}/chats/{sid}/messages?limit=100", headers=h)
    assert listed.status_code == 200
    saved = next(m for m in listed.json()["messages"] if m["id"] == mid)
    assert saved["verdict"] == "accepted" and saved["corrected_intent"] is None

    # Cambiar de opinión olvida el ejemplo: el error no se queda en el perfil.
    assert client.post(url, headers=h, json={"verdict": "rejected"}).status_code == 201
    intent_learning.rebuild(str(org_id), user_id=str(user_id))
    with owner_db.cursor() as cur:
        cur.execute("""SELECT count(*) FROM intent_exemplar
                        WHERE user_id = %s AND question_hash = %s""", (user_id, fingerprint))
        assert cur.fetchone()[0] == 0

    listed = client.get(f"/v1/cases/{ids['pago']}/chats/{sid}/messages?limit=100", headers=h)
    saved = next(m for m in listed.json()["messages"] if m["id"] == mid)
    assert saved["verdict"] == "rejected"


def test_it_pipe_10_forget_style_and_retention(client, auth, ids, owner_db):
    """«Olvidar mi estilo» borra el dato derivado; la retención caduca ejemplos."""
    from app.services import intent_learning

    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    body = _send(client, h, ids["pago"], sid, "¿En qué minuto se habla de pagos?")
    mid = body["assistant_message"]["id"]
    url = f"/v1/cases/{ids['pago']}/chats/{sid}/messages/{mid}/feedback"
    assert client.post(url, headers=h, json={"verdict": "accepted"}).status_code == 201

    with owner_db.cursor() as cur:
        cur.execute("SELECT organization_id, created_by FROM chat_sessions WHERE id = %s", (sid,))
        org_id, user_id = cur.fetchone()
    intent_learning.rebuild(str(org_id), user_id=str(user_id))

    forgotten = client.delete("/v1/auth/me/intent-profile", headers=h)
    assert forgotten.status_code == 200, forgotten.text
    assert forgotten.json()["exemplars_deleted"] >= 1
    with owner_db.cursor() as cur:
        cur.execute("SELECT count(*) FROM intent_exemplar WHERE user_id = %s", (user_id,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM intent_profile WHERE user_id = %s", (user_id,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM intent_feedback WHERE user_id = %s", (user_id,))
        assert cur.fetchone()[0] == 0
        cur.execute("""SELECT count(*) FROM audit_logs
                        WHERE action = 'intent_profile.forgotten' AND entity_id = %s""",
                    (str(user_id),))
        assert cur.fetchone()[0] >= 1

    # El olvido es durable: el job nocturno no puede reconstruirlo sin veredictos.
    fingerprint = intent_learning.question_hash("¿En qué minuto se habla de pagos?")
    intent_learning.rebuild(str(org_id), user_id=str(user_id))
    with owner_db.cursor() as cur:
        cur.execute("""SELECT count(*) FROM intent_exemplar
                        WHERE user_id = %s AND question_hash = %s""", (user_id, fingerprint))
        assert cur.fetchone()[0] == 0

    # Retención: un ejemplo de hace 90 días desaparece con 30 de retención.
    with owner_db.cursor() as cur:
        cur.execute("""INSERT INTO intent_exemplar
                         (organization_id, user_id, label, question, question_hash, source,
                          weight, created_at)
                       VALUES (%s, %s, 'dato', 'pregunta vieja', 'hash-retencion', 'feedback',
                               1.0, now() - interval '90 days')""", (org_id, user_id))
        owner_db.commit()
    purged = intent_learning.purge_expired(str(org_id), days=30)
    assert purged["exemplars"] >= 1
    with owner_db.cursor() as cur:
        cur.execute("""SELECT count(*) FROM intent_exemplar
                        WHERE user_id = %s AND question_hash = 'hash-retencion'""", (user_id,))
        assert cur.fetchone()[0] == 0
