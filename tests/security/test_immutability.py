"""SEC-IMM: integridad de la evidencia, legal hold y registros append-only (SSD §3.2, §20.4, amenazas T7/T8).
Se prueba a nivel de BASE DE DATOS (incluso con el rol propietario) — no depende de la API."""
from __future__ import annotations

from helpers import assert_error, db_raises, fetch


def _doc(owner_db, case_id):
    return fetch(owner_db, "SELECT id FROM documents WHERE case_id = %s ORDER BY created_at LIMIT 1", (case_id,))[0][0]


def test_sec_imm_01_document_hash_and_uri_cannot_change(owner_db, ids):
    d = _doc(owner_db, ids["pago"])
    for col, val in (("sha256", "0" * 64), ("storage_uri", "s3://otro/objeto"), ("filename", "renombrado.pdf"), ("size_bytes", 1)):
        assert "DOCUMENT_IMMUTABLE" in db_raises(owner_db, f"UPDATE documents SET {col} = %s WHERE id = %s", (val, d))


def test_sec_imm_02_documents_cannot_be_deleted(owner_db, app_db, ids, org_ids):
    d = _doc(owner_db, ids["pago"])
    assert "DOCUMENT_IMMUTABLE" in db_raises(owner_db, "DELETE FROM documents WHERE id = %s", (d,))
    msg = db_raises(app_db, "DELETE FROM documents WHERE id = %s", (d,), org=org_ids["alfa"])
    assert "permission denied" in msg or "DOCUMENT_IMMUTABLE" in msg


def test_sec_imm_03_processing_status_can_still_change(owner_db, ids):
    """Control positivo: la inmutabilidad es del ORIGINAL, no de los metadatos de procesamiento."""
    d = _doc(owner_db, ids["pago"])
    with owner_db.cursor() as cur:
        cur.execute("UPDATE documents SET processing_status = processing_status WHERE id = %s", (d,))
    owner_db.rollback()


def test_sec_imm_04_cases_cannot_be_deleted(owner_db, ids):
    assert "CASE_DELETE_BLOCKED" in db_raises(owner_db, "DELETE FROM cases WHERE id = %s", (ids["vacio"],))


def test_sec_imm_05_audit_log_is_append_only(owner_db):
    assert "AUDIT_APPEND_ONLY" in db_raises(owner_db, "UPDATE audit_logs SET action = 'x' WHERE id = (SELECT min(id) FROM audit_logs)")
    assert "AUDIT_APPEND_ONLY" in db_raises(owner_db, "DELETE FROM audit_logs")
    assert "AUDIT_APPEND_ONLY" in db_raises(owner_db, "TRUNCATE audit_logs")


def test_sec_imm_06_reviews_are_append_only(owner_db):
    assert "AUDIT_APPEND_ONLY" in db_raises(owner_db, "UPDATE reviews SET reason = 'x'")
    assert "AUDIT_APPEND_ONLY" in db_raises(owner_db, "DELETE FROM reviews")


def test_sec_imm_07_api_delete_document_is_405(client, auth, ids):
    d = ids["docs"]["01_demanda.pdf"]["id"]
    assert_error(client.delete(f"/v1/cases/{ids['pago']}/documents/{d}", headers=auth("admin.alfa")), 405, "DOCUMENT_IMMUTABLE")


def test_sec_imm_08_legal_hold_blocks_deletion_even_with_purge_flag(client, auth, ids, owner_db):
    h = auth("admin.alfa")
    d = ids["docs"]["02_contestacion.pdf"]["id"]
    assert client.post(f"/v1/cases/{ids['pago']}/legal-hold", headers=h, json={"enabled": True, "reason": "Litigio activo"}).status_code == 200
    try:
        r = client.post(f"/v1/cases/{ids['pago']}/documents/{d}/deletion-request", headers=h,
                        json={"reason": "Solicitud de borrado durante hold"})
        assert_error(r, 409, "LEGAL_HOLD_ACTIVE")
        with owner_db.cursor() as cur:
            cur.execute("SET LOCAL app.allow_evidence_purge = 'on'")
        msg = db_raises(owner_db, "DELETE FROM documents WHERE id = %s", (d,))
        assert "LEGAL_HOLD_ACTIVE" in msg or "DOCUMENT_IMMUTABLE" in msg
        assert "LEGAL_HOLD_ACTIVE" in db_raises(owner_db, "DELETE FROM cases WHERE id = %s", (ids["pago"],))
    finally:
        client.post(f"/v1/cases/{ids['pago']}/legal-hold", headers=h, json={"enabled": False, "reason": "Fin de la prueba"})


def test_sec_imm_09_legal_hold_changes_are_audited(client, auth, ids, owner_db):
    before = fetch(owner_db, "SELECT count(*) FROM audit_logs WHERE action = 'case.legal_hold_changed'")[0][0]
    h = auth("admin.alfa")
    client.post(f"/v1/cases/{ids['vacio']}/legal-hold", headers=h, json={"enabled": True, "reason": "Auditoría hold"})
    client.post(f"/v1/cases/{ids['vacio']}/legal-hold", headers=h, json={"enabled": False, "reason": "Auditoría hold"})
    assert fetch(owner_db, "SELECT count(*) FROM audit_logs WHERE action = 'case.legal_hold_changed'")[0][0] == before + 2
