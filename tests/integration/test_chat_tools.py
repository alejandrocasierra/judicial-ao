"""IT-CT — chat IA: adjuntos "@" reales, tools de lectura y correcciones OCR/ASR
desde la capa case_tools contra PostgreSQL real (BD → reviews → pgvector → grafo)."""
from __future__ import annotations

import uuid

import pytest
from helpers import fetch
from sqlalchemy import text as sa_text

from app.core.db import tx
from app.services import case_tools

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Adjuntos "@" en /query
# ---------------------------------------------------------------------------

def test_it_ct_01_query_with_attachment_returns_file_card(client, auth, ids):
    doc = ids["docs"]["01_demanda.pdf"]
    r = client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"), json={
        "question": "¿Qué dice la demanda sobre el pago?", "mode": "document",
        "attachments": [{"kind": "document", "id": doc["id"], "name": doc["filename"]}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(c.get("document_id") == doc["id"] and c["download_path"].endswith("/download")
               for c in body["file_cards"]), body["file_cards"]


def test_it_ct_02_unknown_attachment_is_ignored(client, auth, ids):
    r = client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"), json={
        "question": "pago transferencia contrato", "mode": "fact_lookup",
        "attachments": [{"kind": "document", "id": str(uuid.uuid4()), "name": "fantasma.pdf"}]})
    assert r.status_code == 200, r.text
    assert r.json()["file_cards"] == []


# ---------------------------------------------------------------------------
# Tool de lectura por minuto (ASR)
# ---------------------------------------------------------------------------

def test_it_ct_03_search_transcript_by_time_range(client, auth, ids, org_ids):
    h = auth("abogada.alfa")
    media = client.get(f"/v1/cases/{ids['pago']}/media", headers=h).json()
    assert media, "las semillas deben incluir un medio transcrito"
    mid = media[0]["id"]
    segs = client.get(f"/v1/cases/{ids['pago']}/media/{mid}/segments", headers=h).json()["segments"]
    assert segs, "el medio semilla debe tener segmentos"
    s0 = segs[0]
    ctx = case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=None)
    with tx(org_ids["alfa"], None) as conn:
        items = case_tools.execute(conn, ids["pago"], "search_transcript_by_time",
                                   {"media_id": mid, "from_minute": s0["start_ms"] / 60000,
                                    "to_minute": s0["end_ms"] / 60000, "k": 5}, ctx=ctx)
    assert any(it["segment_id"] == s0["id"] for it in items), items
    for it in items:
        assert it["source_type"] == "transcript_segment"
        assert ":" in it["start_mmss"]  # 'mm:ss' listo para "¿en qué minuto?"
        assert "speaker" in it


# ---------------------------------------------------------------------------
# Corrección OCR desde el chat (preview → confirm → propagación)
# ---------------------------------------------------------------------------

