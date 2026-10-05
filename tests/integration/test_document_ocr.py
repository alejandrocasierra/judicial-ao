"""IT-OCR — pipeline documental OCR (Fase 2)."""
from __future__ import annotations

import io

import pytest

from tests.helpers import create_case

pytestmark = pytest.mark.integration


def _pdf_bytes(text: str = "Demanda principal") -> bytes:
    from seeds.pdfgen import make_pdf
    return make_pdf([text])


def test_it_ocr_01_document_ocr_creates_pages_and_images(client, auth, owner_db):
    h = auth("admin.alfa")
    case = create_case(client, h, "OCR test")
    case_id = case["id"]

    # Sube un PDF de 1 página (seeds.pdfgen genera varias páginas por texto)
    pdf = _pdf_bytes("Página de prueba para OCR")
    r = client.post(
        f"/v1/cases/{case_id}/documents",
        headers=h,
        files={"file": ("test.pdf", io.BytesIO(pdf), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]

    # Lanza el job document_ocr (Celery eager ejecuta sincrónicamente)
    r = client.post(f"/v1/cases/{case_id}/process", headers=h, json={"job_types": ["document_ocr"]})
    assert r.status_code == 202, r.text

    with owner_db.cursor() as cur:
        cur.execute("SELECT processing_status, page_count, document_type FROM documents WHERE id = %s", (doc_id,))
        status, page_count, doc_type = cur.fetchone()
        cur.execute("SELECT count(*) FROM document_pages WHERE document_id = %s", (doc_id,))
        page_rows = cur.fetchone()[0]
        cur.execute("SELECT image_uri FROM document_pages WHERE document_id = %s LIMIT 1", (doc_id,))
        image = cur.fetchone()
    owner_db.rollback()

    assert status == "OCR_COMPLETE"
    assert page_count is not None and page_count > 0
    assert page_rows == page_count
    assert image and image[0]  # tiene imagen rasterizada
    assert doc_type  # clasificación heurística asignó algún tipo


def test_it_ocr_02_classification_indexes_document(client, auth, owner_db):
    h = auth("admin.alfa")
    case = create_case(client, h, "Classification test")
    case_id = case["id"]

    pdf = _pdf_bytes("Auto que resuelve el incidente de nulidad")
    r = client.post(
        f"/v1/cases/{case_id}/documents",
        headers=h,
        files={"file": ("auto.pdf", io.BytesIO(pdf), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]

    r = client.post(f"/v1/cases/{case_id}/process", headers=h, json={"job_types": ["document_ocr", "document_classification"]})
    assert r.status_code == 202, r.text

    with owner_db.cursor() as cur:
        cur.execute("SELECT processing_status, document_type FROM documents WHERE id = %s", (doc_id,))
        status, doc_type = cur.fetchone()
    owner_db.rollback()

    assert status == "INDEXED"
    assert doc_type != "other/unknown"


def test_it_ocr_03_reprocess_preserves_human_correction(client, auth, owner_db):
    """Reprocesar no debe pisar una página corregida a mano (human_corrected)."""
    h = auth("admin.alfa")
    case = create_case(client, h, "Preserva corrección OCR")
    case_id = case["id"]

    pdf = _pdf_bytes("Demanda principal del proceso")
    r = client.post(
        f"/v1/cases/{case_id}/documents", headers=h,
        files={"file": ("test.pdf", io.BytesIO(pdf), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]

    r = client.post(f"/v1/cases/{case_id}/process", headers=h, json={"job_types": ["document_ocr"]})
    assert r.status_code == 202, r.text

    corrected = "RAMA JUDICIAL DEL PODER PÚBLICO — texto corregido por una persona"
    r = client.patch(f"/v1/cases/{case_id}/documents/{doc_id}/pages/1", headers=h, json={"text": corrected})
    assert r.status_code == 200, r.text

    # Reprocesa (nueva ejecución de OCR): la corrección humana debe sobrevivir.
    r = client.post(f"/v1/cases/{case_id}/documents/{doc_id}/reprocess", headers=h)
    assert r.status_code == 202, r.text

    with owner_db.cursor() as cur:
        cur.execute("SELECT text, human_corrected FROM document_pages WHERE document_id = %s AND page_number = 1", (doc_id,))
        text, human = cur.fetchone()
    owner_db.rollback()

    assert human is True
    assert text == corrected
