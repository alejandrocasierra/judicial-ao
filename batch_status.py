"""Estado del lote: compara PDFs locales vs documentos en BD con ambas versiones."""
from __future__ import annotations

import subprocess
from pathlib import Path

CASE_ID = "38865959-8d5f-44e4-bbc8-1226bd345b00"
ROOT = Path(r"C:\Users\ale13\OneDrive\Escritorio\judicial-ai\11001310302120180036100")
PG = ["docker", "exec", "judicial-ai-postgres-1", "psql", "-U", "postgres", "-d", "judicial", "-t", "-A", "-F", "\x1f", "-c"]

sql = """
WITH RECURSIVE tree AS (
  SELECT id, parent_id, name, name AS path FROM case_folders
  WHERE case_id='%s' AND parent_id IS NULL
  UNION ALL
  SELECT f.id, f.parent_id, f.name, t.path || '/' || f.name
  FROM case_folders f JOIN tree t ON f.parent_id = t.id
)
SELECT COALESCE(t.path,''), d.filename, d.processing_status,
       (SELECT count(*) FROM document_ocr_versions v WHERE v.document_id=d.id AND v.mode='basico'),
       (SELECT count(*) FROM document_ocr_versions v WHERE v.document_id=d.id AND v.mode='document_ai')
FROM documents d LEFT JOIN tree t ON t.id = d.folder_id
WHERE d.case_id='%s';""" % (CASE_ID, CASE_ID)

out = subprocess.run(PG + [sql], capture_output=True, text=True, encoding="utf-8", errors="replace")
db: dict[tuple[str, str], tuple[int, int]] = {}
for line in out.stdout.splitlines():
    p = line.split("\x1f")
    if len(p) != 5 or not p[1]:
        continue
    db[(p[0], p[1])] = (int(p[3] or 0), int(p[4] or 0))

pdfs = [p for p in ROOT.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"]
total = len(pdfs)
ambos = solo_basico = solo_docai = ninguno = 0
faltan = []
for pdf in pdfs:
    rel = pdf.relative_to(ROOT)
    key = (rel.parent.as_posix(), pdf.name)
    b, d = db.get(key, (0, 0))
    if b > 0 and d > 0:
        ambos += 1
    elif b > 0:
        solo_basico += 1
        faltan.append(rel.as_posix())
    elif d > 0:
        solo_docai += 1
        faltan.append(rel.as_posix())
    else:
        ninguno += 1
        faltan.append(rel.as_posix())

print(f"PDFs locales:                     {total}")
print(f"  con AMBOS modos (ok):           {ambos}")
print(f"  solo Básico (falta Document AI): {solo_basico}")
print(f"  solo Document AI (falta Básico): {solo_docai}")
print(f"  sin subir / duplicados:          {ninguno}")
print(f"  PENDIENTES DE CORREGIR:          {len(faltan)}")
print("--- muestra de pendientes ---")
for f in faltan[:30]:
    print("  " + f)
