"""Orquestador de OCR masivo (reanudable por BD + auto-refresh de token).

Para cada PDF: garantiza OCR Básico + Document AI consultando el estado real en la
base de datos.

Uso: python batch_ocr.py
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000/v1"
CASE_ID = "38865959-8d5f-44e4-bbc8-1226bd345b00"
ROOT = Path(r"C:\Users\ale13\OneDrive\Escritorio\judicial-ai\11001310302120180036100")
LOG = Path(r"C:\Users\ale13\AppData\Local\Temp\opencode\batch_ocr.log")
PG = ["docker", "exec", "judicial-ai-postgres-1", "psql", "-U", "postgres", "-d", "judicial", "-t", "-A", "-F", "\x1f", "-c"]

TERMINAL = {"OCR_COMPLETE", "REVIEW_REQUIRED", "FAILED", "INDEXED"}
EMAIL, PASSWORD = "admin@judicial.ai", "9O5[v0>tu-7b"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


class Session:
    """Cliente con re-login automático cuando el access token expira (401)."""

    def __init__(self) -> None:
        self.client = httpx.Client(timeout=600.0)
        self.token = ""
        self.login()

    def login(self) -> None:
        r = self.client.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def post(self, url: str, *, retry: bool = True, **kw) -> httpx.Response:
        r = self.client.post(url, headers=self._h(), **kw)
        if r.status_code == 401 and retry:
            log("    (token expirado; renovando…)")
            self.login()
            r = self.client.post(url, headers=self._h(), **kw)
        return r

    def get(self, url: str, *, retry: bool = True) -> httpx.Response:
        r = self.client.get(url, headers=self._h())
        if r.status_code == 401 and retry:
            self.login()
            r = self.client.get(url, headers=self._h())
        return r


def db_map() -> dict[tuple[str, str], dict]:
    sql = """
    WITH RECURSIVE tree AS (
      SELECT id, parent_id, name, name AS path FROM case_folders
      WHERE case_id='%s' AND parent_id IS NULL
      UNION ALL
      SELECT f.id, f.parent_id, f.name, t.path || '/' || f.name
      FROM case_folders f JOIN tree t ON f.parent_id = t.id
    )
    SELECT COALESCE(t.path,''), d.filename, d.id, d.processing_status,
           (SELECT count(*) FROM document_ocr_versions v WHERE v.document_id=d.id AND v.mode='basico'),
           (SELECT count(*) FROM document_ocr_versions v WHERE v.document_id=d.id AND v.mode='document_ai')
    FROM documents d LEFT JOIN tree t ON t.id = d.folder_id
    WHERE d.case_id='%s';""" % (CASE_ID, CASE_ID)
    out = subprocess.run(PG + [sql], capture_output=True, text=True, encoding="utf-8", errors="replace")
    result: dict[tuple[str, str], dict] = {}
    for line in out.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 6 or not parts[1]:
            continue
        folder, filename, doc_id, status, vbas, vdoc = parts
        result[(folder, filename)] = {"id": doc_id, "status": status,
                                      "basico": int(vbas or 0), "docai": int(vdoc or 0)}
    return result


def folder_id(sess: Session, name: str) -> str | None:
    safe = name.replace("'", "''")
    out = subprocess.run(
        PG + [f"SELECT id FROM case_folders WHERE case_id='{CASE_ID}' AND name = '{safe}' LIMIT 1"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return out.stdout.strip() or None


def wait_terminal(sess: Session, doc_id: str, label: str, timeout_s: int = 7200) -> str:
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout_s:
        try:
            r = sess.get(f"{API}/cases/{CASE_ID}/documents/{doc_id}")
            if r.status_code == 200:
                st = r.json().get("processing_status", "")
                if st != last:
                    log(f"    {label}: {st}")
                    last = st
                if st in TERMINAL:
                    return st
        except Exception as exc:  # noqa: BLE001
            log(f"    (poll {label}: {exc})")
        time.sleep(6)
    return "TIMEOUT"


def main() -> None:
    pdfs = [p for p in ROOT.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"]
    pdfs.sort(key=lambda p: p.stat().st_size)
    existing = db_map()
    log(f"=== inicio: {len(pdfs)} PDFs locales, {len(existing)} documentos en BD ===")

    sess = Session()
    for i, pdf in enumerate(pdfs, 1):
        rel = pdf.relative_to(ROOT)
        folder_path = rel.parent.as_posix()
        key = (folder_path, pdf.name)
        cur = existing.get(key)

        if cur and cur["basico"] > 0 and cur["docai"] > 0:
            continue

        size_mb = pdf.stat().st_size / 1_048_576
        log(f"[{i}/{len(pdfs)}] {rel.as_posix()} ({size_mb:.1f} MB)")
        try:
            doc_id = cur["id"] if cur else None
            if doc_id is None:
                with pdf.open("rb") as fh:
                    files = {"uploads": (pdf.name, fh, "application/pdf")}
                    data = {"ocr_mode": "basico"}
                    fid = folder_id(sess, folder_path.split("/")[-1])
                    if fid:
                        data["folder_id"] = fid
                    r = sess.post(f"{API}/cases/{CASE_ID}/files", files=files, data=data)
                if r.status_code != 201:
                    log(f"    ERROR subida {r.status_code}: {r.text[:140]}")
                    continue
                res = r.json()["results"][0]
                if res.get("status") != "uploaded":
                    log(f"    subida no-ok ({res.get('code')}); saltando")
                    existing[key] = {"id": "?", "status": "?", "basico": 0, "docai": 0}
                    continue
                doc_id = res["id"]
                log(f"    subido {doc_id[:8]}")
                st = wait_terminal(sess, doc_id, "Básico")
                log(f"    Básico -> {st}")
            if cur is not None and cur["basico"] == 0:
                rb = sess.post(f"{API}/cases/{CASE_ID}/documents/{doc_id}/reprocess",
                               params={"ocr_mode": "basico"})
                if rb.status_code == 202:
                    stb = wait_terminal(sess, doc_id, "Básico")
                    log(f"    Básico -> {stb}")
                else:
                    log(f"    ERROR reproceso Básico {rb.status_code}: {rb.text[:140]}")
            if cur is None or cur["docai"] == 0:
                r2 = sess.post(f"{API}/cases/{CASE_ID}/documents/{doc_id}/reprocess",
                               params={"ocr_mode": "document_ai"})
                if r2.status_code != 202:
                    log(f"    ERROR reproceso {r2.status_code}: {r2.text[:140]}")
                else:
                    st2 = wait_terminal(sess, doc_id, "DocumentAI")
                    log(f"    Document AI -> {st2}")
        except Exception as exc:  # noqa: BLE001
            log(f"    EXCEPCIÓN: {exc}")
            continue
    log("=== FIN ===")


if __name__ == "__main__":
    main()
