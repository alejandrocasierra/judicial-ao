"""SEC-AUD: la auditoría es completa, encadenada por hash y verificable (SSD §20.4, §96, amenaza T8)."""
from __future__ import annotations

import uuid

from helpers import fetch


def test_sec_aud_01_hash_chain_is_linked(client, auth, ids, owner_db):
    # Genera suficientes acciones auditables de forma determinista (no depende de tests previos).
    for u in ("lector.alfa", "admin.beta", "abogada.alfa"):
        for mode in ("fact_lookup", "evidence_lookup"):
            client.post(f"/v1/cases/{ids['pago']}/query", headers=auth(u),
                        json={"question": "pago contrato", "mode": mode})
    rows = fetch(owner_db, "SELECT id, prev_hash, hash FROM audit_logs ORDER BY id")
    assert len(rows) > 5
    assert rows[0][1] is None
    for prev, cur in zip(rows, rows[1:]):
        assert cur[1] == prev[2], f"cadena rota en id={cur[0]}"
    assert len({r[2] for r in rows}) == len(rows)


def test_sec_aud_02_hash_is_recomputable(owner_db):
    bad = fetch(owner_db, """SELECT id FROM audit_logs a WHERE a.hash <> encode(digest(coalesce(a.prev_hash,'') || a.id::text ||
        coalesce(a.organization_id::text,'') || coalesce(a.actor_id::text,'') || a.action || coalesce(a.entity_id,'') ||
        coalesce(a.before::text,'') || coalesce(a.after::text,'') || a.created_at::text, 'sha256'), 'hex')""")
    assert bad == [], f"registros con hash que no coincide: {bad[:5]}"


def test_sec_aud_03_sensitive_actions_are_audited_with_request_id(client, auth, ids, owner_db, pdf_bytes):
    rid = f"aud-{uuid.uuid4().hex[:16]}"
    h = {**auth("abogada.alfa"), "X-Request-ID": rid}
    client.post(f"/v1/cases/{ids['pago']}/query", headers=h, json={"question": "pago", "mode": "fact_lookup"})
    client.post(f"/v1/cases/{ids['pago']}/documents", headers=h, files={"file": ("aud.pdf", pdf_bytes(), "application/pdf")})
    actions = {a for (a,) in fetch(owner_db, "SELECT action FROM audit_logs WHERE request_id = %s", (rid,))}
    assert {"ai.query", "document.uploaded"} <= actions, actions


def test_sec_aud_04_audit_rows_carry_actor_and_org(owner_db):
    orphan = fetch(owner_db, "SELECT count(*) FROM audit_logs WHERE action NOT LIKE 'auth.%%' AND (actor_id IS NULL OR organization_id IS NULL)")
    assert orphan == [(0,)]
