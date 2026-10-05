"""IT-COR — corrección conversacional completa (Fase 6).

Flujo: el usuario dice en lenguaje natural que algo está mal → el agente propone
la vista previa (queda PENDIENTE en el servidor) → el usuario confirma con un
"sí" cualquiera → el servidor aplica la corrección de forma DETERMINISTA
(sin volver a llamar al LLM) y propaga a BD + reviews + pgvector + grafo.
"""
from __future__ import annotations

import json
import uuid

import pytest
from helpers import fetch

from app.providers.llm import FakeLLM

pytestmark = pytest.mark.integration


def _session(client, h, case_id):
    r = client.post(f"/v1/cases/{case_id}/chats", headers=h, json={})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _propose(doc_id, page, new_text, captured=None):
    state = {"n": 0}

    def script(system, user):
        if captured is not None:
            captured.append(user)
        state["n"] += 1
        if state["n"] == 1:
            return json.dumps({"thought": "corregir", "tool": "correct_ocr_page",
                               "arguments": {"document_id": doc_id, "page_number": page,
                                             "new_text": new_text, "reason": "el usuario lo indicó"}})
        return json.dumps({"thought": "listo", "done": True})
    return script


def _never_llm(*_a, **_k):
    raise AssertionError("el flujo determinista no debe llamar al LLM")


