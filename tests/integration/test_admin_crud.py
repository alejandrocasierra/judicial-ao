"""IT-ACR — CRUD de agentes, skills, modelos, roles y edición OCR/transcripción."""
import uuid

import pytest

pytestmark = pytest.mark.integration


def test_it_acr_query_with_selected_agent(client, auth, ids):
    agent = client.get("/v1/admin/agents", headers=auth("admin.alfa")).json()[0]
    r = client.post(f"/v1/cases/{ids['pago']}/query", headers=auth("abogada.alfa"),
                    json={"question": "¿Qué pruebas hay del pago?", "mode": "evidence_lookup",
                          "strategy": "agent", "agent_id": agent["id"]})
    assert r.status_code == 200, r.text
    assert "claims" in r.json()


def test_it_acr_00_system_agent_and_role_are_editable(client, auth):
    h = auth("admin.alfa")
    system = next(a for a in client.get("/v1/admin/agents", headers=h).json() if a["is_system"])
    r = client.patch(f"/v1/admin/agents/{system['id']}", headers=h, json={"system_prompt": system["system_prompt"]})
    assert r.status_code == 200, r.text
    # roles de sistema también se pueden editar (permisos/descripción)
    role = next(x for x in client.get("/v1/admin/roles", headers=h).json() if x["code"] == "ANALYST")
    r2 = client.patch(f"/v1/admin/roles/{role['id']}", headers=h, json={"permissions": role["permissions"]})
    assert r2.status_code == 200, r2.text


def test_it_acr_01_agent_crud(client, auth):
    h = auth("admin.alfa")
    # crear skill primero
    sk = client.post("/v1/admin/skills", headers=h, json={"name": "Resumen jurídico", "system_prompt": "Resume."}).json()
    assert sk["name"] == "Resumen jurídico"
    # crear agente enlazando la skill
    ag = client.post("/v1/admin/agents", headers=h, json={
        "name": "Agente de contradicciones", "system_prompt": "Detecta contradicciones.",
        "skills": [sk["id"]]}).json()
    assert ag["skills"] == [sk["id"]]
    # editar
    up = client.patch(f"/v1/admin/agents/{ag['id']}", headers=h, json={"name": "Agente v2"}).json()
    assert up["name"] == "Agente v2"
    # listar
    items = client.get("/v1/admin/agents", headers=h).json()
    assert any(a["id"] == ag["id"] for a in items)
    # borrar
    assert client.delete(f"/v1/admin/agents/{ag['id']}", headers=h).json()["deleted"] == ag["id"]


def test_it_acr_02_model_catalog_and_crud(client, auth):
    h = auth("admin.alfa")
    providers = {p["id"] for p in client.get("/v1/admin/models/providers", headers=h).json()["providers"]}
    assert {"anthropic", "openai", "gemini", "kimi", "deepseek"} <= providers
    m = client.post("/v1/admin/models", headers=h, json={
        "provider": "deepseek", "model_name": "deepseek-reasoner", "api_key": "sk-test", "is_default": True}).json()
    assert m["provider"] == "deepseek"
    listed = client.get("/v1/admin/models", headers=h).json()
    assert any(x["id"] == m["id"] and x["has_api_key"] for x in listed)
    client.delete(f"/v1/admin/models/{m['id']}", headers=h)


def test_it_acr_03_roles_list_and_custom(client, auth):
    h = auth("admin.alfa")
    roles = client.get("/v1/admin/roles", headers=h).json()
    codes = {r["code"] for r in roles}
    assert "ORG_ADMIN" in codes and "LAWYER" in codes
    perms = client.get("/v1/admin/permissions", headers=h).json()["permissions"]
    assert "ai.query" in perms
    # rol personalizado
    r = client.post("/v1/admin/roles", headers=h, json={
        "code": "PARALEGAL", "name": "Paralegal", "description": "Apoyo",
        "permissions": ["case.read", "document.read", "ai.query"]}).json()
    assert set(r["permissions"]) == {"case.read", "document.read", "ai.query"}
    # editar permisos
    up = client.patch(f"/v1/admin/roles/{r['id']}", headers=h, json={"permissions": ["case.read"]}).json()
    assert up["permissions"] == ["case.read"]
    # el rol crítico ORG_ADMIN no se elimina
    org_admin = next(x for x in roles if x["code"] == "ORG_ADMIN")
    assert client.delete(f"/v1/admin/roles/{org_admin['id']}", headers=h).status_code == 403
    client.delete(f"/v1/admin/roles/{r['id']}", headers=h)