def test_it_ct_04_correct_ocr_page_via_tool_propagates(client, auth, ids, org_ids, owner_db):
    h = auth("abogada.alfa")
    actor = client.get("/v1/auth/me", headers=h).json()["id"]
    doc_id = client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()[0]["id"]
    n = client.get(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages", headers=h).json()["pages"][0]["page_number"]
    snap = fetch(owner_db, "SELECT text, ocr_confidence, needs_review, human_corrected FROM document_pages "
                           "WHERE document_id = %s AND page_number = %s", (doc_id, n))[0]
    chunks_before = fetch(owner_db, "SELECT count(*) FROM chunks WHERE document_id = %s", (doc_id,))[0][0]
    new_text = f"Texto corregido desde el chat {uuid.uuid4().hex[:8]}."
    ctx = case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=actor)
    try:
        # 1) Sin confirmar: solo vista previa, la BD no cambia.
        with tx(org_ids["alfa"], actor) as conn:
            preview = case_tools.execute(conn, ids["pago"], "correct_ocr_page",
                                         {"document_id": doc_id, "page_number": n, "new_text": new_text,
                                          "reason": "el usuario lo indicó en el chat"}, ctx=ctx)
        assert preview[0]["source_type"] == "correction_preview"
        assert preview[0]["requires_confirmation"] is True and preview[0]["old_text"] == snap[0]
        assert fetch(owner_db, "SELECT text FROM document_pages WHERE document_id = %s AND page_number = %s",
                     (doc_id, n))[0][0] == snap[0]

        # 2) Confirmada: BD + human_corrected + reviews (original preservado).
        with tx(org_ids["alfa"], actor) as conn:
            result = case_tools.execute(conn, ids["pago"], "correct_ocr_page",
                                        {"document_id": doc_id, "page_number": n, "new_text": new_text,
                                         "reason": "el usuario lo indicó en el chat", "confirm": True}, ctx=ctx)
        # La propagación (pgvector/grafo) se difirió a post-commit: se drena tras el commit.
        case_tools.drain_post_commit(ctx)
        assert result[0]["source_type"] == "correction_result"
        row = fetch(owner_db, "SELECT text, human_corrected FROM document_pages "
                              "WHERE document_id = %s AND page_number = %s", (doc_id, n))[0]
        assert row[0] == new_text and row[1] is True
        review = fetch(owner_db, """SELECT original_output->>'text', human_output->>'text', action, reason
            FROM reviews WHERE entity_type = 'document_page'
              AND entity_id = (SELECT id FROM document_pages WHERE document_id = %s AND page_number = %s)
            ORDER BY created_at DESC LIMIT 1""", (doc_id, n))[0]
        assert review[0] == snap[0] and review[1] == new_text and review[2] == "EDIT"
        # pgvector: el documento quedó reindexado con el texto corregido.
        assert fetch(owner_db, "SELECT count(*) FROM chunks WHERE document_id = %s AND text LIKE %s",
                     (doc_id, "%corregido desde el chat%"))[0][0] >= 1
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE document_pages SET text=%s, ocr_confidence=%s, needs_review=%s, human_corrected=%s "
                        "WHERE document_id=%s AND page_number=%s", (snap[0], snap[1], snap[2], snap[3], doc_id, n))
            if chunks_before == 0:
                cur.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
        owner_db.commit()


def test_it_ct_05_correct_transcript_segment_via_tool(client, auth, ids, org_ids, owner_db):
    h = auth("abogada.alfa")
    actor = client.get("/v1/auth/me", headers=h).json()["id"]
    media = client.get(f"/v1/cases/{ids['pago']}/media", headers=h).json()
    mid = media[0]["id"]
    seg = client.get(f"/v1/cases/{ids['pago']}/media/{mid}/segments", headers=h).json()["segments"][0]
    snap = fetch(owner_db, "SELECT text, confidence, needs_review FROM transcript_segments WHERE id = %s",
                 (seg["id"],))[0]
    new_text = f"Intervención corregida desde el chat {uuid.uuid4().hex[:8]}."
    ctx = case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=actor)
    try:
        with tx(org_ids["alfa"], actor) as conn:
            preview = case_tools.execute(conn, ids["pago"], "correct_transcript_segment",
                                         {"media_id": mid, "segment_id": seg["id"], "new_text": new_text}, ctx=ctx)
        assert preview[0]["source_type"] == "correction_preview" and preview[0]["old_text"] == snap[0]
        with tx(org_ids["alfa"], actor) as conn:
            result = case_tools.execute(conn, ids["pago"], "correct_transcript_segment",
                                        {"media_id": mid, "segment_id": seg["id"], "new_text": new_text,
                                         "confirm": True}, ctx=ctx)
        case_tools.drain_post_commit(ctx)
        assert result[0]["source_type"] == "correction_result"
        row = fetch(owner_db, "SELECT text, confidence, needs_review FROM transcript_segments WHERE id = %s",
                    (seg["id"],))[0]
        assert row[0] == new_text and float(row[1]) == 1.0 and row[2] is False
        review = fetch(owner_db, """SELECT original_output->>'text', human_output->>'text' FROM reviews
            WHERE entity_type = 'transcript_segment' AND entity_id = %s ORDER BY created_at DESC LIMIT 1""",
                       (seg["id"],))[0]
        assert review[0] == snap[0] and review[1] == new_text
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE transcript_segments SET text=%s, confidence=%s, needs_review=%s WHERE id=%s",
                        (snap[0], snap[1], snap[2], seg["id"]))
        owner_db.commit()


