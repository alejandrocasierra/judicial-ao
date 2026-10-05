"""SEC-UPL: carga de archivos (SSD §20.2, amenaza T6 — archivos maliciosos)."""
from __future__ import annotations

import uuid

import pytest

from helpers import assert_error, create_case, fetch

EICAR = (b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" + b"!$H+H*")


@pytest.fixture
def case(client, auth):
    return create_case(client, auth("abogada.alfa"), title="Caso para pruebas de carga")


def _up(client, auth, case, name, data, ctype="application/pdf", kind="documents"):
    return client.post(f"/v1/cases/{case['id']}/{kind}", headers=auth("abogada.alfa"), files={"file": (name, data, ctype)})


@pytest.mark.parametrize("name", ["malware.exe", "script.sh", "pagina.html", "doble.pdf.exe", "sin_extension", "archivo.svg"])
def test_sec_upl_01_disallowed_extensions(client, auth, case, pdf_bytes, name):
    assert_error(_up(client, auth, case, name, pdf_bytes()), 415, "UPLOAD_TYPE_NOT_ALLOWED")


def test_sec_upl_02_extension_content_mismatch(client, auth, case):
    fake_pdf = b"MZ\x90\x00" + uuid.uuid4().bytes * 8  # ejecutable PE disfrazado de PDF
    assert_error(_up(client, auth, case, "contrato.pdf", fake_pdf), 415, "UPLOAD_CONTENT_MISMATCH")


def test_sec_upl_03_declared_content_type_mismatch(client, auth, case, pdf_bytes):
    assert_error(_up(client, auth, case, "contrato.pdf", pdf_bytes(), ctype="image/png"), 415, "UPLOAD_CONTENT_MISMATCH")


def test_sec_upl_04_empty_file(client, auth, case):
    assert_error(_up(client, auth, case, "vacio.pdf", b""), 422, "UPLOAD_EMPTY")


def test_sec_upl_05_malware_signature_rejected_and_not_stored(client, auth, case, owner_db):
    data = b"%PDF-1.4\n" + EICAR + b"\n%%EOF"
    assert_error(_up(client, auth, case, "infectado.pdf", data), 422, "UPLOAD_MALWARE_DETECTED")
    assert fetch(owner_db, "SELECT count(*) FROM documents WHERE case_id = %s", (case["id"],)) == [(0,)]


def test_sec_upl_06_size_limit(client, auth, case, pdf_bytes, settings, monkeypatch):
    data = pdf_bytes()
    monkeypatch.setattr(settings, "UPLOAD_MAX_BYTES_DOCUMENT", len(data) - 1)
    assert_error(_up(client, auth, case, "grande.pdf", data), 413, "UPLOAD_TOO_LARGE")


@pytest.mark.parametrize("name,expected", [
    ("../../../etc/passwd.pdf", "passwd.pdf"),
    ("..\\..\\windows\\system32\\evil.pdf", "evil.pdf"),
    ("tab\tulado.pdf", "tabulado.pdf"),
    (".oculto.pdf", "oculto.pdf"),
    ("a<b>c:d|e?.pdf", "a_b_c_d_e_.pdf"),
])
def test_sec_upl_07_filename_sanitized(client, auth, case, pdf_bytes, name, expected):
    r = _up(client, auth, case, name, pdf_bytes())
    assert r.status_code == 201, r.text
    assert r.json()["filename"] == expected
    assert not any(ord(ch) < 32 for ch in r.json()["filename"])


def test_sec_upl_08_storage_key_is_content_addressed_not_user_controlled(client, auth, case, pdf_bytes, owner_db):
    r = _up(client, auth, case, "../../ruta-maliciosa.pdf", pdf_bytes())
    uri = fetch(owner_db, "SELECT storage_uri FROM documents WHERE id = %s", (r.json()["id"],))[0][0]
    assert "ruta-maliciosa" not in uri and ".." not in uri and r.json()["sha256"] in uri


def test_sec_upl_09_media_endpoint_rejects_documents_and_vice_versa(client, auth, case, pdf_bytes):
    wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + uuid.uuid4().bytes
    assert_error(_up(client, auth, case, "doc.pdf", pdf_bytes(), kind="media"), 415, "UPLOAD_TYPE_NOT_ALLOWED")
    assert_error(_up(client, auth, case, "audio.wav", wav, ctype="audio/wav"), 415, "UPLOAD_TYPE_NOT_ALLOWED")


def test_sec_upl_10_download_verifies_integrity(client, auth, case, pdf_bytes, settings):
    """Si el objeto almacenado es alterado, la descarga falla en vez de servir evidencia manipulada."""
    r = _up(client, auth, case, "integridad.pdf", pdf_bytes()).json()
    h = auth("abogada.alfa")
    assert client.get(f"/v1/cases/{case['id']}/documents/{r['id']}/download", headers=h).status_code == 200
    if settings.STORAGE_BACKEND != "local":
        pytest.skip("manipulación directa sólo en almacenamiento local")
    root = settings.path(settings.STORAGE_LOCAL_ROOT)
    candidates = [p for p in root.rglob("*") if p.is_file() and r["sha256"] in str(p)]
    assert candidates, "objeto almacenado no encontrado"
    p = candidates[0]
    original = p.read_bytes()
    p.chmod(0o600)
    p.write_bytes(original + b"manipulado")
    try:
        resp = client.get(f"/v1/cases/{case['id']}/documents/{r['id']}/download", headers=h)
        assert resp.status_code == 500 and b"manipulado" not in resp.content
    finally:
        p.write_bytes(original)
