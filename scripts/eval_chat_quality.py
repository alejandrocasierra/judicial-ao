#!/usr/bin/env python3
"""Banco de pruebas del chat: genera preguntas desde el expediente y mide calidad.

No usa un juez LLM (sería caro y circular): mide señales duras y verificables:
- ¿respondió o se abstuvo?
- ¿citó fuentes?
- ¿la respuesta tiene cuerpo suficiente para una pregunta exhaustiva?
- ¿se degradó por deadline?
- latencia y longitud.

Las preguntas se generan de datos que EXISTEN en el expediente (entidades,
documentos, contradicciones y temas), así que una abstención es una señal real
de fallo de recuperación o de prompt, no una pregunta imposible.

Uso:
    python scripts/eval_chat_quality.py --api http://127.0.0.1:8002/v1 --limit 30
    python scripts/eval_chat_quality.py --types exhaustive,contradiction --limit 10
    python scripts/eval_chat_quality.py --model-id <uuid> --limit 200 --out var/chat_eval/run.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))

TOPICS = [
    "el pago de intereses",
    "el pagaré 77391469",
    "el interrogatorio de Jorge Rojas",
    "la capitalización de intereses",
    "la audiencia",
    "el contrato",
    "la demanda",
    "la contestación",
]


def _load_env() -> None:
    import envload

    envload.load(os.environ.get("ENV_FILE") or str(_ROOT / ".env"))


def _db_questions(case_number: str, per_type: int) -> list[dict]:
    from app.core.db import rows, tx

    case = None
    with tx(None) as c:
        orgs = [str(r["id"]) for r in rows(c, "SELECT id FROM ops_list_organizations()")]
    for org in orgs:
        with tx(org) as c:
            found = rows(c, "SELECT id, organization_id FROM cases WHERE case_number = :n",
                         n=case_number)
        if found:
            case = found[0]
            break
    if not case:
        raise SystemExit(f"[ERROR] no existe el expediente {case_number}")
    case_id, org_id = str(case["id"]), str(case["organization_id"])

    out: list[dict] = []
    with tx(org_id) as c:
        entities = rows(c, """SELECT name FROM entities
                               WHERE case_id = :c
                                 AND entity_type IN ('person', 'organization', 'contract')
                                 AND name ~ '[A-Za-zÁÉÍÓÚÑáéíóúñ]'
                                 AND length(name) BETWEEN 5 AND 60
                               ORDER BY random() LIMIT :n""", c=case_id, n=per_type)
        documents = rows(c, """SELECT filename FROM documents
                                WHERE case_id = :c AND length(filename) > 8
                                ORDER BY random() LIMIT :n""", c=case_id, n=per_type)
        contradictions = rows(c, """SELECT description FROM contradictions
                                     WHERE case_id = :c
                                     ORDER BY random() LIMIT :n""", c=case_id, n=per_type)
    for item in entities:
        out.append({"type": "entity", "question": f"¿Qué se dice en el expediente sobre {item['name']}?"})
    for item in documents:
        out.append({"type": "document", "question":
                    f"¿Qué contiene y qué afirma el documento {item['filename']}?"})
    for item in contradictions:
        desc = " ".join(str(item["description"]).split())[:120]
        out.append({"type": "contradiction", "question":
                    f"Explique esta contradicción del expediente y las dos versiones que la sustentan: {desc}"})
    for topic in TOPICS:
        out.append({"type": "topic", "question": f"¿Qué dice el expediente sobre {topic}?"})
    out.append({"type": "exhaustive", "question":
                "haga una busqueda exhaustiva sobre todas las contradicciones de la parte "
                "demandante sobre el pago de intereses?"})
    random.shuffle(out)
    return out


def _login(api: str) -> dict:
    import requests

    candidates = [
        ("admin@judicial.ai", os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")),
        (f"gestor.alfa@{os.environ.get('SEED_EMAIL_DOMAIN', 'example.test')}",
         os.environ.get("SEED_DEFAULT_PASSWORD", "")),
    ]
    for email, password in candidates:
        if not password:
            continue
        r = requests.post(f"{api}/auth/login", json={"email": email, "password": password}, timeout=30)
        if r.ok:
            return {"Authorization": f"Bearer {r.json()['access_token']}"}
    raise SystemExit("[ERROR] sin credenciales: define BOOTSTRAP_ADMIN_PASSWORD o SEED_DEFAULT_PASSWORD")


def _case_id(api: str, headers: dict, case_number: str) -> str:
    import requests

    r = requests.get(f"{api}/cases", headers=headers, timeout=30)
    r.raise_for_status()
    cases = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    for case in cases:
        if str(case.get("case_number")) == case_number:
            return str(case["id"])
    raise SystemExit(f"[ERROR] la API no expone el expediente {case_number}")


def _run_one(api: str, headers: dict, case_id: str, model_id: str | None,
             question: str, timeout: int = 240) -> dict:
    import requests

    payload: dict = {"content": question}
    if model_id:
        payload["model_id"] = model_id
    r = requests.post(f"{api}/cases/{case_id}/chats", headers=headers,
                      json={"model_id": model_id, "title": question[:80]}, timeout=30)
    if not r.ok:
        return {"error": f"create_session:{r.status_code}", "question": question}
    session = r.json()["id"]
    started = time.monotonic()
    r = requests.post(f"{api}/cases/{case_id}/chats/{session}/messages",
                      headers=headers, json=payload, timeout=timeout)
    elapsed = time.monotonic() - started
    if not r.ok:
        return {"error": f"send:{r.status_code}", "question": question, "elapsed": elapsed}
    body = r.json()
    answer = str(body.get("answer") or "")
    return {
        "question": question,
        "session": session,
        "elapsed": round(elapsed, 2),
        "answer_kind": body.get("answer_kind"),
        "intent": body.get("intent"),
        "degraded": bool(body.get("degraded")),
        "citations": len(body.get("citations") or []),
        "claims": len(body.get("claims") or []),
        "evidence": body.get("evidence_count"),
        "length": len(answer),
        "refusal": "no se encontr" in answer.lower() or "no hay evidencia" in answer.lower(),
        "answer": answer[:1500],
    }


def _summary(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r.get("error")]
    answerable = [r for r in ok if r.get("answer_kind") != "insufficient"]
    latencies = sorted(r["elapsed"] for r in ok if r.get("elapsed"))
    def pct(p: float) -> float:
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))] if latencies else 0.0
    by_type: dict[str, dict] = {}
    for row in ok:
        key = row.get("type", "?")
        bucket = by_type.setdefault(key, {"n": 0, "ok": 0, "citations": 0, "refusal": 0, "degraded": 0})
        bucket["n"] += 1
        bucket["ok"] += int(row.get("answer_kind") != "insufficient")
        bucket["citations"] += int((row.get("citations") or 0) > 0)
        bucket["refusal"] += int(bool(row.get("refusal")))
        bucket["degraded"] += int(bool(row.get("degraded")))
    return {
        "n": len(ok),
        "errors": len(rows) - len(ok),
        "answered_rate": round(len(answerable) / len(ok), 3) if ok else 0.0,
        "citation_rate": round(sum(1 for r in ok if (r.get("citations") or 0) > 0) / len(ok), 3) if ok else 0.0,
        "refusal_rate": round(sum(1 for r in ok if r.get("refusal")) / len(ok), 3) if ok else 0.0,
        "degraded_rate": round(sum(1 for r in ok if r.get("degraded")) / len(ok), 3) if ok else 0.0,
        "avg_length": round(statistics.mean(r["length"] for r in ok), 0) if ok else 0,
        "p50_s": pct(0.5), "p95_s": pct(0.95),
        "by_type": by_type,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Banco de pruebas de calidad del chat.")
    p.add_argument("--api", default="http://127.0.0.1:8002/v1")
    p.add_argument("--case-number", default="11001310302120180036100")
    p.add_argument("--model-id", default=None)
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--per-type", type=int, default=6,
                   help="Preguntas por entidad/documento/contradicción (default 6)")
    p.add_argument("--types", default=None, help="Filtro: entity,document,contradiction,topic,exhaustive")
    p.add_argument("--out", default="var/chat_eval/run.jsonl")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    _load_env()
    random.seed(13)
    import requests  # noqa: F401 — el login lo usa

    bank = _db_questions(args.case_number, args.per_type)
    if args.types:
        allowed = {x.strip() for x in args.types.split(",")}
        bank = [q for q in bank if q["type"] in allowed]
    bank = bank[: args.limit]

    headers = _login(args.api)
    case_id = _case_id(args.api, headers, args.case_number)
    print(f"casos en el banco: {len(bank)} · expediente: {case_id}")

    out = Path(args.out)
    if not out.is_absolute():
        out = _ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    with out.open("w", encoding="utf-8") as fh:
        for index, item in enumerate(bank, start=1):
            row = _run_one(args.api, headers, case_id, args.model_id, item["question"])
            row["type"] = item["type"]
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            mark = "ERR" if row.get("error") else (
                f"{row.get('answer_kind'):11} cit={row.get('citations')} "
                f"{row.get('elapsed'):6.1f}s len={row.get('length')}")
            print(f"[{index:3}/{len(bank)}] {item['type']:14} {mark}")
            print(f"    {item['question'][:150]}")
            if row.get("error"):
                print(f"    -> {row['error']}")

    summary = _summary(rows)
    (out.with_suffix(".summary.json")).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n== Resumen ==")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