# ---------------------------------------------------------------------------
# Diagnóstico de calidad OCR (suggest_reprocess, sin confirmar no muta)
# ---------------------------------------------------------------------------

def test_it_ct_06_suggest_reprocess_diagnoses_without_mutating(client, auth, ids, org_ids, owner_db):
    h = auth("abogada.alfa")
    doc_id = client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()[0]["id"]
    status_before = fetch(owner_db, "SELECT processing_status FROM documents WHERE id = %s", (doc_id,))[0][0]
    ctx = case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=None)
    with tx(org_ids["alfa"], None) as conn:
        out = case_tools.execute(conn, ids["pago"], "suggest_reprocess", {"document_id": doc_id}, ctx=ctx)
    assert out[0]["source_type"] == "correction_preview"
    assert "Diagnóstico" in out[0]["text"] and out[0]["recommended_mode"] in ("basico", "document_ai")
    assert fetch(owner_db, "SELECT processing_status FROM documents WHERE id = %s", (doc_id,))[0][0] == status_before


# ---------------------------------------------------------------------------
# Skills fusionadas al prompt del agente (fix: skills ya no son data muerta)
# ---------------------------------------------------------------------------

def test_it_ct_07_agent_prompt_includes_skill_prompts(client, auth, ids, org_ids):
    marker = f"INSTRUCCION-SKILL-{uuid.uuid4().hex[:8]}"
    with tx(org_ids["alfa"], None) as conn:
        skill = conn.execute(
            sa_text("INSERT INTO skills (organization_id, name, system_prompt) VALUES (:o, :n, :sp) RETURNING id"),
            {"o": org_ids["alfa"], "n": f"Skill prueba {marker}", "sp": f"Regla especial: {marker}"}).first()
        agent_row = conn.execute(
            sa_text("INSERT INTO agents (organization_id, name, system_prompt, skills) VALUES (:o, :n, :sp, :sk) RETURNING id"),
            {"o": org_ids["alfa"], "n": f"Agente prueba {marker}", "sp": "Base.",
             "sk": [str(skill[0])]}).first()
    from app.services import agent as agent_svc
    with tx(org_ids["alfa"], None) as conn:
        prompt = agent_svc.load_agent_prompt(conn, str(agent_row[0]))
    assert prompt and marker in prompt and "Base." in prompt


# ---------------------------------------------------------------------------
# Fase 6+: búsqueda por hablante y fallback de find_person a fuentes citables
# ---------------------------------------------------------------------------

def test_it_ct_08_speaker_name_search_returns_their_segments(client, auth, ids, org_ids):
    """«¿en qué minuto habló Paola?» debe encontrar los segmentos del HABLANTE, no solo menciones."""
    label = next(iter(ids["speakers"]))
    ctx = case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=None)
    with tx(org_ids["alfa"], None) as conn:
        items = case_tools.execute(conn, ids["pago"], "search_transcript_by_time",
                                   {"query": label, "k": 10}, ctx=ctx)
    assert items, "debe encontrar segmentos del hablante"
    assert all("speaker" in it for it in items)
    starts = [it["start_ms"] for it in items]
    assert starts == sorted(starts), "los resultados deben ir en orden cronológico"


