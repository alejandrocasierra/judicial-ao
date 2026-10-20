"""Corrige los datos del 0065: desasigna los 23 segmentos del 'hablante' falso
('Correo: Juzgado 21 Civil Circuil' = texto leído por el OCR de Teams) y los deja
como 'Sin identificar' + marcados para revisión. Usa el flujo oficial de la API."""
import os

import requests
import psycopg

BASE = "http://127.0.0.1:8000/v1"
CASE = "38865959-8d5f-44e4-bbc8-1226bd345b00"
ADMIN_EMAIL = os.environ.get("JUDICIAL_ADMIN_EMAIL", "admin@judicial.ai")
ADMIN_PASSWORD = os.environ["JUDICIAL_ADMIN_PASSWORD"]

r = requests.post(f"{BASE}/auth/login",
                  json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
r.raise_for_status()
h = {"Authorization": "Bearer " + r.json()["access_token"]}

DSN = os.environ.get("JUDICIAL_TEST_DSN") or (
    f"host={os.environ.get('POSTGRES_HOST', '127.0.0.1')} "
    f"port={os.environ.get('POSTGRES_PORT', '54322')} "
    f"dbname={os.environ.get('POSTGRES_DB', 'judicial')} "
    f"user={os.environ.get('POSTGRES_SUPERUSER', 'postgres')} "
    f"password={os.environ['POSTGRES_SUPERUSER_PASSWORD']}")
with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    cur.execute("""SELECT id FROM speakers WHERE media_id='6930961d-ab78-4aa4-9dd3-0e074d5ea412'
                   AND label='SPEAKER_05'""")
    spk = cur.fetchone()
print("speaker a desasignar:", spk[0] if spk else None)

if spk:
    r = requests.delete(f"{BASE}/cases/{CASE}/speakers/{spk[0]}", headers=h, timeout=120)
    print(f"[{'ok' if r.ok else 'ERROR'}] delete speaker: {r.status_code} {r.text[:200]}")