def test_it_acr_04_roles_permissions_validation(client, auth):
    h = auth("admin.alfa")
    r = client.post("/v1/admin/roles", headers=h, json={
        "code": "BAD_ROLE", "name": "Bad", "permissions": ["not.a.permission"]})
    assert r.status_code == 422


def test_it_acr_05_document_page_edit(client, auth, ids, owner_db):
    """Corrige el OCR de una página semilla y restaura el estado exacto (no toca el corpus)."""
    h = auth("abogada.alfa")
    doc_id = client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()[0]["id"]
    n = client.get(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages", headers=h).json()["pages"][0]["page_number"]
    with owner_db.cursor() as cur:
        cur.execute("SELECT text, ocr_confidence, needs_review, human_corrected FROM document_pages "
                    "WHERE document_id=%s AND page_number=%s", (doc_id, n))
        snap = cur.fetchone()
        cur.execute("SELECT count(*) FROM chunks WHERE document_id = %s", (doc_id,))
        chunks_before = cur.fetchone()[0]
    try:
        r = client.patch(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages/{n}", headers=h,
                         json={"text": "Obligación indemnizatoria del demandado, corregida."})
        assert r.status_code == 200, r.text
        after = client.get(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages/{n}", headers=h).json()
        assert after["needs_review"] is False and "corregida" in after["text"]
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE document_pages SET text=%s, ocr_confidence=%s, needs_review=%s, human_corrected=%s "
                        "WHERE document_id=%s AND page_number=%s",
                        (snap[0], snap[1], snap[2], snap[3], doc_id, n))
            if chunks_before == 0:
                cur.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
        owner_db.commit()


def test_it_acr_06_media_segments_edit(client, auth, ids, owner_db):
    h = auth("abogada.alfa")
    media = client.get(f"/v1/cases/{ids['pago']}/media", headers=h).json()
    assert media
    mid = media[0]["id"]
    first = client.get(f"/v1/cases/{ids['pago']}/media/{mid}/segments", headers=h).json()["segments"][0]
    sid = first["id"]
    with owner_db.cursor() as cur:
        cur.execute("SELECT text, confidence, needs_review FROM transcript_segments WHERE id = %s", (sid,))
        snap = cur.fetchone()
        cur.execute("SELECT count(*) FROM chunks WHERE media_id = %s", (mid,))
        chunks_before = cur.fetchone()[0]
    try:
        r = client.patch(f"/v1/cases/{ids['pago']}/media/{mid}/segments/{sid}", headers=h,
                         json={"text": "Transcripción corregida del testigo."})
        assert r.status_code == 200, r.text
        after = client.get(f"/v1/cases/{ids['pago']}/media/{mid}/segments", headers=h).json()
        seg = next(s for s in after["segments"] if s["id"] == sid)
        assert "corregida" in seg["text"] and seg["needs_review"] is False
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE transcript_segments SET text=%s, confidence=%s, needs_review=%s WHERE id=%s",
                        (snap[0], snap[1], snap[2], sid))
            if chunks_before == 0:
                cur.execute("DELETE FROM chunks WHERE media_id = %s", (mid,))
        owner_db.commit()


def _first_media(client, h, case_id):
    media = client.get(f"/v1/cases/{case_id}/media", headers=h).json()
    assert media
    return media[0]["id"]


