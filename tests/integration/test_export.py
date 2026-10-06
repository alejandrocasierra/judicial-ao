"""IT-EXP — exportación CKP (Case Knowledge Package)."""
import io
import json
import zipfile

import pytest

pytestmark = pytest.mark.integration


def test_it_exp_01_export_requires_auth(client):
    r = client.get("/v1/cases/00000000-0000-0000-0000-000000000000/export")
    assert r.status_code == 401


def test_it_exp_02_export_requires_permission(client, auth, ids):
    # ANALYST no tiene ai.export
    r = client.get(f"/v1/cases/{ids['pago']}/export", headers=auth("analista.alfa"))
    assert r.status_code == 403


def test_it_exp_03_export_returns_zip(client, auth, ids):
    r = client.get(f"/v1/cases/{ids['pago']}/export", headers=auth("admin.alfa"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]

    # Validar que es un ZIP válido
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()

    # Debe contener manifest y entidades principales
    assert "manifest.json" in names
    assert "case.json" in names
    assert "entities/parties.jsonl" in names
    assert "entities/claims.jsonl" in names
    assert "documents/documents.jsonl" in names
    assert "documents/document_pages.jsonl" in names
    assert "chunks/chunks.jsonl" in names
    assert any(n.startswith("documents/") and n.endswith("/document.md") for n in names)
    assert any(n.startswith("documents/") and "/pages/" in n and n.endswith(".json") for n in names)
    assert "events/timeline.json" in names
    assert "README.md" in names
    assert "graph/nodes.jsonl" in names
    assert "audit/audit_logs.jsonl" in names

    # Manifest debe ser JSON válido
    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    assert manifest["case_id"] == ids["pago"]
    assert "pipeline_version" in manifest
    assert "schema_version" in manifest


def test_it_exp_04_export_content_is_valid_jsonl(client, auth, ids):
    r = client.get(f"/v1/cases/{ids['pago']}/export", headers=auth("admin.alfa"))
    assert r.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(r.content))

    # Validar que los JSONL son válidos
    for name in zf.namelist():
        if name.endswith(".jsonl"):
            content = zf.read(name).decode("utf-8")
            if content:
                for line in content.splitlines():
                    json.loads(line)  # debe ser JSON válido
