"""MUST: lo que la plataforma DEBE hacer (SSD §3, §13, §20, §132-133). Cada prueba cita su requisito."""
from __future__ import annotations

import hashlib

from helpers import create_case, fetch


def _q(client, h, case, question, mode="evidence_lookup"):
    r = client.post(f"/v1/cases/{case}/query", headers=h, json={"question": question, "mode": mode})
    assert r.status_code == 200, r.text
    return r.json()


def test_must_01_preserve_originals_byte_for_byte(client, auth, pdf_bytes):
    """§3.2/§132: preserva originales y calcula checksum."""
    h = auth("abogada.alfa")
    case = create_case(client, h)
    data = pdf_bytes()
    doc = client.post(f"/v1/cases/{case['id']}/documents", headers=h, files={"file": ("o.pdf", data, "application/pdf")}).json()
    dl = client.get(f"/v1/cases/{case['id']}/documents/{doc['id']}/download", headers=h)
    assert dl.content == data and hashlib.sha256(dl.content).hexdigest() == doc["sha256"]


def test_must_02_every_answer_claim_cites_page_or_timestamp(client, auth, ids):
    """§133: sin cita inexistente, sin fuente inexistente; documento+página o media+timestamp."""
    h = auth("abogada.alfa")
    body = _q(client, h, ids["pago"], "¿Qué declaró la testigo sobre la firma del contrato en la audiencia?", "testimony")
    assert body["claims"]
    for c in body["citations"]:
        res = client.get(f"/v1/citations/{c['citation_id']}", headers=h).json()
        assert res["valid"] is True
        src = res["source"]
        if res["source_type"] == "document_page":
            assert src["page"] >= 1 and src["document_id"]
        else:
            assert 0 <= src["start_ms"] < src["end_ms"] and src["speaker"]


def test_must_03_can_open_source_page_and_timestamp(client, auth, ids):
    """§132: permite abrir la página fuente y el timestamp fuente."""
    h = auth("lector.alfa")
    for cl in ids["claims"]:
        for cid in cl["citation_ids"]:
            res = client.get(f"/v1/citations/{cid}", headers=h).json()
            if res["source_type"] == "document_page":
                page = client.get(f"/v1/cases/{ids['pago']}/documents/{res['source']['document_id']}/pages/{res['source']['page']}", headers=h)
                assert page.status_code == 200 and page.json()["text"]
            else:
                assert res["source"]["media_id"] and res["source"]["end_ms"] > res["source"]["start_ms"]


def test_must_04_allegations_are_distinguished_from_facts(client, auth, ids):
    """§1/§13: una afirmación de parte no es un hecho; los hechos disputados conservan ambas posturas."""
    facts = client.get(f"/v1/cases/{ids['pago']}/facts", headers=auth("lector.alfa")).json()
    assert {f["status"] for f in facts} <= {"ALLEGED", "DISPUTED", "SUPPORTED", "CONTRADICTED", "JUDICIALLY_DETERMINED", "UNRESOLVED"}
    assert all(f["status"] != "JUDICIALLY_DETERMINED" or f["determined_by_decision_id"] for f in facts)
    disputed = [f for f in facts if f["status"] in ("DISPUTED", "CONTRADICTED")]
    assert disputed and all({"asserts", "disputes"} <= {c["stance"] for c in f["claims"]} for f in disputed)
    assert all(c["claim_type"] and c["claimant"] for c in ids["claims"])


def test_must_05_relevant_contradictions_are_surfaced(client, auth, ids):
    """§133: una contradicción relevante no se omite; se marca para revisión humana."""
    body = _q(client, auth("abogada.alfa"), ids["pago"], "¿Cuándo se firmó el contrato?", "contradiction")
    assert body["related_contradictions"] and body["uncertainties"]


def test_must_06_insufficient_evidence_is_declared_localized(client, auth, ids):
    """§20: si no hay evidencia se dice explícitamente, en el idioma del usuario."""
    es = _q(client, auth("abogada.alfa", "es"), ids["vacio"], "¿Quién firmó?")
    en = _q(client, auth("abogada.alfa", "en"), ids["vacio"], "Who signed?")
    assert es["evidence_count"] == en["evidence_count"] == 0 and es["answer"] != en["answer"]


def test_must_07_human_review_keeps_history(client, auth, ids, owner_db):
    """§34/§95: la revisión no sobrescribe en silencio: versión +1, revisor y salida original conservados."""
    fact = client.get(f"/v1/cases/{ids['pago']}/facts", headers=auth("revisor.alfa")).json()[0]
    r = client.post(f"/v1/review/{fact['id']}", headers=auth("revisor.alfa"),
                    json={"entity_type": "fact", "action": "FLAG", "expected_version": fact["version"], "reason": "Requiere revisión"})
    assert r.status_code == 200 and r.json()["version"] == fact["version"] + 1
    rows = fetch(owner_db, "SELECT action, reviewer_id, original_output FROM reviews WHERE entity_id = %s", (fact["id"],))
    assert rows and rows[-1][0] == "FLAG" and rows[-1][1] and rows[-1][2]


def test_must_08_low_confidence_ocr_asr_requires_review(client, auth, ids, settings):
    """§134 riesgos 1-2: OCR/ASR bajo umbral se marca para revisión humana."""
    case = client.get(f"/v1/cases/{ids['pago']}", headers=auth("lector.alfa")).json()
    assert case["stats"]["items_requiring_review"] >= 2


def test_must_09_timeline_is_ordered_and_source_backed(client, auth, ids):
    """§132: genera timeline; cada evento con fuente."""
    tl = client.get(f"/v1/cases/{ids['pago']}/timeline", headers=auth("lector.alfa")).json()
    assert tl and [e["event_date"] for e in tl] == sorted(e["event_date"] for e in tl)
    assert all(e["sources"] and e["timeline_confidence"] in ("source_backed", "inferred", "ambiguous") for e in tl)


def test_must_10_every_error_code_and_enum_is_bilingual(client):
    """Requisito del proyecto: compatibilidad total es/en."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "packages" / "i18n"
    es, en = (json.loads((root / f"{ln}.json").read_text(encoding="utf-8")) for ln in ("es", "en"))
    assert set(es["errors"]) == set(en["errors"]) and set(es["messages"]) == set(en["messages"])
    e1 = client.get("/v1/meta/enums", headers={"Accept-Language": "es"}).json()["enums"]
    e2 = client.get("/v1/meta/enums", headers={"Accept-Language": "en"}).json()["enums"]
    assert e1.keys() == e2.keys() and any(e1[k] != e2[k] for k in e1)


def test_must_11_every_sensitive_action_is_audited(client, auth, ids, owner_db):
    """§132: registra auditoría (consultas IA, cargas, revisiones, legal hold)."""
    have = {a for (a,) in fetch(owner_db, "SELECT DISTINCT action FROM audit_logs")}
    client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"), json={"question": "pago", "mode": "fact_lookup"})
    have |= {a for (a,) in fetch(owner_db, "SELECT DISTINCT action FROM audit_logs")}
    assert {"auth.login", "ai.query"} <= have


def test_must_12_processing_is_idempotent(client, auth, ids):
    """§128: procesar dos veces lo mismo no duplica."""
    body = {"job_types": ["embedding"]}
    a = client.post(f"/v1/cases/{ids['pago']}/process", headers=auth("gestor.alfa"), json=body).json()["jobs"]
    b = client.post(f"/v1/cases/{ids['pago']}/process", headers=auth("gestor.alfa"), json=body).json()["jobs"]
    assert a[0]["id"] == b[0]["id"] and b[0]["reused"] is True