def test_it_acr_07_speaker_rename_updates_all_segments(client, auth, ids, owner_db):
    """Punto 1: edición GENERAL del nombre del hablante se refleja en sus segmentos."""
    h = auth("abogada.alfa")
    mid = _first_media(client, h, ids["pago"])
    spk = ids["speakers"]["SPK-03"]  # UNRESOLVED: se puede renombrar sin asociar parte
    before = client.get(f"/v1/cases/{ids['pago']}/media/{mid}/segments", headers=h).json()
    assert any(s["speaker_id"] == spk["id"] for s in before["segments"])
    new_name = f"Hablante {uuid.uuid4().hex[:6]}"
    try:
        r = client.post(f"/v1/review/{spk['id']}", headers=h, json={
            "entity_type": "speaker", "action": "EDIT", "expected_version": spk["version"],
            "changes": {"display_name": new_name}, "reason": "Edición general del nombre del hablante"})
        assert r.status_code == 200, r.text
        after = client.get(f"/v1/cases/{ids['pago']}/media/{mid}/segments", headers=h).json()
        owned = [s for s in after["segments"] if s["speaker_id"] == spk["id"]]
        assert owned and all(s["speaker_name"] == new_name for s in owned)
        # el tag de hablantes del media también lo muestra
        assert any(x["id"] == spk["id"] and x["display_name"] == new_name for x in after["speakers"])
        # pgvector (chunks) se reindexó con el nombre nuevo
        with owner_db.cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks WHERE media_id = %s AND metadata->>'speaker' = %s",
                        (mid, new_name))
            assert cur.fetchone()[0] >= 1, "pgvector debe reindexarse con el nuevo nombre"
            # y se encoló la reconstrucción del grafo
            cur.execute("SELECT count(*) FROM jobs WHERE case_id = %s AND job_type = 'graph_build'",
                        (ids["pago"],))
            assert cur.fetchone()[0] >= 1, "debe encolarse graph_build tras renombrar"
        # el hablante queda como nodo del grafo con su nombre nuevo
        rb = client.post(f"/v1/cases/{ids['pago']}/graph/build", headers=h)
        assert rb.status_code == 200, rb.text
        with owner_db.cursor() as cur:
            cur.execute("SELECT count(*) FROM graph_nodes WHERE case_id = %s AND node_type = 'Speaker' AND label = %s",
                        (ids["pago"], new_name))
            assert cur.fetchone()[0] >= 1, "el grafo debe mostrar el nombre nuevo del hablante"
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE speakers SET display_name = NULL, version = version - 1 WHERE id = %s",
                        (spk["id"],))
        owner_db.commit()


def test_it_acr_08_media_segment_speaker_edit(client, auth, ids, owner_db):
    """Punto 2: se puede corregir qué hablante dijo un segmento (independiente del texto)."""
    h = auth("abogada.alfa")
    mid = _first_media(client, h, ids["pago"])
    segs = client.get(f"/v1/cases/{ids['pago']}/media/{mid}/segments", headers=h).json()["segments"]
    seg = segs[0]
    other = next(s for s in ids["speakers"].values() if s["id"] != seg["speaker_id"])
    with owner_db.cursor() as cur:
        cur.execute("SELECT speaker_id FROM transcript_segments WHERE id = %s", (seg["id"],))
        snap = cur.fetchone()[0]
    try:
        # sólo el hablante: el texto no cambia y no se envía
        r = client.patch(f"/v1/cases/{ids['pago']}/media/{mid}/segments/{seg['id']}", headers=h,
                         json={"speaker_id": other["id"]})
        assert r.status_code == 200, r.text
        after = client.get(f"/v1/cases/{ids['pago']}/media/{mid}/segments", headers=h).json()["segments"]
        got = next(s for s in after if s["id"] == seg["id"])
        assert got["speaker_id"] == other["id"]
        assert got["text"] == seg["text"]
        # pgvector (chunks) refleja el hablante corregido
        with owner_db.cursor() as cur:
            cur.execute("SELECT metadata->>'speaker' FROM chunks WHERE media_id = %s AND metadata->>'segment_id' = %s",
                        (mid, seg["id"]))
            row = cur.fetchone()
        assert row and row[0] == other["label"], "pgvector debe reflejar el hablante corregido"
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE transcript_segments SET speaker_id = %s WHERE id = %s", (snap, seg["id"]))
        owner_db.commit()


