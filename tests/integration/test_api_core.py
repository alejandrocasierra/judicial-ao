"""IT — flujos funcionales principales contra API + PostgreSQL real."""
import hashlib
import uuid

import pytest

pytestmark = pytest.mark.integration


def test_it_health_01_liveness_and_readiness(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_it_auth_01_login_and_me(client, auth):
    r = client.get("/v1/auth/me", headers=auth("abogada.alfa"))
    assert r.status_code == 200
    body = r.json()
    assert body["org_role"] == "LAWYER" and "ai.query" in body["permissions"] and "audit.read" not in body["permissions"]


def test_it_auth_02_refresh_rotation_and_reuse_detection(client, email, password):
    r = client.post("/v1/auth/login", json={"email": email("revisor.alfa"), "password": password}).json()
    r2 = client.post("/v1/auth/refresh", json={"refresh_token": r["refresh_token"]})
    assert r2.status_code == 200 and r2.json()["refresh_token"] != r["refresh_token"]
    reuse = client.post("/v1/auth/refresh", json={"refresh_token": r["refresh_token"]})
    assert reuse.status_code == 401
    # tras detectar reutilización, toda la familia queda revocada
    assert client.post("/v1/auth/refresh", json={"refresh_token": r2.json()["refresh_token"]}).status_code == 401


def _new_case_number():
    return "".join(str(uuid.uuid4().int)[:23]).ljust(23, "0")


def test_it_case_01_create_valid_case(client, auth):
    num = _new_case_number()
    r = client.post("/v1/cases", headers=auth("abogada.alfa"),
                    json={"jurisdiction": "co", "case_number": num, "title": "Caso de prueba integral", "language": "es"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "CREATED" and r.json()["version"] == 1
    dup = client.post("/v1/cases", headers=auth("abogada.alfa"),
                      json={"jurisdiction": "co", "case_number": num, "title": "Duplicado", "language": "es"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "CASE_DUPLICATE"


@pytest.mark.parametrize("payload,code", [
    ({"jurisdiction": "co", "case_number": "123", "title": "Radicado corto", "language": "es"}, "CASE_NUMBER_INVALID"),
    ({"jurisdiction": "zz", "case_number": "ABC-123", "title": "Jurisdicción rara", "language": "es"}, "JURISDICTION_UNKNOWN"),
    ({"jurisdiction": "co", "case_number": "11001310300120240012399", "title": "x", "language": "es"}, "VALIDATION_ERROR"),
    ({"jurisdiction": "co", "case_number": "11001310300120240012399", "title": "Idioma", "language": "fr"}, "VALIDATION_ERROR"),
])
def test_it_case_02_validation_errors(client, auth, payload, code):
    r = client.post("/v1/cases", headers=auth("abogada.alfa"), json=payload)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_it_case_03_list_shows_only_member_cases(client, auth, ids):
    assert client.get("/v1/cases", headers=auth("externo.alfa")).json() == []
    lector = {c["id"] for c in client.get("/v1/cases", headers=auth("lector.alfa")).json()}
    assert lector == {ids["pago"]}


def test_it_case_04_optimistic_locking_and_transitions(client, auth):
    num = _new_case_number()
    case = client.post("/v1/cases", headers=auth("abogada.alfa"),
                       json={"jurisdiction": "co", "case_number": num, "title": "Bloqueo optimista", "language": "es"}).json()
    ok = client.patch(f"/v1/cases/{case['id']}", headers=auth("abogada.alfa"), json={"expected_version": 1, "title": "Nuevo título"})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = client.patch(f"/v1/cases/{case['id']}", headers=auth("abogada.alfa"), json={"expected_version": 1, "title": "Viejo"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    bad = client.patch(f"/v1/cases/{case['id']}", headers=auth("abogada.alfa"), json={"expected_version": 2, "status": "READY"})
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_it_doc_01_upload_dedup_download_integrity(client, auth, ids, pdf_bytes):
    data = pdf_bytes()
    h = auth("abogada.alfa")
    r = client.post(f"/v1/cases/{ids['pago']}/documents", headers=h, files={"file": ("prueba.pdf", data, "application/pdf")})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["sha256"] == hashlib.sha256(data).hexdigest() and doc["size_bytes"] == len(data)
    # Desde 0020 la deduplicación es (case_id, sha256, filename): mismo contenido con
    # OTRO nombre es legítimo (201); con el MISMO nombre es duplicado (409).
    renamed = client.post(f"/v1/cases/{ids['pago']}/documents", headers=h, files={"file": ("otro_nombre.pdf", data, "application/pdf")})
    assert renamed.status_code == 201
    dup = client.post(f"/v1/cases/{ids['pago']}/documents", headers=h, files={"file": ("prueba.pdf", data, "application/pdf")})
    assert dup.status_code == 409 and dup.json()["error"]["details"]["existing_id"] == doc["id"]
    dl = client.get(f"/v1/cases/{ids['pago']}/documents/{doc['id']}/download", headers=h)
    assert dl.status_code == 200 and dl.content == data and dl.headers["x-content-sha256"] == doc["sha256"]


def test_it_doc_02_page_viewer(client, auth, ids):
    d = ids["docs"]["01_demanda.pdf"]
    r = client.get(f"/v1/cases/{ids['pago']}/documents/{d['id']}/pages/1", headers=auth("lector.alfa"))
    assert r.status_code == 200 and "DEMANDA" in r.json()["text"]
    assert client.get(f"/v1/cases/{ids['pago']}/documents/{d['id']}/pages/99", headers=auth("lector.alfa")).status_code == 404


def test_it_media_01_upload_audio(client, auth):
    case = client.post("/v1/cases", headers=auth("abogada.alfa"), json={
        "jurisdiction": "co", "case_number": _new_case_number(), "title": "Caso con audio", "language": "es"}).json()
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + uuid.uuid4().bytes
    r = client.post(f"/v1/cases/{case['id']}/media", headers=auth("abogada.alfa"),
                    files={"file": ("audiencia.wav", wav, "audio/wav")}, data={"title": "Audiencia"})
    assert r.status_code == 201 and r.json()["media_type"] == "audio"


def test_it_views_01_timeline_claims_facts_evidence(client, auth, ids):
    h = auth("lector.alfa")
    tl = client.get(f"/v1/cases/{ids['pago']}/timeline", headers=h).json()
    dates = [e["event_date"] for e in tl]
    assert dates == sorted(dates) and all(e["sources"] for e in tl)
    ev = client.get(f"/v1/cases/{ids['pago']}/evidence", headers=h).json()
    assert {"supports", "neutral"} <= {e["stance"] for e in ev}
    assert client.get(f"/v1/cases/{ids['pago']}/contradictions", headers=h).json()[0]["human_review_required"] is True
    case = client.get(f"/v1/cases/{ids['pago']}", headers=h).json()
    assert case["stats"]["items_requiring_review"] >= 2  # OCR bajo + ASR bajo


def test_it_cit_01_all_seeded_citations_are_valid(client, auth, ids):
    h = auth("lector.alfa")
    cit_ids = [c for cl in ids["claims"] for c in cl["citation_ids"]]
    assert cit_ids
    for cid in cit_ids:
        r = client.get(f"/v1/citations/{cid}", headers=h)
        assert r.status_code == 200 and r.json()["valid"] is True, r.json()


def test_it_cit_02_tampered_quote_hash_is_detected(client, auth, ids, owner_db):
    cid = ids["claims"][0]["citation_ids"][0]
    with owner_db.cursor() as cur:
        cur.execute("UPDATE citations SET quote_hash = repeat('0', 64) WHERE id = %s", (cid,))
    owner_db.commit()
    try:
        r = client.get(f"/v1/citations/{cid}", headers=auth("lector.alfa")).json()
        assert r["valid"] is False and "quote_hash_mismatch" in r["invalid_reasons"]
    finally:
        from app.services.citations import quote_hash
        with owner_db.cursor() as cur:
            cur.execute("SELECT p.text, c.char_start, c.char_end FROM citations c JOIN document_pages p ON p.document_id = c.document_id "
                        "AND p.page_number = c.page_number WHERE c.id = %s", (cid,))
            text, s, e = cur.fetchone()
            cur.execute("UPDATE citations SET quote_hash = %s WHERE id = %s", (quote_hash(text[s:e]), cid))
        owner_db.commit()


def test_it_query_01_grounded_answer_with_resolvable_citations(client, auth, ids):
    r = client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"),
                    json={"question": "¿Qué pruebas hay sobre el pago de $80.000.000?", "mode": "evidence_lookup"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["claims"] and all(c["citations"] for c in body["claims"])
    for c in body["citations"]:
        res = client.get(f"/v1/citations/{c['citation_id']}", headers=auth("abogada.alfa")).json()
        assert res["valid"] is True


def test_it_query_02_localized_insufficient_evidence(client, auth, ids):
    es = client.post(f"/v1/cases/{ids['vacio']}/query", headers=auth("abogada.alfa", "es"),
                     json={"question": "¿Quién firmó el contrato?", "mode": "fact_lookup"}).json()
    en = client.post(f"/v1/cases/{ids['vacio']}/query", headers=auth("abogada.alfa", "en"),
                     json={"question": "Who signed the contract?", "mode": "fact_lookup"}).json()
    assert es["evidence_count"] == 0 and es["claims"] == [] and es["answer"] != en["answer"]


def test_it_query_03_agent_strategy_returns_response(client, auth, ids):
    r = client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"),
                    json={"question": "¿Qué pruebas hay sobre el pago?", "mode": "evidence_lookup", "strategy": "agent"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "claims" in body and "citations" in body and "evidence_count" in body


def test_it_review_01_edit_keeps_original_ai_output(client, auth, ids, owner_db):
    claim = next(c for c in client.get(f"/v1/cases/{ids['pago']}/claims", headers=auth("revisor.alfa")).json()
                 if c["claim_type"] == "witness_statement")
    r = client.post(f"/v1/review/{claim['id']}", headers=auth("revisor.alfa"), json={
        "entity_type": "claim", "action": "EDIT", "expected_version": claim["version"],
        "changes": {"text": claim["text"] + " (corregido)"}, "reason": "Ajuste de redacción"})
    assert r.status_code == 200, r.text
    with owner_db.cursor() as cur:
        cur.execute("SELECT original_output->>'text', human_output->>'text', reviewer_id FROM reviews WHERE entity_id = %s", (claim["id"],))
        orig, human, reviewer = cur.fetchone()
    assert "(corregido)" not in orig and human.endswith("(corregido)") and reviewer
    conflict = client.post(f"/v1/review/{claim['id']}", headers=auth("revisor.alfa"), json={
        "entity_type": "claim", "action": "ACCEPT", "expected_version": claim["version"], "reason": "Versión vieja"})
    assert conflict.status_code == 409


def test_it_i18n_01_error_messages_follow_accept_language(client, auth):
    fake = uuid.uuid4()
    es = client.get(f"/v1/cases/{fake}", headers=auth("abogada.alfa", "es-CO")).json()["error"]
    en = client.get(f"/v1/cases/{fake}", headers=auth("abogada.alfa", "en-US")).json()["error"]
    assert es["code"] == en["code"] == "CASE_NOT_FOUND" and es["message"] != en["message"] and es["request_id"]
    enums_en = client.get("/v1/meta/enums", headers={"Accept-Language": "en"}).json()
    assert enums_en["enums"]["fact_status"]["DISPUTED"] == "Disputed"


def test_it_proc_01_process_is_idempotent(client, auth, ids):
    body = {"job_types": ["document_ocr", "legal_extraction"]}
    a = client.post(f"/v1/cases/{ids['pago']}/process", headers=auth("gestor.alfa"), json=body).json()["jobs"]
    b = client.post(f"/v1/cases/{ids['pago']}/process", headers=auth("gestor.alfa"), json=body).json()["jobs"]
    assert [j["id"] for j in a] == [j["id"] for j in b] and all(j["reused"] for j in b)
    st = client.get(f"/v1/cases/{ids['pago']}/processing", headers=auth("gestor.alfa")).json()
    assert len([j for j in st["jobs"] if j["job_type"] in body["job_types"]]) == 2
