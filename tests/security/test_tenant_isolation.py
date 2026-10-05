"""SEC-TEN: aislamiento entre organizaciones (SSD §19, §20.3, amenaza T1).
Se verifica en DOS capas: la API (404 sin revelar existencia) y la base de datos (RLS forzado),
de modo que un bug en la API no basta para filtrar datos de otro tenant."""
from __future__ import annotations

import uuid

import pytest

from helpers import assert_error, db_raises, fetch

CASE_SUBRESOURCES = ["", "/claims", "/facts", "/evidence", "/contradictions", "/timeline", "/entities",
                     "/parties", "/speakers", "/documents", "/processing"]


@pytest.mark.parametrize("suffix", CASE_SUBRESOURCES)
def test_sec_ten_01_other_tenant_case_is_404_everywhere(client, auth, ids, suffix):
    r = client.get(f"/v1/cases/{ids['lease']}{suffix}", headers=auth("admin.alfa"))
    assert_error(r, 404, "CASE_NOT_FOUND")


def test_sec_ten_02_other_tenant_404_is_indistinguishable_from_nonexistent(client, auth, ids):
    a = client.get(f"/v1/cases/{ids['lease']}", headers=auth("admin.alfa", "es")).json()["error"]
    b = client.get(f"/v1/cases/{uuid.uuid4()}", headers=auth("admin.alfa", "es")).json()["error"]
    assert (a["code"], a["message"]) == (b["code"], b["message"])


def test_sec_ten_03_write_operations_on_other_tenant_are_404(client, auth, ids, pdf_bytes):
    h = auth("admin.alfa")
    assert_error(client.post(f"/v1/cases/{ids['lease']}/query", headers=h,
                             json={"question": "What is the rent?", "mode": "fact_lookup"}), 404)
    assert_error(client.post(f"/v1/cases/{ids['lease']}/documents", headers=h,
                             files={"file": ("x.pdf", pdf_bytes(), "application/pdf")}), 404)
    assert_error(client.patch(f"/v1/cases/{ids['lease']}", headers=h, json={"expected_version": 1, "title": "Hijacked"}), 404)
    assert_error(client.post(f"/v1/cases/{ids['lease']}/legal-hold", headers=h,
                             json={"enabled": True, "reason": "cross tenant"}), 404)


def test_sec_ten_04_other_tenant_citation_and_entity_are_not_found(client, auth, org_ids, owner_db):
    beta_cit = fetch(owner_db, "SELECT id FROM citations WHERE organization_id = %s LIMIT 1", (org_ids["beta"],))
    beta_claim = fetch(owner_db, "SELECT id, version FROM claims WHERE organization_id = %s LIMIT 1", (org_ids["beta"],))
    assert beta_cit and beta_claim, "las semillas beta deben tener citas y claims"
    assert_error(client.get(f"/v1/citations/{beta_cit[0][0]}", headers=auth("admin.alfa")), 404, "CITATION_NOT_FOUND")
    r = client.post(f"/v1/review/{beta_claim[0][0]}", headers=auth("admin.alfa"), json={
        "entity_type": "claim", "action": "REJECT", "expected_version": beta_claim[0][1], "reason": "cross tenant"})
    assert_error(r, 404)


def test_sec_ten_05_cannot_add_user_from_other_tenant_as_member(client, auth, ids):
    beta_user = client.get("/v1/auth/me", headers=auth("lawyer.beta")).json()["id"]
    r = client.post(f"/v1/cases/{ids['pago']}/members", headers=auth("gestor.alfa"),
                    json={"user_id": beta_user, "case_role": "VIEWER"})
    assert_error(r, 404, "USER_NOT_FOUND")


def test_sec_ten_06_confidential_marker_never_reaches_other_tenant(client, auth, ids, markers):
    marker = markers["beta_confidential"]
    h = auth("admin.alfa")
    for path in ("/v1/cases", f"/v1/cases/{ids['pago']}/documents", "/v1/audit"):
        assert marker not in client.get(path, headers=h).text
    r = client.post(f"/v1/cases/{ids['pago']}/query", headers=h, json={"question": marker, "mode": "evidence_lookup"})
    assert r.status_code == 200 and marker not in r.text


def test_sec_ten_07_audit_api_is_scoped_to_own_org(client, auth):
    rid = f"trace-{uuid.uuid4().hex}"
    client.get("/v1/auth/me", headers={**auth("admin.alfa"), "X-Request-ID": rid})
    client.post(f"/v1/cases/{uuid.uuid4()}/query", headers={**auth("admin.alfa"), "X-Request-ID": rid},
                json={"question": "nada", "mode": "fact_lookup"})
    assert rid not in client.get("/v1/audit?limit=1000", headers=auth("admin.beta")).text


# ---------------------------- Capa de base de datos (RLS) ----------------------------

