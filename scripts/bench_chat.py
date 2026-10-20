#!/usr/bin/env python3
"""Benchmark reproducible del pipeline de chat (contrato §3.2, plan §3.4).

Mide por etapa con `FakeLLM` (aislado de la red):

- intent.classify
- retrieval_rank.apply (rerank + diversidad)
- compose.render
- answering.retrieve (solo si hay DB)
- total por pregunta (clasificar → [retrieve] → síntesis fake → componer)

Compara p50/p95 contra el presupuesto del contrato y sale con código 1 si
`dato` p95 > 8000 ms o `navegar` p95 > 800 ms.

Uso local (sin DB):
  python scripts/bench_chat.py --out var/bench_chat.json
Con DB:
  python scripts/bench_chat.py --org-id <uuid> --user-id <uuid> --case-id <uuid>
Contra API viva (login desde env EVAL_EMAIL/EVAL_PASSWORD):
  python scripts/bench_chat.py --http --base-url http://127.0.0.1:8000 --case-id <uuid>
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))

log = logging.getLogger("bench_chat")

BUDGETS_MS = {
    "classify": {"p50": 5, "p95": 20},
    "retrieve": {"p50": 250, "p95": 600},
    "locate": {"p50": 300, "p95": 800},
    "rerank": {"p50": 80, "p95": 250},
    "compose": {"p50": 15, "p95": 40},
    "dato_total": {"p50": 4000, "p95": 8000},
    "navegar_total": {"p50": 300, "p95": 800},
}


def questions() -> list[tuple[str, str]]:
    """40 preguntas sintéticas balanceadas por intención."""
    data = [
        ("dato", "¿Qué evidencia hay sobre pagos?"),
        ("dato", "¿Cuánto se pactó como pago del contrato?"),
        ("dato", "¿Quién firmó el contrato de suministro?"),
        ("dato", "¿Cuándo se presentó la demanda?"),
        ("dato", "¿Qué dice la contestación sobre el pago?"),
        ("dato", "¿Cuál es el número del proceso?"),
        ("dato", "¿Qué afirmó la testigo en la audiencia?"),
        ("dato", "¿Qué documento acredita la transferencia?"),
        ("dato", "¿Cuál es el NIT de la sociedad demandante?"),
        ("dato", "¿Qué fecha tiene el comprobante de pago?"),
        ("dato", "¿Qué hechos relata la demanda?"),
        ("dato", "¿Qué valor tiene el pagaré?"),
        ("navegar", "¿En qué minuto se habla de pagos?"),
        ("navegar", "¿Dónde aparece la palabra transferencia?"),
        ("navegar", "¿En qué página aparece el oficio 2834?"),
        ("navegar", "Localiza las menciones del pagaré."),
        ("navegar", "¿Dónde se menciona INVERFAST?"),
        ("navegar", "¿En qué minuto interviene la testigo?"),
        ("navegar", "¿En qué página está el comprobante?"),
        ("navegar", "¿Dónde aparece el término «pago»?"),
        ("analisis", "¿Existe contradicción sobre la fecha de firma?"),
        ("analisis", "¿Cómo se relacionan la demanda y la contestación?"),
        ("analisis", "¿Qué implica que el pago esté discutido?"),
        ("analisis", "Compara las versiones sobre la firma del contrato."),
        ("analisis", "¿Cuáles son las debilidades de la tesis del demandado?"),
        ("analisis", "¿Qué fortalezas tiene la posición de la demandante?"),
        ("analisis", "¿La afirmación de pago está respaldada por evidencia?"),
        ("analisis", "¿Qué consecuencia tiene la contradicción de fechas?"),
        ("procedimental", "¿Por qué se llegó a la audiencia inicial?"),
        ("procedimental", "¿Qué actuación originó la contestación?"),
        ("procedimental", "¿Cuál es la cadena procesal del expediente?"),
        ("procedimental", "¿Qué antecedentes llevaron al proceso verbal?"),
        ("procedimental", "¿Qué causó la contradicción sobre la firma?"),
        ("procedimental", "¿Cuál fue la secuencia de actuaciones?"),
        ("agregacion", "¿Quiénes son las partes del proceso?"),
        ("agregacion", "¿Cuántos documentos tiene el expediente?"),
        ("agregacion", "¿Quiénes son los testigos?"),
        ("agregacion", "¿Quiénes intervienen en la audiencia?"),
        ("agregacion", "¿Qué roles procesales aparecen?"),
        ("agregacion", "¿Cuántos jueces hay en el expediente?"),
    ]
    assert len(data) == 40
    return data


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p / 100
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _fake_evidence() -> list[dict]:
    return [
        {"handle": "E1", "source_type": "document_page", "document_id": "doc-1", "page_number": 1,
         "folio": 1, "filename": "01_demanda.pdf", "speaker": None, "start_ms": None,
         "text": "DEMANDA. Comercializadora Andina S.A.S. demanda a Juan Carlos Pérez. El demandado no realizó "
                 "el pago de $80.000.000 pactado para el 30 de abril de 2024."},
        {"handle": "E2", "source_type": "document_page", "document_id": "doc-2", "page_number": 1,
         "folio": 9, "filename": "02_contestacion.pdf", "speaker": None, "start_ms": None,
         "text": "CONTESTACIÓN. El demandado afirma haber realizado el pago de $80.000.000 el 28 de abril "
                 "de 2024 mediante transferencia bancaria."},
        {"handle": "E3", "source_type": "transcript_segment", "document_id": None, "page_number": None,
         "folio": None, "filename": "audiencia_2024-03-17.mp4", "media_id": "media-1", "segment_id": "seg-1",
         "speaker": "SPK-02", "start_ms": 4421000, "end_ms": 4468000,
         "text": "No recuerdo haber visto el pago; el contrato se firmó en mayo de 2024."},
    ]


class Bench:
    def __init__(self) -> None:
        self.stages: dict[str, list[float]] = {k: [] for k in
                                               ("classify", "retrieve", "locate", "rerank", "compose",
                                                "dato_total", "navegar_total")}

    def add(self, stage: str, ms: float) -> None:
        self.stages.setdefault(stage, []).append(ms)

    def summary(self) -> dict:
        out = {}
        for stage, values in self.stages.items():
            budget = BUDGETS_MS.get(stage)
            out[stage] = {
                "n": len(values),
                "p50_ms": _percentile(values, 50),
                "p95_ms": _percentile(values, 95),
                "budget_p50_ms": budget["p50"] if budget else None,
                "budget_p95_ms": budget["p95"] if budget else None,
                "within_budget": (bool(budget) and len(values) > 0
                                  and _percentile(values, 95) <= budget["p95"])
                if budget and values else None,
            }
        return out


def run_local(args) -> int:
    from app.core.config import get_settings
    from app.core.i18n import negotiate
    from app.providers.llm import FakeLLM
    from app.services import answering

    s = get_settings()
    locale = negotiate(None, "es")
    llm = FakeLLM(s.LLM_MODEL)

    pipeline = {}
    for name in ("intent", "retrieval_plan", "retrieval_rank", "compose", "occurrences", "text_terms"):
        try:
            pipeline[name] = __import__(f"app.services.{name}", fromlist=["*"])
        except ImportError:
            pipeline[name] = None

    bench = Bench()
    evidence_pool = _fake_evidence()
    conn_ctx = None
    if args.org_id and args.user_id and args.case_id:
        from app.core.db import tx

        conn_ctx = tx(args.org_id, args.user_id)

    conn = conn_ctx.__enter__() if conn_ctx else None
    try:
        for intent_name, question in questions():
            classify_ms = retrieve_ms = locate_ms = rerank_ms = synthesis_ms = compose_ms = 0.0
            term = None

            t0 = time.perf_counter()
            decision = None
            if pipeline["intent"] is not None:
                decision = pipeline["intent"].classify(question)
                intent_actual = getattr(decision.intent, "value", str(decision.intent))
            else:
                intent_actual = intent_name
            classify_ms = (time.perf_counter() - t0) * 1000
            bench.add("classify", classify_ms)

            items = list(evidence_pool)
            if intent_actual == "navegar" and conn is not None:
                # Producción: navegar = locate + ocurrencias (0 LLM, no retrieve()).
                from app.services import case_tools
                from app.services import text_terms
                from app.services.case_tools import ToolContext

                term = text_terms.specific_term(question) or question
                t0 = time.perf_counter()
                try:
                    locs = case_tools.execute(
                        conn, str(args.case_id), "locate", {"term": term, "k": 64},
                        ctx=ToolContext(org_id=str(args.org_id), actor_id=str(args.user_id), locale=locale))
                    if locs:
                        items = locs
                except Exception as exc:
                    log.debug("locate no ejecutado: %s", exc)
                locate_ms = (time.perf_counter() - t0) * 1000
                bench.add("locate", locate_ms)
            elif conn is not None:
                t0 = time.perf_counter()
                try:
                    items = answering.retrieve(conn, str(args.case_id), question,
                                               **({"intent": decision.intent} if decision is not None else {}))
                except TypeError:
                    items = answering.retrieve(conn, str(args.case_id), question)
                retrieve_ms = (time.perf_counter() - t0) * 1000
                bench.add("retrieve", retrieve_ms)
                if not items:
                    items = list(evidence_pool)

            if (intent_actual != "navegar" and pipeline["retrieval_rank"] is not None
                    and pipeline["retrieval_plan"] is not None and decision is not None):
                t0 = time.perf_counter()
                try:
                    plan = pipeline["retrieval_plan"].build(question, decision, settings=s)
                    items = pipeline["retrieval_rank"].apply(question, items, plan=plan, intent=decision.intent)
                except Exception as exc:  # el bench mide el pipeline, no la firma exacta de A
                    log.debug("rerank no ejecutado: %s", exc)
                rerank_ms = (time.perf_counter() - t0) * 1000
                bench.add("rerank", rerank_ms)
            for index, item in enumerate(items, 1):
                item["handle"] = f"E{index}"

            claims: list[dict] = []
            if intent_actual not in ("navegar", "agregacion") and items:
                t0 = time.perf_counter()
                system, _pid, _pv = answering.system_prompt(locale)
                llm_result = llm.complete(system, answering.build_user_prompt(question, items))
                claims = answering.parse_and_validate(llm_result.text, items, s.ANSWER_MIN_GROUNDING_OVERLAP)["claims"]
                synthesis_ms = (time.perf_counter() - t0) * 1000

            t0 = time.perf_counter()
            if intent_actual == "navegar" and pipeline.get("occurrences") is not None:
                pipeline["occurrences"].build(term or question, items, locale, max_items=s.NAV_MAX_OCCURRENCES)
            elif pipeline["compose"] is not None and decision is not None:
                pipeline["compose"].render(question, intent=decision.intent, claims=claims, evidence=items,
                                           uncertainties=[], locale=locale,
                                           max_citations=s.MAX_VISIBLE_CITATIONS,
                                           max_per_file=s.MAX_CITATIONS_PER_FILE)
            compose_ms = (time.perf_counter() - t0) * 1000
            bench.add("compose", compose_ms)
            bench.add(f"{intent_actual}_total",
                      classify_ms + retrieve_ms + locate_ms + rerank_ms + synthesis_ms + compose_ms)
    finally:
        if conn_ctx is not None:
            conn_ctx.__exit__(None, None, None)

    return _finish(bench, args, mode="local")


def _finish(bench: Bench, args, mode: str) -> int:
    summary = bench.summary()
    report: dict[str, Any] = {"mode": mode, "questions": 40, "stages": summary,
                              "budgets": BUDGETS_MS}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    for stage, data in summary.items():
        if not data["n"]:
            continue
        flag = "" if data["within_budget"] in (None, True) else "  ← FUERA DE PRESUPUESTO"
        print(f"{stage:>14}: n={data['n']:>2} p50={data['p50_ms']:.1f} ms p95={data['p95_ms']:.1f} ms"
              f" (budget p95 {data['budget_p95_ms']}){flag}")

    failures = []
    dato = summary.get("dato_total") or {}
    navegar = summary.get("navegar_total") or {}
    if dato.get("p95_ms") is not None and dato["p95_ms"] > 8000:
        failures.append(f"dato p95={dato['p95_ms']:.0f} ms > 8000 ms")
    if navegar.get("p95_ms") is not None and navegar["p95_ms"] > 800:
        failures.append(f"navegar p95={navegar['p95_ms']:.0f} ms > 800 ms")
    print(f"\nInforme: {out}")
    for f in failures:
        print(f"[FAIL] {f}")
    return 1 if failures else 0


def run_http(args) -> int:
    """Contra una API viva. Credenciales desde env (nunca por argumento)."""
    import httpx

    base = args.base_url.rstrip("/")
    email = os.environ.get("EVAL_EMAIL")
    password = os.environ.get("EVAL_PASSWORD")
    if not email or not password:
        print("[ERROR] --http requiere EVAL_EMAIL y EVAL_PASSWORD en el entorno")
        return 2
    headers: dict[str, str] = {}
    with httpx.Client(base_url=base, timeout=60) as client:
        try:
            login = client.post("/v1/auth/login", json={"email": email, "password": password})
            login.raise_for_status()
            headers["Authorization"] = f"Bearer {login.json()['access_token']}"
            session = client.post(f"/v1/cases/{args.case_id}/chats", headers=headers, json={})
            session.raise_for_status()
            session_id = session.json()["id"]
        except httpx.HTTPError as exc:
            print(f"[ERROR] no se pudo preparar la sesión HTTP: {type(exc).__name__}: {exc}")
            return 2

        bench = Bench()
        for intent_name, question in questions():
            started = time.perf_counter()
            response = client.post(f"/v1/cases/{args.case_id}/chats/{session_id}/messages",
                                   headers=headers, json={"content": question, "strategy": "auto"})
            elapsed = (time.perf_counter() - started) * 1000
            if response.status_code >= 400:
                print(f"[WARN] {response.status_code} en «{question[:40]}»")
                continue
            bench.add(f"{intent_name}_total", elapsed)

    return _finish(bench, args, mode="http")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Benchmark del pipeline de chat.")
    p.add_argument("--out", default="var/bench_chat.json")
    p.add_argument("--org-id", default=None)
    p.add_argument("--user-id", default=None)
    p.add_argument("--case-id", default=None)
    p.add_argument("--http", action="store_true", help="Mide la API viva en lugar del pipeline local")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = p.parse_args()
    if args.http:
        return run_http(args)
    if args.org_id and (not args.user_id or not args.case_id):
        print("[ERROR] para medir retrieval real se necesitan --org-id, --user-id y --case-id")
        return 2
    return run_local(args)


if __name__ == "__main__":
    raise SystemExit(main())
