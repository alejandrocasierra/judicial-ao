"""MUST NOT: lo que la plataforma NO DEBE hacer (SSD §1, §13, §19, §20, §133). Cada prueba cita su requisito."""
from __future__ import annotations

import json

import pytest

from app.providers.llm import FakeLLM
from helpers import assert_error, create_case, db_raises, fetch


def test_not_01_no_judicial_determination_without_decision(client, auth, ids, owner_db):
    """§13: nunca 'determinado judicialmente' sin decisión citada — ni por API ni por SQL."""
    fact = client.get(f"/v1/cases/{ids['pago']}/facts", headers=auth("revisor.alfa")).json()[1]
    r = client.post(f"/v1/review/{fact['id']}", headers=auth("revisor.alfa"), json={
        "entity_type": "fact", "action": "EDIT", "expected_version": fact["version"],
        "changes": {"status": "JUDICIALLY_DETERMINED"}, "reason": "Intento sin decisión"})
    assert_error(r, 422, "JUDICIAL_DETERMINATION_REQUIRES_DECISION")
    msg = db_raises(owner_db, "UPDATE facts SET status = 'JUDICIALLY_DETERMINED' WHERE id = %s", (fact["id"],))
    assert "check" in msg.lower() or "JUDICIAL" in msg


def test_not_02_no_speaker_confirmation_without_party(client, auth, ids):
    """§134 riesgo 3: no se confirma la identidad de un hablante sin parte asociada."""
    spk = ids["speakers"]["SPK-03"]
    r = client.post(f"/v1/review/{spk['id']}", headers=auth("revisor.alfa"), json={
        "entity_type": "speaker", "action": "EDIT", "expected_version": spk["version"],
        "changes": {"resolution_status": "CONFIRMED"}, "reason": "Confirmar sin parte"})
    assert_error(r, 422, "SPEAKER_CONFIRMATION_REQUIRES_PARTY")


def test_not_03_platform_never_decides_contradictions(owner_db, ids):
    """§20: toda contradicción exige revisión humana; la base de datos no permite desactivarlo."""
    cid = ids["contradictions"][0]["id"]
    assert "check" in db_raises(owner_db, "UPDATE contradictions SET human_review_required = false WHERE id = %s", (cid,)).lower()


def test_not_04_no_model_call_without_evidence(client, auth, ids):
    """§20: sin evidencia no se llama al modelo ni se inventa una respuesta."""
    def forbidden(system, user):
        raise AssertionError("el modelo NO debe invocarse sin evidencia")
    FakeLLM.script = forbidden
    r = client.post(f"/v1/cases/{ids['vacio']}/query", headers=auth("abogada.alfa"), json={"question": "¿Quién pagó?", "mode": "fact_lookup"})
    assert r.status_code == 200 and r.json()["claims"] == []


def test_not_05_no_invented_citations_or_numbers(client, auth, ids):
    """§133: citation inexistente / dato no respaldado => la respuesta no lo presenta."""
    FakeLLM.script = lambda s, u: json.dumps({"claims": [
        {"text": "El demandado pagó $95.000.000 el 1 de enero de 2020.", "citations": ["E1"]},
        {"text": "Existe una sentencia firme.", "citations": ["E42"]}], "uncertainties": []})
    body = client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"),
                       json={"question": "pago de $80.000.000", "mode": "fact_lookup"}).json()
    assert body["evidence_count"] > 0, "la prueba requiere evidencia para que el modelo sí sea invocado"
    assert body["claims"] == [] and len(body["unsupported_claims"]) == 2 and "95.000.000" not in body["answer"]


def test_not_06_no_claim_attributed_to_wrong_speaker(owner_db, ids):
    """§133: claim atribuido a persona incorrecta. Un claim citado en audiencia debe coincidir con el hablante resuelto."""
    rows = fetch(owner_db, """SELECT cl.id FROM claims cl JOIN citations ci ON ci.target_type = 'claim' AND ci.target_id = cl.id
        JOIN transcript_segments ts ON ts.id = ci.segment_id JOIN speakers sp ON sp.id = ts.speaker_id
        WHERE cl.case_id = %s AND sp.resolved_party_id IS NOT NULL AND cl.claimant_party_id IS DISTINCT FROM sp.resolved_party_id""",
                 (ids["pago"],))
    assert rows == []