def test_sec_ten_10_rls_hides_everything_without_org_context(app_db):
    for table in ("cases", "documents", "document_pages", "claims", "citations", "transcript_segments", "users"):
        assert fetch(app_db, f"SELECT count(*) FROM {table}")[0][0] == 0, table


def test_sec_ten_11_rls_shows_only_current_org(app_db, org_ids, ids):
    rows = fetch(app_db, "SELECT DISTINCT organization_id::text FROM cases", org=org_ids["alfa"])
    assert rows == [(org_ids["alfa"],)]
    assert fetch(app_db, "SELECT id FROM cases WHERE id = %s", (ids["lease"],), org=org_ids["alfa"]) == []


def test_sec_ten_12_rls_with_check_blocks_insert_into_other_org(app_db, org_ids, ids):
    msg = db_raises(app_db, "INSERT INTO claims (organization_id, case_id, text, claim_type) VALUES (%s, %s, 'x', 'inference')",
                    (org_ids["beta"], ids["lease"]), org=org_ids["alfa"])
    assert "row-level security" in msg or "violates" in msg


def test_sec_ten_13_trigger_blocks_cross_tenant_foreign_keys(app_db, org_ids, ids):
    """Aunque organization_id sea el propio, apuntar a un expediente de otro tenant => TENANT_MISMATCH."""
    msg = db_raises(app_db, "INSERT INTO claims (organization_id, case_id, text, claim_type) VALUES (%s, %s, 'x', 'inference')",
                    (org_ids["alfa"], ids["lease"]), org=org_ids["alfa"])
    assert "TENANT_MISMATCH" in msg


def test_sec_ten_14_rls_cannot_be_bypassed_by_updating_org(app_db, org_ids, ids):
    """Reasignar un expediente propio a otro tenant debe fallar (WITH CHECK) — jamás se "regala" un caso."""
    msg = db_raises(app_db, "UPDATE cases SET organization_id = %s WHERE id = %s", (org_ids["beta"], ids["vacio"]),
                    org=org_ids["alfa"])
    assert "row-level security" in msg or "violates" in msg or "TENANT" in msg


