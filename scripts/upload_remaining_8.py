"""Sube los 8 videos restantes (0065 ya está en procesamiento como prueba de estrés).
Carpetas destino según el expediente:
- 0003AcumuladoEjecutHipotecario201800361: 0083, 0084
- 0006 MedidasCautelaresAcumulado: 0048, 0061
- 0028DiligenciaDespachoComisorio: 015, 021, 022, 023
"""
from __future__ import annotations

import pathlib
import sys
import time

import requests

BASE = "http://127.0.0.1:8000/v1"
CASE = "38865959-8d5f-44e4-bbc8-1226bd345b00"
EMAIL = os.environ.get("JUDICIAL_ADMIN_EMAIL", "admin@judicial.ai")
PASSWORD = os.environ["JUDICIAL_ADMIN_PASSWORD"]

ROOT = pathlib.Path(r"C:\Users\ale13\OneDrive\Escritorio\judicial-ai\11001310302120180036100\01PrimeraInstancia")

TASKS = [
    ("370ded8a-f23e-4b6b-9cb0-3a0fb08c282f", ROOT / "0003AcumuladoEjecutHipotecario201800361", [
        "0083Grabacion25Junio2025Parte1.mp4",
        "0084Grabacion25JunioParte2.mp4",
    ]),
    ("9e47587d-5027-46d3-b2f7-1c16a88130a1", ROOT / "0006 MedidasCautelaresAcumulado", [
        "0048Grabacion4Diciembre2025.mp4",
        "0061GrabacioinAudiencia16-09-2026.mp4",
    ]),
    ("ceeea92f-5362-4afa-b9ea-ae1c08f142c3", ROOT / "0006 MedidasCautelaresAcumulado" / "0028DiligenciaDespachoComisorio", [
        "015InstalacionAudiencia.MOV",
        "021IngresoInmuebleBodega1.MOV",
        "022PresentacionOposicionIntervencionDemandado.MOV",
        "023ResuelveOposicion.MOV",
    ]),
]

MIME = {".mp4": "video/mp4", ".mov": "video/quicktime"}


def login() -> str:
    r = requests.post(f"{BASE}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    token = login()
    print("[login] OK", flush=True)
    failures = 0
    for folder_id, folder_path, names in TASKS:
        for name in names:
            fp = folder_path / name
            if not fp.exists():
                print(f"[ERROR] no existe: {fp}", flush=True)
                failures += 1
                continue
            size_mb = fp.stat().st_size / (1024 * 1024)
            print(f"[upload] {name} ({size_mb:.0f} MB) -> carpeta {folder_id[:8]}...", flush=True)
            t0 = time.time()
            try:
                with open(fp, "rb") as fh:
                    r = requests.post(
                        f"{BASE}/cases/{CASE}/files",
                        headers={"Authorization": f"Bearer {token}"},
                        files={"uploads": (name, fh, MIME.get(fp.suffix.lower(), "application/octet-stream"))},
                        data={"folder_id": folder_id, "ocr_mode": "basico"},
                        timeout=(10, None),
                    )
                if r.status_code == 401:
                    token = login()
                    with open(fp, "rb") as fh:
                        r = requests.post(
                            f"{BASE}/cases/{CASE}/files",
                            headers={"Authorization": f"Bearer {token}"},
                            files={"uploads": (name, fh, MIME.get(fp.suffix.lower(), "application/octet-stream"))},
                            data={"folder_id": folder_id, "ocr_mode": "basico"},
                            timeout=(10, None),
                        )
                r.raise_for_status()
                res = r.json()["results"][0]
                dt = time.time() - t0
                print(f"[ok] {name}: {res.get('status')} kind={res.get('kind')} "
                      f"job={str(res.get('job_id'))[:8]} ({dt:.0f}s)", flush=True)
                if res.get("status") != "uploaded":
                    failures += 1
            except Exception as exc:
                failures += 1
                print(f"[ERROR] {name}: {exc}", flush=True)
    print(f"[done] fallos={failures}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
