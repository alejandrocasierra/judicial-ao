"""Borra el media FAILED del intento anterior y re-subo el 0065 (prueba de estrés)."""
import os
import pathlib
import time

import requests

BASE = "http://127.0.0.1:8000/v1"
CASE = "38865959-8d5f-44e4-bbc8-1226bd345b00"
ADMIN_EMAIL = os.environ.get("JUDICIAL_ADMIN_EMAIL", "admin@judicial.ai")
ADMIN_PASSWORD = os.environ["JUDICIAL_ADMIN_PASSWORD"]
FOLDER = "370ded8a-f23e-4b6b-9cb0-3a0fb08c282f"
OLD_MEDIA_ID = "6b71c3a5-3e7b-4ef4-9e8e-760484488168"
FILE = pathlib.Path(r"C:\Users\ale13\OneDrive\Escritorio\judicial-ai\11001310302120180036100"
                    r"\01PrimeraInstancia\0003AcumuladoEjecutHipotecario201800361"
                    r"\0065GrabacionAudiencia13Dic2024.mp4")

r = requests.post(f"{BASE}/auth/login",
                  json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=30)
r.raise_for_status()
h = {"Authorization": f"Bearer " + r.json()["access_token"]}
print("[login] OK", flush=True)

r = requests.delete(f"{BASE}/cases/{CASE}/media/{OLD_MEDIA_ID}", headers=h, timeout=120)
print(f"[delete viejo] {r.status_code} {r.text[:150]}", flush=True)

t0 = time.time()
with open(FILE, "rb") as fh:
    r = requests.post(f"{BASE}/cases/{CASE}/files", headers=h,
                      files={"uploads": (FILE.name, fh, "video/mp4")},
                      data={"folder_id": FOLDER, "ocr_mode": "basico"}, timeout=(10, None))
r.raise_for_status()
print(f"[ok] {r.json()['results'][0]} ({time.time() - t0:.0f}s)", flush=True)