def test_sec_ten_15_app_role_has_no_bypassrls_and_no_superuser(app_db):
    row = fetch(app_db, "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    assert row == [(False, False)]


def test_sec_ten_16_all_tenant_tables_force_rls(owner_db):
    missing = fetch(owner_db, """SELECT c.relname FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace AND a.attname = 'organization_id'
          AND NOT (c.relrowsecurity AND c.relforcerowsecurity)""")
    assert missing == [], f"Tablas con organization_id sin RLS forzado: {missing}"


# ---------------------------- Chat IA (chat_sessions / chat_messages) ----------------------------

def _exec_commit(conn, sql: str, params: tuple = (), org: str | None = None) -> list[tuple]:
    """Como fetch() pero CONFIRMA (para datos de setup que deben sobrevivir entre sentencias)."""
    with conn.cursor() as cur:
        if org is not None:
            cur.execute("SELECT set_config('app.current_org', %s, true)", (org,))
        cur.execute(sql, params)
        out = cur.fetchall() if cur.description else []
    conn.commit()
    return out


def _mk_beta_chat(app_db, org_ids, ids) -> str:
    """Crea una sesión + mensaje de la org beta y devuelve el session_id."""
    sid = _exec_commit(app_db, """INSERT INTO chat_sessions (organization_id, case_id, title)
        VALUES (%s, %s, 'sesión beta') RETURNING id::text""", (org_ids["beta"], ids["lease"]), org=org_ids["beta"])[0][0]
    _exec_commit(app_db, """INSERT INTO chat_messages (organization_id, session_id, role, content)
        VALUES (%s, %s, 'user', 'hola')""", (org_ids["beta"], sid), org=org_ids["beta"])
    return sid


def _drop_beta_chat(app_db, org_ids, sid: str) -> None:
    _exec_commit(app_db, "DELETE FROM chat_sessions WHERE id = %s", (sid,), org=org_ids["beta"])


def test_sec_ten_17_chat_tables_are_invisible_to_other_tenant(app_db, org_ids, ids):
    sid = _mk_beta_chat(app_db, org_ids, ids)
    try:
        for org in (None, org_ids["alfa"]):
            assert fetch(app_db, "SELECT count(*) FROM chat_sessions WHERE id = %s", (sid,), org=org)[0][0] == 0
            assert fetch(app_db, "SELECT count(*) FROM chat_messages WHERE session_id = %s", (sid,), org=org)[0][0] == 0
    finally:
        _drop_beta_chat(app_db, org_ids, sid)


def test_sec_ten_18_chat_with_check_blocks_insert_into_other_org(app_db, org_ids, ids):
    msg = db_raises(app_db, "INSERT INTO chat_sessions (organization_id, case_id) VALUES (%s, %s)",
                    (org_ids["beta"], ids["lease"]), org=org_ids["alfa"])
    assert "row-level security" in msg or "violates" in msg


def test_sec_ten_19_chat_triggers_block_cross_tenant_foreign_keys(app_db, org_ids, ids):
    """Sesión propia apuntando a expediente ajeno, y mensaje propio colgado de sesión ajena."""
    msg = db_raises(app_db, "INSERT INTO chat_sessions (organization_id, case_id) VALUES (%s, %s)",
                    (org_ids["alfa"], ids["lease"]), org=org_ids["alfa"])
    assert "TENANT_MISMATCH" in msg
    sid = _mk_beta_chat(app_db, org_ids, ids)
    try:
        msg = db_raises(app_db, "INSERT INTO chat_messages (organization_id, session_id, role) VALUES (%s, %s, 'user')",
                        (org_ids["alfa"], sid), org=org_ids["alfa"])
        assert "TENANT_MISMATCH" in msg
    finally:
        _drop_beta_chat(app_db, org_ids, sid)


def test_sec_ten_20_chat_message_constraints_enforced(app_db, org_ids, ids):
    """role válido y attachments/citations siempre arreglos jsonb (los '@' reales del chat)."""
    sid = _mk_beta_chat(app_db, org_ids, ids)
    try:
        msg = db_raises(app_db, "INSERT INTO chat_messages (organization_id, session_id, role) VALUES (%s, %s, 'robot')",
                        (org_ids["beta"], sid), org=org_ids["beta"])
        assert "violates check constraint" in msg or "chat_messages_role_check" in msg
        msg = db_raises(app_db, """INSERT INTO chat_messages (organization_id, session_id, role, attachments)
            VALUES (%s, %s, 'user', '{}'::jsonb)""", (org_ids["beta"], sid), org=org_ids["beta"])
        assert "violates check constraint" in msg
        ok = fetch(app_db, """INSERT INTO chat_messages (organization_id, session_id, role, attachments)
            VALUES (%s, %s, 'user', '[{"kind":"document","id":"%s","name":"x.pdf"}]'::jsonb) RETURNING id""",
            (org_ids["beta"], sid, uuid.uuid4()), org=org_ids["beta"])
        assert len(ok) == 1
    finally:
        _drop_beta_chat(app_db, org_ids, sid)


def test_sec_ten_21_chat_attachment_from_other_tenant_is_invisible(client, auth, ids, org_ids, owner_db):
    """Adjuntar con '@' un documento de otra org no filtra nada: sin tarjeta y sin contenido."""
    beta_doc = fetch(owner_db, "SELECT id::text, filename FROM documents WHERE organization_id = %s LIMIT 1",
                     (org_ids["beta"],))[0]
    r = client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("admin.alfa"), json={
        "question": "¿Qué dice el documento adjunto sobre el contrato?", "mode": "document",
        "attachments": [{"kind": "document", "id": beta_doc[0], "name": beta_doc[1]}]})
    assert r.status_code == 200
    assert r.json()["file_cards"] == [] and beta_doc[1] not in r.text


def test_sec_ten_22_chat_sessions_are_tenant_scoped(client, auth, ids, org_ids, owner_db):
    """Sesiones de chat: crear en expediente ajeno → 404; leer/enviar a sesión ajena → 404."""
    h_alfa = auth("abogada.alfa")
    h_beta = auth("lawyer.beta")
    sid = client.post(f"/v1/cases/{ids['pago']}/chats", headers=h_alfa, json={}).json()["id"]
    # beta no puede crear sesión en un expediente de alfa
    r = client.post(f"/v1/cases/{ids['pago']}/chats", headers=h_beta, json={})
    assert_error(r, 404, "CASE_NOT_FOUND")
    # ni ver mensajes, ni enviar, ni archivar la sesión de alfa (404: no se revela existencia)
    assert_error(client.get(f"/v1/cases/{ids['lease']}/chats/{sid}/messages", headers=h_beta), 404, "CHAT_NOT_FOUND")
    assert_error(client.post(f"/v1/cases/{ids['lease']}/chats/{sid}/messages", headers=h_beta,
                             json={"content": "hola desde beta"}), 404, "CHAT_NOT_FOUND")
    assert_error(client.delete(f"/v1/cases/{ids['lease']}/chats/{sid}", headers=h_beta), 404, "CHAT_NOT_FOUND")
    # y sus mensajes no aparecen para beta en la BD (RLS)
    assert fetch(owner_db, "SELECT count(*) FROM chat_messages WHERE session_id = %s", (sid,)) is not None
    assert fetch(owner_db, "SELECT count(*) FROM chat_sessions WHERE id = %s AND organization_id = %s",
                 (sid, org_ids["beta"]))[0][0] == 0