def test_not_07_no_silent_overwrite(client, auth, ids):
    """§1672: una modificación no sobrescribe en silencio (bloqueo optimista)."""
    claim = client.get(f"/v1/cases/{ids['pago']}/claims", headers=auth("revisor.alfa")).json()[-1]
    stale = claim["version"] - 1 if claim["version"] > 1 else claim["version"] + 5
    r = client.post(f"/v1/review/{claim['id']}", headers=auth("revisor.alfa"),
                    json={"entity_type": "claim", "action": "ACCEPT", "expected_version": stale, "reason": "Versión vieja"})
    assert_error(r, 409, "VERSION_CONFLICT")


def test_not_08_no_duplicate_evidence(client, auth, pdf_bytes):
    """§128: el mismo archivo (contenido + nombre) no genera duplicados inconsistentes.
    Desde 0020 la deduplicación es (case_id, sha256, filename): el MISMO PDF archivado
    bajo OTRO nombre es legítimo en un expediente judicial y se admite (201)."""
    h = auth("abogada.alfa")
    case = create_case(client, h)
    data = pdf_bytes()
    first = client.post(f"/v1/cases/{case['id']}/documents", headers=h, files={"file": ("a.pdf", data, "application/pdf")})
    renamed = client.post(f"/v1/cases/{case['id']}/documents", headers=h, files={"file": ("b.pdf", data, "application/pdf")})
    again = client.post(f"/v1/cases/{case['id']}/documents", headers=h, files={"file": ("a.pdf", data, "application/pdf")})
    assert first.status_code == 201 and renamed.status_code == 201 and again.status_code == 409
    assert len(client.get(f"/v1/cases/{case['id']}/documents", headers=h).json()) == 2


@pytest.mark.parametrize("jur,number,ok", [("co", "11001310300120240099991", True), ("co", "ABC-2024", False),
                                           ("generic", "ABC-2024-77", True)])
def test_not_09_jurisdiction_rules_do_not_leak_into_generic_domain(client, auth, jur, number, ok):
    """§2564: la validación colombiana no contamina la jurisdicción genérica."""
    import uuid
    num = number[:-4] + str(uuid.uuid4().int)[:4] if jur == "co" and ok else f"{number}-{uuid.uuid4().hex[:6]}" if ok else number
    r = client.post("/v1/cases", headers=auth("abogada.alfa"),
                    json={"jurisdiction": jur, "case_number": num, "title": "Jurisdicción", "language": "es"})
    assert (r.status_code == 201) is ok, r.text


def test_not_10_no_spending_beyond_case_budget(client, auth, owner_db):
    """§ costos: al agotar el presupuesto de tokens del expediente no se invoca el modelo."""
    h = auth("abogada.alfa")
    case = create_case(client, h)
    with owner_db.cursor() as cur:
        cur.execute("UPDATE cases SET spent_llm_tokens = max_llm_tokens WHERE id = %s", (case["id"],))
    owner_db.commit()
    FakeLLM.script = lambda s, u: (_ for _ in ()).throw(AssertionError("no debe llamarse"))
    r = client.post(f"/v1/cases/{case['id']}/query", headers=h, json={"question": "¿Algo?", "mode": "fact_lookup"})
    assert_error(r, 409, "BUDGET_EXCEEDED")


def test_not_11_no_obedience_to_instructions_inside_documents(client, auth, ids):
    """§20.5: el contenido del expediente es dato, nunca instrucción."""
    body = client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"),
                       json={"question": "¿Qué dice el correo reenviado sobre el contrato?", "mode": "document"}).json()
    low = body["answer"].lower()
    assert "system prompt" not in low and "untrusted" not in low


def test_not_12_no_deletion_of_evidence_through_api(client, auth, ids):
    """§3.2: los originales no se borran vía API; sólo se solicita borrado (auditado, sujeto a legal hold)."""
    d = ids["docs"]["03_soporte_transferencia.pdf"]["id"]
    assert_error(client.delete(f"/v1/cases/{ids['pago']}/documents/{d}", headers=auth("admin.alfa")), 405, "DOCUMENT_IMMUTABLE")