def test_it_ct_09_find_person_returns_citable_sources(client, auth, ids, org_ids):
    """«¿quién es X?» no se rinde si no hay ficha en el grafo: cita documentos/transcripciones.
    Usa un término presente en la semilla (la demanda contiene 'DEMANDA')."""
    ctx = case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=None)
    with tx(org_ids["alfa"], None) as conn:
        items = case_tools.execute(conn, ids["pago"], "find_person", {"name": "DEMANDA"}, ctx=ctx)
    assert items, "debe devolver algo para un término presente en el expediente"
    assert any(it["source_type"] in ("document_page", "transcript_segment") for it in items)


def test_it_ct_10_document_tools_accept_filename(client, auth, ids, org_ids):
    """El modelo suele pasar el NOMBRE del archivo; las tools deben resolverlo (no solo UUID)."""
    fname = next(iter(ids["docs"]))
    ctx = case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=None)
    with tx(org_ids["alfa"], None) as conn:
        out = case_tools.execute(conn, ids["pago"], "get_document_page",
                                 {"document_id": fname, "page_number": 1}, ctx=ctx)
    assert out and out[0]["source_type"] == "document_page" and out[0]["page_number"] == 1


def test_it_ct_11_rename_speaker_via_tool_propagates(client, auth, ids, org_ids, owner_db):
    """El chat puede renombrar a un hablante: preview → confirm → BD + reviews + pgvector."""
    h = auth("abogada.alfa")
    actor = client.get("/v1/auth/me", headers=h).json()["id"]
    spk = ids["speakers"]["SPK-02"]
    mid = client.get(f"/v1/cases/{ids['pago']}/media", headers=h).json()[0]["id"]
    snap = fetch(owner_db, "SELECT display_name, version FROM speakers WHERE id = %s", (spk["id"],))[0]
    chunks_before = fetch(owner_db, "SELECT count(*) FROM chunks WHERE media_id = %s", (mid,))[0][0]
    new_name = f"Hablante Test {uuid.uuid4().hex[:6]}"
    ctx = case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=actor)
    try:
        # 1) Sin confirmar: preview y la BD no cambia.
        with tx(org_ids["alfa"], actor) as conn:
            preview = case_tools.execute(conn, ids["pago"], "rename_speaker",
                                         {"speaker_id": spk["id"], "new_name": new_name, "reason": "prueba"}, ctx=ctx)
        assert preview[0]["source_type"] == "correction_preview" and preview[0]["requires_confirmation"] is True
        assert fetch(owner_db, "SELECT display_name FROM speakers WHERE id = %s", (spk["id"],))[0][0] == snap[0]
        # 2) Confirmado: BD + review + pgvector.
        with tx(org_ids["alfa"], actor) as conn:
            result = case_tools.execute(conn, ids["pago"], "rename_speaker",
                                        {"speaker_id": spk["id"], "new_name": new_name,
                                         "reason": "prueba", "confirm": True}, ctx=ctx)
        case_tools.drain_post_commit(ctx)
        assert result[0]["source_type"] == "correction_result"
        assert fetch(owner_db, "SELECT display_name FROM speakers WHERE id = %s", (spk["id"],))[0][0] == new_name
        rev = fetch(owner_db, """SELECT original_output->>'display_name', human_output->>'display_name'
            FROM reviews WHERE entity_type = 'speaker' AND entity_id = %s ORDER BY created_at DESC LIMIT 1""",
            (spk["id"],))[0]
        assert rev[0] == (snap[0] or spk["label"]) and rev[1] == new_name
        assert fetch(owner_db, "SELECT count(*) FROM chunks WHERE media_id = %s AND metadata->>'speaker' = %s",
                     (mid, new_name))[0][0] >= 1
        with tx(org_ids["alfa"], actor) as conn:
            speakers = case_tools.execute(conn, ids["pago"], "list_speakers", {}, ctx=ctx)
        assert any(s.get("speaker_id") == spk["id"] and s.get("display_name") == new_name for s in speakers)
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE speakers SET display_name=%s, version=%s WHERE id=%s", (snap[0], snap[1], spk["id"]))
            if chunks_before == 0:
                cur.execute("DELETE FROM chunks WHERE media_id = %s", (mid,))
        owner_db.commit()


