#!/usr/bin/env python3
"""Prueba de carga del chat (plan §3.4): N workers concurrentes contra
`POST /v1/cases/{id}/chats/{sid}/messages`.

Mide p50/p95/p99, throughput, respuestas 429 y errores. Nunca imprime tokens ni
credenciales.

Uso:
  python scripts/load_chat.py --case-id <uuid> --requests 200 --workers 10
  python scripts/load_chat.py --case-id <uuid> --duration 300 --workers 10
  EVAL_TOKEN=... python scripts/load_chat.py --case-id <uuid> --session-id <uuid>

Credenciales (si no hay EVAL_TOKEN): EVAL_EMAIL / EVAL_PASSWORD.
Sale con código 1 si hay errores 5xx o si p95 supera --p95-budget-ms.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import httpx

QUESTIONS = [
    "¿Qué evidencia hay sobre pagos?",
    "¿Quién firmó el contrato?",
    "¿En qué minuto se habla de pagos?",
    "¿Existe contradicción sobre la fecha de firma?",
    "¿Quiénes son las partes del proceso?",
    "¿Qué dice la contestación sobre el pago?",
    "¿Cuál es la cadena procesal del expediente?",
    "¿Cuáles son las debilidades de la tesis del demandado?",
    "¿En qué página aparece el comprobante?",
    "¿Qué documentos conforman el expediente?",
]


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p / 100
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _login(client: httpx.Client) -> str:
    token = os.environ.get("EVAL_TOKEN")
    if token:
        return token
    email = os.environ.get("EVAL_EMAIL")
    password = os.environ.get("EVAL_PASSWORD")
    if not email or not password:
        raise SystemExit("faltan EVAL_TOKEN o EVAL_EMAIL/EVAL_PASSWORD en el entorno")
    r = client.post("/v1/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Carga concurrente del chat.")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--case-id", required=True)
    p.add_argument("--session-id", default=None)
    p.add_argument("--workers", type=int, default=10)
    p.add_argument("--requests", type=int, default=0, help="Total de peticiones (0 = usar --duration)")
    p.add_argument("--duration", type=int, default=300, help="Segundos de la prueba")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--p95-budget-ms", type=float, default=8000.0)
    p.add_argument("--out", default="var/load_chat.json")
    args = p.parse_args()

    latencies: list[float] = []
    statuses: Counter = Counter()
    errors: Counter = Counter()
    lock = threading.Lock()
    deadline = time.monotonic() + args.duration
    issued = 0
    issued_lock = threading.Lock()

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
        try:
            token = _login(client)
            headers = {"Authorization": f"Bearer {token}"}
            session_id = args.session_id
            if not session_id:
                r = client.post(f"/v1/cases/{args.case_id}/chats", headers=headers, json={})
                r.raise_for_status()
                session_id = r.json()["id"]
        except SystemExit as exc:
            print(f"[ERROR] {exc}")
            return 2
        except httpx.HTTPError as exc:
            print(f"[ERROR] no se pudo preparar la sesión: {type(exc).__name__}: {exc}")
            return 2

        def worker(worker_id: int) -> None:
            nonlocal issued
            with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as wc:
                rng = random.Random(worker_id)
                while time.monotonic() < deadline:
                    with issued_lock:
                        if args.requests and issued >= args.requests:
                            return
                        issued += 1
                    question = rng.choice(QUESTIONS)
                    started = time.perf_counter()
                    try:
                        r = wc.post(f"/v1/cases/{args.case_id}/chats/{session_id}/messages",
                                    headers=headers,
                                    json={"content": question, "strategy": "auto"})
                        elapsed = (time.perf_counter() - started) * 1000
                        with lock:
                            latencies.append(elapsed)
                            statuses[r.status_code] += 1
                            if r.status_code >= 400:
                                errors[f"http_{r.status_code}"] += 1
                    except Exception as exc:
                        elapsed = (time.perf_counter() - started) * 1000
                        with lock:
                            latencies.append(elapsed)
                            statuses["exception"] += 1
                            errors[type(exc).__name__] += 1

        started_at = time.monotonic()
        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(max(1, args.workers))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = max(0.001, time.monotonic() - started_at)

    total = sum(statuses.values())
    report = {
        "base_url": args.base_url,
        "case_id": args.case_id,
        "workers": args.workers,
        "requests": total,
        "wall_seconds": round(wall, 2),
        "throughput_rps": round(total / wall, 2),
        "status_counts": dict(statuses),
        "errors": dict(errors),
        "rate_limited_429": statuses.get(429, 0),
        "latency_ms": {
            "n": len(latencies),
            "mean": round(statistics.fmean(latencies), 2) if latencies else None,
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "p99": _percentile(latencies, 99),
            "max": max(latencies) if latencies else None,
        },
        "p95_budget_ms": args.p95_budget_ms,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lat = report["latency_ms"]
    print(f"peticiones={total} workers={args.workers} rps={report['throughput_rps']}")
    print(f"p50={lat['p50']} ms p95={lat['p95']} ms p99={lat['p99']} ms")
    print(f"429={report['rate_limited_429']} errores={report['errors']}")
    print(f"Informe: {out}")

    server_errors = sum(v for k, v in statuses.items() if isinstance(k, int) and k >= 500)
    if server_errors or lat["p95"] is None or lat["p95"] > args.p95_budget_ms:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
