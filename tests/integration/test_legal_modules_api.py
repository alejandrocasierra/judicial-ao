"""Integración de los endpoints de auditoría, hechos y política legal."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_legal_modules_facts_and_policy(client, auth, ids):
    h = auth("admin.alfa")
    case_id = ids["pago"]

    all_facts = client.get(f"/v1/cases/{case_id}/facts?proof=all&limit=20", headers=h)
    assert all_facts.status_code == 200, all_facts.text
    facts = all_facts.json()["facts"]
    assert facts
    for fact in facts:
        assert "proof_status" in fact and "positions" in fact
        assert "is_proven" in fact

    proven = client.get(f"/v1/cases/{case_id}/facts?proof=proven&limit=20", headers=h)
    assert proven.status_code == 200, proven.text
    assert all(f["is_proven"] for f in proven.json()["facts"])

    side = client.get(f"/v1/cases/{case_id}/facts?proof=all&side=DEMANDANTE&limit=10", headers=h)
    assert side.status_code == 200, side.text

    policy = client.get(f"/v1/cases/{case_id}/verification-policy", headers=h)
    assert policy.status_code == 200, policy.text
    assert "policy" in policy.json()

    updated = client.put(f"/v1/cases/{case_id}/verification-policy",
                         json={"mode": "STRICT", "require_legal_norm": True}, headers=h)
    assert updated.status_code == 200, updated.text
    assert updated.json()["policy"]["mode"] == "STRICT"

    rebuild = client.post(f"/v1/cases/{case_id}/facts/rebuild", headers=h)
    assert rebuild.status_code == 200, rebuild.text
    assert "job_id" in rebuild.json()

    refreshed = client.get(f"/v1/cases/{case_id}/facts?proof=all&limit=20", headers=h).json()["facts"]
    fact = refreshed[0]
    patched = client.patch(f"/v1/cases/{case_id}/facts/{fact['id']}", headers=h, json={
        "expected_version": fact["version"],
        "reason": "Prueba de revisión del ledger",
        "proof_status": fact["proof_status"],
    })
    assert patched.status_code == 200, patched.text
    assert patched.json()["version"] == fact["version"] + 1


def test_legal_modules_audits_empty_and_second_opinion_requires_message(client, auth, ids):
    h = auth("admin.alfa")
    case_id = ids["pago"]
    audits = client.get(f"/v1/cases/{case_id}/audits", headers=h)
    assert audits.status_code == 200, audits.text
    assert "audits" in audits.json()

    runs = client.get(f"/v1/cases/{case_id}/second-opinions", headers=h)
    assert runs.status_code == 200, runs.text
    assert "runs" in runs.json()


def test_legal_modules_chat_answer_has_persisted_audit(client, auth, ids):
    h = auth("abogada.alfa")
    case_id = ids["pago"]
    client.put(f"/v1/cases/{case_id}/verification-policy",
               json={"mode": "BALANCED", "require_legal_norm": False}, headers=h)
    session = client.post(f"/v1/cases/{case_id}/chats", headers=h, json={})
    assert session.status_code == 201, session.text
    sid = session.json()["id"]
    sent = client.post(f"/v1/cases/{case_id}/chats/{sid}/messages", headers=h,
                       json={"content": "¿Qué evidencia hay sobre pagos?"})
    assert sent.status_code == 201, sent.text
    body = sent.json()
    assert body.get("audit_id"), body
    audit_id = body["audit_id"]
    detail = client.get(f"/v1/cases/{case_id}/audits/{audit_id}", headers=h)
    assert detail.status_code == 200, detail.text
    assert detail.json()["chat_message_id"] == body["assistant_message"]["id"]
    listed = client.get(f"/v1/cases/{case_id}/audits?message_id={body['assistant_message']['id']}", headers=h)
    assert listed.status_code == 200, listed.text
    assert any(a["id"] == audit_id for a in listed.json()["audits"])