def test_it_acr_09_media_segment_speaker_must_belong_to_case(client, auth, ids):
    """No se puede asignar a un segmento un hablante de otro expediente."""
    h = auth("abogada.alfa")
    mid = _first_media(client, h, ids["pago"])
    seg = client.get(f"/v1/cases/{ids['pago']}/media/{mid}/segments", headers=h).json()["segments"][0]
    r = client.patch(f"/v1/cases/{ids['pago']}/media/{mid}/segments/{seg['id']}", headers=h,
                     json={"speaker_id": str(uuid.uuid4())})
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "SPEAKER_NOT_FOUND"


def test_it_acr_10_ocr_confidence_100_and_drops_by_mode(client, auth, ids, owner_db):
    """Confianza OCR por modo: arranca al 100%, baja solo con ediciones reales y es
    independiente por motor (Básico vs Document AI)."""
    h = auth("abogada.alfa")
    doc_id = client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()[0]["id"]
    n = client.get(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages", headers=h).json()["pages"][0]["page_number"]
    with owner_db.cursor() as cur:
        cur.execute("SELECT text, ocr_confidence, needs_review, human_corrected FROM document_pages "
                    "WHERE document_id=%s AND page_number=%s", (doc_id, n))
        snap = cur.fetchone()
        cur.execute("DELETE FROM document_ocr_versions WHERE document_id=%s AND page_number=%s", (doc_id, n))
        cur.execute("""INSERT INTO document_ocr_versions (organization_id, document_id, page_number, mode, text, ocr_confidence)
                       SELECT organization_id, document_id, page_number, t.m, text, 1.0
                       FROM document_pages, (VALUES ('basico'), ('document_ai')) AS t(m)
                       WHERE document_id=%s AND page_number=%s""", (doc_id, n))
        cur.execute("SELECT count(*) FROM chunks WHERE document_id = %s", (doc_id,))
        chunks_before = cur.fetchone()[0]
    owner_db.commit()
    try:
        edited = "Texto corregido con palabras completamente distintas."
        r = client.patch(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages/{n}", headers=h,
                         json={"text": edited, "mode": "basico"})
        assert r.status_code == 200, r.text
        conf = r.json()["confidence"]
        assert 0.0 <= conf < 1.0, "una edición real debe bajar la confianza"
        with owner_db.cursor() as cur:
            cur.execute("SELECT ocr_confidence FROM document_ocr_versions "
                        "WHERE document_id=%s AND page_number=%s AND mode='basico'", (doc_id, n))
            assert abs(float(cur.fetchone()[0]) - conf) < 0.001
            cur.execute("SELECT ocr_confidence FROM document_ocr_versions "
                        "WHERE document_id=%s AND page_number=%s AND mode='document_ai'", (doc_id, n))
            assert float(cur.fetchone()[0]) == 1.0, "el otro modo no debe cambiar"
            cur.execute("SELECT text FROM document_pages WHERE document_id=%s AND page_number=%s", (doc_id, n))
            assert cur.fetchone()[0] == snap[0], "editar un modo no toca la página del otro"
            cur.execute("SELECT count(*) FROM jobs WHERE case_id=%s AND job_type='graph_build'", (ids["pago"],))
            assert cur.fetchone()[0] >= 1, "debe encolarse graph_build"
        # Sólo puntuación/espacios: la confianza no cambia.
        r2 = client.patch(f"/v1/cases/{ids['pago']}/documents/{doc_id}/pages/{n}", headers=h,
                          json={"text": edited + " .", "mode": "basico"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["confidence"] == conf
    finally:
        with owner_db.cursor() as cur:
            cur.execute("UPDATE document_pages SET text=%s, ocr_confidence=%s, needs_review=%s, human_corrected=%s "
                        "WHERE document_id=%s AND page_number=%s",
                        (snap[0], snap[1], snap[2], snap[3], doc_id, n))
            cur.execute("DELETE FROM document_ocr_versions WHERE document_id=%s AND page_number=%s", (doc_id, n))
            if chunks_before == 0:
                cur.execute("DELETE FROM chunks WHERE document_id=%s", (doc_id,))
        owner_db.commit()


def test_it_acr_11_model_catalog_dynamic_endpoint(client, auth):
    """El catálogo de modelos se consulta en vivo; sin API key cae al de referencia."""
    h = auth("admin.alfa")
    r = client.post("/v1/admin/models/available", headers=h, json={"provider": "openai"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "openai"
    assert body["source"] == "catalog"  # la semilla no tiene API key para el proveedor
    assert body["models"]


def test_it_acr_12_document_markdown_endpoint(client, auth, ids):
    """El documento se expone como Markdown con jerarquía (metadatos + páginas)."""
    h = auth("abogada.alfa")
    doc_id = client.get(f"/v1/cases/{ids['pago']}/documents", headers=h).json()[0]["id"]
    r = client.get(f"/v1/cases/{ids['pago']}/documents/{doc_id}/markdown", headers=h)
    assert r.status_code == 200, r.text
    md = r.json()["markdown"]
    assert md.startswith("#")
    assert "## Página 1" in md


def test_it_acr_13_direct_upload_presign_and_complete(client, auth, ids):
    """Subida directa: con storage local, presign devuelve mode=local y complete falla
    si el objeto no está en storage (evita registrar archivos inexistentes)."""
    h = auth("abogada.alfa")
    sha = "a" * 64
    payload = {"filename": "audiencia_grande.mp4", "sha256": sha, "size_bytes": 120 * 1024 * 1024,
               "content_type": "video/mp4"}
    r = client.post(f"/v1/cases/{ids['pago']}/uploads/presign", headers=h, json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "local"  # la suite usa storage local

    r2 = client.post(f"/v1/cases/{ids['pago']}/uploads/complete", headers=h,
                     json={"filename": "audiencia_grande.mp4", "sha256": sha, "size_bytes": 120 * 1024 * 1024,
                           "mime_type": "video/mp4"})
    assert r2.status_code == 404, r2.text  # el objeto no llegó al storage

    bad = client.post(f"/v1/cases/{ids['pago']}/uploads/presign", headers=h,
                      json={"filename": "malware.exe", "sha256": sha, "size_bytes": 10})
    assert bad.status_code == 415


def test_it_acr_14_create_and_merge_speakers(client, auth, ids):
    """Alta manual de hablantes y fusión de duplicados (reasigna segmentos y elimina el duplicado)."""
    h = auth("abogada.alfa")
    case = ids["pago"]
    a = client.post(f"/v1/cases/{case}/speakers", headers=h, json={"display_name": "Hablante Manual A"})
    assert a.status_code == 201, a.text
    b = client.post(f"/v1/cases/{case}/speakers", headers=h, json={"display_name": "Hablante Manual B"})
    assert b.status_code == 201, b.text
    aid, bid = a.json()["id"], b.json()["id"]
    assert a.json()["label"].startswith("SPEAKER_")
    assert aid != bid

    same = client.post(f"/v1/cases/{case}/speakers/merge", headers=h,
                       json={"keep_speaker_id": aid, "merge_speaker_id": aid})
    assert same.status_code == 422, same.text

    m = client.post(f"/v1/cases/{case}/speakers/merge", headers=h,
                    json={"keep_speaker_id": aid, "merge_speaker_id": bid})
    assert m.status_code == 200, m.text
    assert m.json()["merge_speaker_id"] == bid

    labels = {s["id"]: s["label"] for s in client.get(f"/v1/cases/{case}/speakers", headers=h).json()}
    assert aid in labels and bid not in labels
