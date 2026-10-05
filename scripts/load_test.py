#!/usr/bin/env python3
"""Load testing de uploads y queries (SSD §113)."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

_env_path = Path(__file__).resolve().parents[1] / ".env"
if _env_path.exists():
    import envload  # noqa: E402
    envload.load(str(_env_path))

import httpx  # noqa: E402

API_URL = "http://localhost:8000/v1"


def login(email: str, password: str) -> str:
    r = httpx.post(f"{API_URL}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def upload_document(token: str, case_id: UUID, filename: str, content: bytes) -> dict:
    t0 = time.time()
    r = httpx.post(
        f"{API_URL}/cases/{case_id}/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, "application/pdf")},
        timeout=30.0,
    )
    elapsed = time.time() - t0
    return {"status_code": r.status_code, "elapsed": elapsed, "size_bytes": len(content)}


def query_case(token: str, case_id: UUID, question: str) -> dict:
    t0 = time.time()
    r = httpx.post(
        f"{API_URL}/cases/{case_id}/query",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": question, "mode": "fact_lookup"},
        timeout=30.0,
    )
    elapsed = time.time() - t0
    return {"status_code": r.status_code, "elapsed": elapsed}


def run_load_test(case_id: UUID, org_id: UUID, user_id: UUID, num_uploads: int, num_queries: int) -> dict:
    """Ejecuta load test de uploads y queries."""
    token = login("admin.alfa@example.test", "lmdUt%xP0QnXq-vPE!TO")

    # Crear un PDF de prueba
    pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF"

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "case_id": str(case_id),
        "num_uploads": num_uploads,
        "num_queries": num_queries,
        "uploads": [],
        "queries": [],
    }

    # Load test de uploads
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(upload_document, token, case_id, f"load_test_{i}.pdf", pdf_content)
            for i in range(num_uploads)
        ]
        for f in concurrent.futures.as_completed(futures):
            results["uploads"].append(f.result())

    # Load test de queries
    questions = [
        "¿Quién es el demandante?",
        "¿Cuál es el monto del contrato?",
        "¿Qué fecha se firmó el contrato?",
        "¿Hay alguna contradicción?",
        "¿Quién testificó en la audiencia?",
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(query_case, token, case_id, questions[i % len(questions)])
            for i in range(num_queries)
        ]
        for f in concurrent.futures.as_completed(futures):
            results["queries"].append(f.result())

    # Estadísticas
    upload_times = [u["elapsed"] for u in results["uploads"]]
    query_times = [q["elapsed"] for q in results["queries"]]
    upload_success = sum(1 for u in results["uploads"] if u["status_code"] == 200)
    query_success = sum(1 for q in results["queries"] if q["status_code"] == 200)

    results["stats"] = {
        "uploads": {
            "success_rate": upload_success / num_uploads if num_uploads > 0 else 0,
            "avg_time": sum(upload_times) / len(upload_times) if upload_times else 0,
            "p95_time": sorted(upload_times)[int(len(upload_times) * 0.95)] if upload_times else 0,
        },
        "queries": {
            "success_rate": query_success / num_queries if num_queries > 0 else 0,
            "avg_time": sum(query_times) / len(query_times) if query_times else 0,
            "p95_time": sorted(query_times)[int(len(query_times) * 0.95)] if query_times else 0,
        },
    }

    return results


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--case-id", required=True, type=UUID)
    p.add_argument("--org-id", required=True, type=UUID)
    p.add_argument("--user-id", required=True, type=UUID)
    p.add_argument("--uploads", type=int, default=10)
    p.add_argument("--queries", type=int, default=20)
    p.add_argument("--out", default="var/load_test.json")
    args = p.parse_args()

    result = run_load_test(args.case_id, args.org_id, args.user_id, args.uploads, args.queries)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Load test guardado en {out}")
    print(f"Uploads: {result['stats']['uploads']['success_rate']:.0%} éxito, {result['stats']['uploads']['avg_time']:.2f}s promedio")
    print(f"Queries: {result['stats']['queries']['success_rate']:.0%} éxito, {result['stats']['queries']['avg_time']:.2f}s promedio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
