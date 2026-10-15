"""Borra los 9 videos subidos en la tanda fallida (OOM) con el flujo oficial
file_delete.delete_media: cascada completa (citas, chunks/pgvector, segmentos,
evidence, speakers por cascada FK) + storage (GCS key y copia local incoming)."""
import os

import requests

BASE = "http://127.0.0.1:8000/v1"
CASE = "38865959-8d5f-44e4-bbc8-1226bd345b00"
ADMIN_EMAIL = os.environ.get("JUDICIAL_ADMIN_EMAIL", "admin@judicial.ai")
ADMIN_PASSWORD = os.environ["JUDICIAL_ADMIN_PASSWORD"]

MEDIA_IDS = [
    "5d558e4d-b588-456e-86e4-640193c1ee01",  # 0065
    "a3b7a63f-0a82-4c13-9c4f-6b5cd9a46e4d",  # 0083
    "b4989f8f-bc46-4aab-aff7-f57b993d95e3",  # 0084
    "df3f241c-2cba-49ed-bd44-ea46c195f44e",  # 0048
    "97a3b5f4-075a-45ce-a5a4-2db8064c5208",  # 0061
    "8cd9ac24-cc69-4339-89bc-879a5cb96b77",  # 015
    "15db829c-22c9-47a7-ae27-333484da1ef6",  # 021
    "109c2c13-dd65-44e8-8442-9e4521dc9ab1",  # 022
    "0e552c5f-5ebd-49a8-9f34-7be38e7d8cf2",  # 023
]

r = requests.post(f"{BASE}/auth/login",
                  json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
r.raise_for_status()
token = r.json()["access_token"]
print("[login] OK")

for mid in MEDIA_IDS:
    r = requests.delete(f"{BASE}/cases/{CASE}/media/{mid}",
                        headers={"Authorization": f"Bearer {token}"}, timeout=120)
    print(f"[{'ok' if r.ok else 'ERROR'}] {mid}: {r.status_code} {r.text[:200]}")
print("[done]")
