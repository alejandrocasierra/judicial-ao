"""Lista páginas con baja confianza y renderiza algunas para análisis."""
import sys
from pathlib import Path

import pymupdf as fitz

sys.path.insert(0, 'apps/api')
from dotenv import load_dotenv
load_dotenv('.env')
import os, psycopg

ROOT = Path('C:/Users/ale13/OneDrive/Escritorio/judicial-ai')
CASE_ID = '182a09e0-deeb-4918-80f6-19cf350b4087'
OUT_DIR = ROOT / 'var' / 'low_confidence_samples'
OUT_DIR.mkdir(parents=True, exist_ok=True)

def find_pdf(filename):
    matches = list((ROOT / '11001310302120180036100').rglob(filename))
    return matches[0] if matches else None

c = psycopg.connect(host=os.environ['POSTGRES_HOST'], port=os.environ['POSTGRES_PORT'], dbname=os.environ['POSTGRES_DB'],
                    user=os.environ['POSTGRES_SUPERUSER'], password=os.environ['POSTGRES_SUPERUSER_PASSWORD'])
cur = c.cursor()
cur.execute("""
    SELECT d.filename, dp.page_number, dp.ocr_confidence
    FROM document_pages dp
    JOIN documents d ON d.id = dp.document_id
    WHERE d.case_id = %s AND dp.ocr_confidence < 0.85
    ORDER BY dp.ocr_confidence ASC
""", (CASE_ID,))
rows = cur.fetchall()
print(f"Páginas bajo 0.85: {len(rows)}")
for filename, pn, conf in rows[:20]:
    print(f"  {filename} pág {pn}: conf={conf:.3f}")

# Renderiza las 5 peores para inspección visual
for filename, pn, conf in rows[:5]:
    pdf_path = find_pdf(filename)
    if not pdf_path:
        continue
    with fitz.open(pdf_path) as doc:
        page = doc.load_page(pn - 1)
        pix = page.get_pixmap(dpi=300)
        out = OUT_DIR / f"{Path(filename).stem}_p{pn:03d}_conf{conf:.3f}.png"
        pix.save(out)
        print(f"Guardada imagen: {out}")
c.close()