def test_it_ct_12_list_people_by_role_returns_citable_document_pages(client, auth, ids, org_ids, owner_db):
    """`list_people_by_role` devuelve evidencia CITABLE (document_page) con nombre y conteo,
    para que la respuesta final pueda agregar '¿cuántos jueces?' sin abstenerse."""
    h = auth("abogada.alfa")
    doc_id = client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()[0]["id"]
    n = client.get(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages", headers=h).json()["pages"][0]["page_number"]
    snap = fetch(owner_db, "SELECT text FROM document_pages WHERE document_id=%s AND page_number=%s", (doc_id, n))[0][0]
    roles_snap = fetch(owner_db, "SELECT id, speaker_role FROM speakers WHERE case_id = %s", (ids["pago"],))
    with owner_db.cursor() as cur:
        cur.execute("UPDATE document_pages SET text=%s WHERE document_id=%s AND page_number=%s",
                    ("Auto. Firmado por ALBA LUCY COCK ÁLVAREZ\nJUEZ CIRCUITO", doc_id, n))
        cur.execute("UPDATE speakers SET speaker_role = NULL WHERE case_id = %s", (ids["pago"],))
    owner_db.commit()
    try:
        ctx = case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=None)
        with tx(org_ids["alfa"], None) as conn:
            items = case_tools.execute(conn, ids["pago"], "list_people_by_role", {"role": "juez"}, ctx=ctx)
        assert items
        assert any(it["source_type"] == "document_page" and "ALBA LUCY COCK" in (it.get("person_name") or "")
                   and it.get("document_id") and it.get("page_number") for it in items)
        assert all(it.get("mentions", 0) >= 1 for it in items)
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE document_pages SET text=%s WHERE document_id=%s AND page_number=%s", (snap, doc_id, n))
            for sid, role in roles_snap:
                cur.execute("UPDATE speakers SET speaker_role = %s WHERE id = %s", (role, sid))
        owner_db.commit()


def test_it_ct_13_get_document_markdown_tool(client, auth, ids, org_ids):
    """El agente puede leer un documento completo ya estructurado en Markdown."""
    fname = next(iter(ids["docs"]))
    ctx = case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=None)
    with tx(org_ids["alfa"], None) as conn:
        items = case_tools.execute(conn, ids["pago"], "get_document_markdown", {"document_id": fname}, ctx=ctx)
    assert items and items[0]["source_type"] == "document_markdown"
    assert items[0]["text"].startswith("#") and "## Página 1" in items[0]["text"]


def test_it_ct_14_list_people_by_role_prefers_confirmed_roles(client, auth, ids, org_ids, owner_db):
    """Si el usuario confirma el rol de un hablante, el conteo por rol usa ESA lista (exacta), no la heurística."""
    h = auth("abogada.alfa")
    actor = client.get("/v1/auth/me", headers=h).json()["id"]
    r = client.post(f"/v1/cases/{ids['pago']}/speakers", headers=h,
                    json={"display_name": "JUEZ CONFIRMADO PRUEBA", "speaker_role": "juez"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    try:
        with tx(org_ids["alfa"], actor) as conn:
            items = case_tools.execute(conn, ids["pago"], "list_people_by_role", {"role": "juez"},
                                       ctx=case_tools.ToolContext(org_id=org_ids["alfa"], actor_id=actor))
        assert items and all(it.get("confirmed") for it in items)
        assert any(it.get("speaker_id") == sid and it.get("speaker_role") == "juez" for it in items)
    finally:
        with owner_db.cursor() as cur:
            cur.execute("DELETE FROM speakers WHERE id = %s", (sid,))
        owner_db.commit()
