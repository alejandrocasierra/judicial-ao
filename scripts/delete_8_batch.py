"""Detiene y borra los 8 videos que el usuario pidió eliminar (BD, pgvector, grafo).
Usa el flujo oficial file_delete.delete_media: cascada (citas, chunks/pgvector,
segmentos, evidence) + storage (GCS + copia local). Solo estos 8; el 0065 se conserva.
"""
import os

import requests

BASE = "http://127.0.0.1:8000/v1"
CASE = "38865959-8d5f-44e4-bbc8-1226bd345b00"
ADMIN_EMAIL = os.environ.get("JUDICIAL_ADMIN_EMAIL", "admin@judicial.ai")
ADMIN_PASSWORD = os.environ["JUDICIAL_ADMIN_PASSWORD"]

MEDIA_IDS = [
    "681b6b92-bba6-47d2-871e-eac17754117b",  # 0083
    "e8e96a3a-6fe6-460a-9fcb-8d83af4876ff",  # 0084
    "91f0378f-12e6-4690-b693-02fe9043b3a8",  # 0048
    "9e972ec2-c880-408a-91ac-a0184e72cf7b",  # 0061
    "f396f6a8-eb94-449f-b773-a13d3178e7d9",  # 015
    "7b14e8dc-befd-4eed-aa9b-ed7fd76572e1",  # 021
    "8b059c1f-2fd4-4723-bffe-2bd30641fb68",  # 022
    "9b6020e3-f849-41c5-8668-897ddf9bf558",  # 023
]

r = requests.post(f"{BASE}/auth/login",
                  json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
r.raise_for_status()
h = {"Authorization": "Bearer " + r.json()["access_token"]}
print("[login] OK", flush=True)

for mid in MEDIA_IDS:
    r = requests.delete(f"{BASE}/cases/{CASE}/media/{mid}", headers=h, timeout=120)
    print(f"[{'ok' if r.ok else 'ERROR'}] {mid}: {r.status_code} {r.text[:120]}", flush=True)
print("[done]", flush=True)