def test_it_cor_01_full_flow_natural_language_then_confirmation(client, auth, ids, org_ids, owner_db):
    h = auth("abogada.alfa")
    doc_id = client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()[0]["id"]
    n = client.get(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages", headers=h).json()["pages"][0]["page_number"]
    snap = fetch(owner_db, "SELECT text, ocr_confidence, needs_review, human_corrected FROM document_pages "
                           "WHERE document_id = %s AND page_number = %s", (doc_id, n))[0]
    chunks_before = fetch(owner_db, "SELECT count(*) FROM chunks WHERE document_id = %s", (doc_id,))[0][0]
    new_text = f"Contenido corregido en lenguaje natural {uuid.uuid4().hex[:8]}."
    sid = _session(client, h, ids["pago"])
    captured: list[str] = []
    FakeLLM.script = _propose(doc_id, n, new_text, captured)
    try:
        # 1) El usuario NO dice "esto está malo, es así": lo dice natural.
        r = client.post(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h, json={
            "content": "Oye, el OCR de esa página está mal: en realidad dice otra cosa y le falta el apellido"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["pending_correction"] and body["pending_correction"]["tool"] == "correct_ocr_page"
        # pista inyectada al agente (detección determinista de intención)
        assert any("<context_hints>" in u for u in captured), captured
        # sin confirmar, la página NO cambia
        assert fetch(owner_db, "SELECT text FROM document_pages WHERE document_id=%s AND page_number=%s",
                     (doc_id, n))[0][0] == snap[0]
        pending = fetch(owner_db, "SELECT tool, resolved_at FROM chat_pending_corrections WHERE session_id = %s", (sid,))
        assert pending and pending[0][0] == "correct_ocr_page" and pending[0][1] is None

        # 2) El usuario confirma con un "sí" cualquiera: se aplica SIN LLM.
        FakeLLM.script = _never_llm
        r = client.post(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h, json={"content": "sí, dale"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["pending_correction"] is None and "Corregida" in body["answer"]
        row = fetch(owner_db, "SELECT text, human_corrected FROM document_pages WHERE document_id=%s AND page_number=%s",
                    (doc_id, n))[0]
        assert row[0] == new_text and row[1] is True
        rev = fetch(owner_db, """SELECT original_output->>'text', human_output->>'text' FROM reviews
            WHERE entity_type='document_page'
              AND entity_id = (SELECT id FROM document_pages WHERE document_id=%s AND page_number=%s)
            ORDER BY created_at DESC LIMIT 1""", (doc_id, n))[0]
        assert rev[0] == snap[0] and rev[1] == new_text
        assert fetch(owner_db, "SELECT count(*) FROM chunks WHERE document_id = %s AND text LIKE %s",
                     (doc_id, "%corregido en lenguaje natural%"))[0][0] >= 1
        p2 = fetch(owner_db, "SELECT resolved_at, resolved_reason FROM chat_pending_corrections WHERE session_id = %s", (sid,))
        assert p2 and p2[0][0] is not None and p2[0][1] == "applied"
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE document_pages SET text=%s, ocr_confidence=%s, needs_review=%s, human_corrected=%s "
                        "WHERE document_id=%s AND page_number=%s", (snap[0], snap[1], snap[2], snap[3], doc_id, n))
            if chunks_before == 0:
                cur.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
        owner_db.commit()


def test_it_cor_02_cancellation_leaves_case_untouched(client, auth, ids, org_ids, owner_db):
    h = auth("abogada.alfa")
    doc_id = client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()[0]["id"]
    n = client.get(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages", headers=h).json()["pages"][0]["page_number"]
    before = fetch(owner_db, "SELECT text FROM document_pages WHERE document_id=%s AND page_number=%s", (doc_id, n))[0][0]
    sid = _session(client, h, ids["pago"])
    FakeLLM.script = _propose(doc_id, n, "No debería aplicarse nunca.")
    r = client.post(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h, json={
        "content": "el nombre de esa página está equivocado"})
    assert r.status_code == 201 and r.json()["pending_correction"]
    FakeLLM.script = _never_llm
    r = client.post(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h, json={"content": "no, déjalo"})
    assert r.status_code == 201 and r.json()["pending_correction"] is None
    assert fetch(owner_db, "SELECT text FROM document_pages WHERE document_id=%s AND page_number=%s",
                 (doc_id, n))[0][0] == before
    res = fetch(owner_db, "SELECT resolved_reason FROM chat_pending_corrections WHERE session_id = %s", (sid,))
    assert res and res[0][0] == "cancelled"


def test_it_cor_03_reprocess_question_proposes_then_applies(client, auth, ids, org_ids, monkeypatch, owner_db):
    """«¿Cómo mejoramos este OCR?» → diagnóstico (pendiente) → confirmar → reproceso encolado."""
    from app.services.case_tools import write as ct_write

    monkeypatch.setattr(ct_write, "_safe_enqueue", lambda *a, **k: None)  # evita correr el pipeline
    h = auth("abogada.alfa")
    doc_id = client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()[0]["id"]
    sid = _session(client, h, ids["pago"])
    state = {"n": 0}

    def script(system, user):
        state["n"] += 1
        if state["n"] == 1:
            return json.dumps({"thought": "diagnosticar", "tool": "suggest_reprocess",
                               "arguments": {"document_id": doc_id}})
        return json.dumps({"thought": "listo", "done": True})

    FakeLLM.script = script
    r = client.post(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h, json={
        "content": "¿cómo podemos mejorar el OCR de este documento? tiene baja calidad"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pending_correction"] and body["pending_correction"]["tool"] == "suggest_reprocess"
    assert "Diagnóstico" in body["answer"]

    before = fetch(owner_db, "SELECT count(*) FROM jobs WHERE case_id = %s AND job_type='file_ingest'", (ids["pago"],))[0][0]
    FakeLLM.script = _never_llm
    r = client.post(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h, json={"content": "dale"})
    assert r.status_code == 201, r.text
    assert r.json()["pending_correction"] is None
    after = fetch(owner_db, "SELECT count(*) FROM jobs WHERE case_id = %s AND job_type='file_ingest'", (ids["pago"],))[0][0]
    assert after == before + 1, "confirmar el reproceso debe encolar un file_ingest"


def test_it_cor_04_confirmation_without_pending_falls_through(client, auth, ids):
    """Un 'sí' sin corrección pendiente no rompe: sigue el flujo normal del agente."""
    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    FakeLLM.script = lambda system, user: json.dumps({"thought": "nada", "done": True})
    r = client.post(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h, json={"content": "sí"})
    assert r.status_code == 201, r.text


def test_it_cor_05_chat_occurrences_are_persisted(client, auth, ids, owner_db):
    """El bloque «Aparece en N ubicaciones» debe sobrevivir al cambio de chat y a nuevos mensajes."""
    h = auth("abogada.alfa")
    sid = _session(client, h, ids["pago"])
    FakeLLM.script = lambda system, user: json.dumps({"thought": "listo", "done": True})
    r = client.post(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h, json={"content": "transferencia"})
    assert r.status_code == 201, r.text
    body = r.json()
    occ = body["occurrences"]
    assert occ, "el término existe en la semilla; debe haber apariciones"
    # Se guardaron en el mensaje del asistente...
    row = fetch(owner_db, "SELECT occurrences FROM chat_messages WHERE id = %s",
                (body["assistant_message"]["id"],))[0][0]
    assert row == occ
    # ...y el historial los devuelve (sobrevive al recargar/cambiar de conversación).
    msgs = client.get(f"/v1/cases/{ids['pago']}/chats/{sid}/messages", headers=h).json()["messages"]
    assistant = [m for m in msgs if m["role"] == "assistant"][-1]
    assert assistant["occurrences"] == occ
