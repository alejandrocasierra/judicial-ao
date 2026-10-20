#!/usr/bin/env python3
"""Matriz cross-model del pipeline de chat + juez semántico portable.

Para cada modelo ejecuta el mismo set dorado con el pipeline real
(`answering.retrieve()` + `compose.render()`) y calcula:

- capa A: coherencia determinista y `answer_kind`;
- capa B: retrieval/citation recall, citation_precision, noise_ratio;
- capa C: juez semántico con rúbrica JSON, 3 muestras y mayoría.

Adaptadores:
- Si se pasan `--org-id`/`--user-id` y el modelo existe en `ai_models`, se usa
  `get_llm_for_model` (misma ruta que producción).
- Si no, se construye con `OpenAiLLM`/`AnthropicLLM` y las variables de entorno
  del proveedor. Las claves NUNCA se imprimen ni se escriben en el informe.
- `--offline` usa `FakeLLM` (o cassettes con `--replay-dir`) y lo declara.

Gates (plan §2.6):
  coherence_pass == 1.0 · citation_precision >= 0.85 · faithfulness >= 0.90
  abstention_accuracy >= 0.90 · p95(dato) <= budget

Uso:
  python scripts/eval_matrix.py --org-id <uuid> --user-id <uuid> --case-id <uuid> \\
      --models gemini:gemini-2.5-flash,deepseek:deepseek-chat,openai:gpt-4o-mini \\
      --judge openai:gpt-4o-mini --golden evals/golden/chat_pipeline.yaml --out var/eval_matrix.json
  python scripts/eval_matrix.py ... --offline --out var/eval_matrix_offline.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))

try:  # ejecutado como script (scripts/ en sys.path) o importado como paquete
    import run_evals as rv  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - ruta de paquete
    from scripts import run_evals as rv  # type: ignore[no-redef]

RUBRIC_FIELDS = ("faithfulness", "citation_correctness", "usefulness", "naturalness", "abstention")
JUDGE_SYSTEM = (
    "Eres un evaluador independiente de respuestas de un asistente jurídico. "
    "Evalúa SOLO con la evidencia aportada; no uses conocimiento externo. "
    "Devuelve exclusivamente un objeto JSON válido, sin markdown ni comentarios, con las claves: "
    "faithfulness, citation_correctness, usefulness, naturalness, abstention (enteros 1-5), "
    "verdict (pass|fail|borderline) y reasons (lista de strings cortos).\n"
    "Criterios: faithfulness = cada afirmación se sigue de la evidencia citada (5) o inventa (1); "
    "citation_correctness = las citas respaldan lo afirmado y no hay citas decorativas; "
    "usefulness = responde a la pregunta sin rodeos; naturalness = se lee como prosa jurídica clara; "
    "abstention = 5 si se abstiene cuando no hay evidencia o responde cuando la hay, 1 si hace lo contrario."
)

PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "kimi": "KIMI_API_KEY",
}


# ---------------------------------------------------------------------------
# Juez
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict | None:
    """Extrae el primer objeto JSON de una respuesta (tolera fences y prosa)."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1] if cleaned.count("```") >= 2 else cleaned
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    start = cleaned.find("{")
    while start != -1:
        depth = 0
        for i, ch in enumerate(cleaned[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(cleaned[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        return value
        start = cleaned.find("{", start + 1)
    return None


def _valid_rubric(data: dict | None) -> bool:
    if not isinstance(data, dict):
        return False
    for field in RUBRIC_FIELDS:
        value = data.get(field)
        if not isinstance(value, (int, float)) or not (1 <= int(value) <= 5):
            return False
    return data.get("verdict") in {"pass", "fail", "borderline", "pass_with_notes"}


def _normalize_verdict(value: Any) -> str:
    if value in {"pass", "fail", "borderline"}:
        return value
    return "borderline"


def _majority(values: list) -> Any:
    if not values:
        return None
    numbers = [v for v in values if isinstance(v, (int, float))]
    if len(numbers) == len(values):
        counts = Counter(int(v) for v in numbers)
        top = max(counts.values())
        winners = sorted(k for k, c in counts.items() if c == top)
        return winners[0] if len(winners) == 1 else int(round(statistics.median(numbers)))
    counts = Counter(values)
    top = max(counts.values())
    winners = sorted(str(k) for k, c in counts.items() if c == top)
    return winners[0]


def _build_judge_prompt(claim: str, evidence: str, question: str | None = None) -> str:
    parts = []
    if question:
        parts.append(f"<pregunta>\n{question}\n</pregunta>")
    parts.append(f"<respuesta>\n{claim}\n</respuesta>")
    parts.append(f"<evidencia>\n{evidence}\n</evidencia>")
    return "\n\n".join(parts)


def judge(claim: str, evidence: str, llm: Any = None, *, samples: int = 3,
          question: str | None = None, generator: str | None = None) -> dict:
    """Evalúa una afirmación contra su evidencia con rúbrica cerrada.

    - 3 muestras y mayoría por campo (los numéricos usan moda; empate → mediana).
    - `judge_error=True` si menos de 2 muestras válidas: no se promedia.
    - `same_family=True` si el juez es el mismo proveedor/modelo que el generador.
    - `llm=None` usa un juez heurístico determinista (modo --offline).
    """
    prompt = _build_judge_prompt(claim, evidence, question)
    meta = {"samples": samples, "judge_error": False, "same_family": False, "errors": []}

    if llm is None:
        return {**_heuristic_judge(claim, evidence, question), **meta, "judge_mode": "heuristic"}

    judge_provider = getattr(llm, "name", "")
    judge_model = getattr(llm, "model", "")
    if generator:
        gen = generator.lower()
        meta["same_family"] = gen in {judge_provider.lower(), judge_model.lower()} or \
            (judge_provider and gen.startswith(judge_provider.lower()))

    valid: list[dict] = []
    for _ in range(max(1, samples)):
        try:
            result = llm.complete(JUDGE_SYSTEM, prompt)
            parsed = _extract_json(getattr(result, "text", "") or "")
        except Exception as exc:  # el juez nunca tumba la matriz
            meta["errors"].append(f"{type(exc).__name__}: {exc}")
            continue
        if _valid_rubric(parsed):
            valid.append(parsed)  # type: ignore[arg-type]
        else:
            meta["errors"].append("json_invalido")

    if len(valid) < 2:
        meta["judge_error"] = True
        return {field: None for field in RUBRIC_FIELDS} | {"verdict": None, "reasons": ["judge_error"],
                                                          **meta, "judge_mode": "llm"}

    out: dict = {field: _majority([int(v[field]) for v in valid]) for field in RUBRIC_FIELDS}
    out["verdict"] = _normalize_verdict(_majority([v.get("verdict") for v in valid]))
    reasons: list[str] = []
    for v in valid:
        for r in v.get("reasons") or []:
            if isinstance(r, str) and r not in reasons:
                reasons.append(r)
    out["reasons"] = reasons[:6]
    out.update(meta)
    out["judge_mode"] = "llm"
    out["agreement"] = round(max(Counter(v.get("verdict") for v in valid).values()) / len(valid), 2)
    return out


def judge_panel(claim: str, evidence: str, llms: list[Any], *, samples: int = 3,
                question: str | None = None, generator: str | None = None) -> dict:
    """Panel de jueces: cada uno juzga; se agrega por campo y veredicto.

    Un juez con `judge_error` no promedia; si todos fallan, el resultado queda
    marcado como error (nunca se inventa un veredicto).
    """
    if not llms:
        return judge(claim, evidence, None, samples=samples, question=question, generator=generator)
    results = [judge(claim, evidence, llm, samples=samples, question=question, generator=generator)
               for llm in llms]
    valid = [r for r in results if not r.get("judge_error")]
    if not valid:
        return {**results[0], "judge_error": True, "panel_size": 0}
    out: dict = {}
    for field in RUBRIC_FIELDS:
        values = [r.get(field) for r in valid if r.get(field) is not None]
        out[field] = _majority(values) if values else None
    out["verdict"] = _majority([r.get("verdict") for r in valid if r.get("verdict")])
    reasons: list[str] = []
    for r in valid:
        for reason in r.get("reasons") or []:
            if isinstance(reason, str) and reason not in reasons:
                reasons.append(reason)
    out["reasons"] = reasons[:8]
    out["judge_mode"] = "panel"
    out["panel_size"] = len(valid)
    out["same_family"] = all(bool(r.get("same_family")) for r in valid)
    out["judge_error"] = False
    out["agreement"] = round(len(valid) / len(results), 2)
    return out


def _heuristic_judge(claim: str, evidence: str, question: str | None = None) -> dict:
    """Juez offline: determinista y explícitamente NO semántico."""
    try:
        from app.services.text_terms import coverage, missing_numbers
    except ImportError:  # pragma: no cover
        coverage, missing_numbers = (lambda a, b: 1.0), (lambda a, b: [])
    answer = claim or ""
    normalized = answer.lower()
    abstention_markers = ("no se encontró evidencia", "no encontre evidencia", "no encontré evidencia",
                          "no hay evidencia", "sin evidencia en el expediente")
    abstains = any(marker in normalized for marker in abstention_markers)
    if abstains and not (evidence or "").strip():
        return {"faithfulness": 5, "citation_correctness": 5, "usefulness": 4, "naturalness": 4,
                "abstention": 5, "verdict": "pass", "reasons": ["abstención con evidencia vacía"]}
    # Encabezados de sección y la muletilla de `partial` no son afirmaciones.
    core_lines = [line for line in answer.splitlines()
                  if line.strip() and not line.lstrip().startswith("**")
                  and not line.lstrip().startswith("Encontré material")]
    core = " ".join(core_lines) or answer
    cov = coverage(core, evidence or "")
    missing = missing_numbers(core, evidence or "")
    if not answer.strip():
        faithfulness = 1
    elif missing:
        faithfulness = 2
    elif cov >= 0.6:
        faithfulness = 5
    elif cov >= 0.35:
        faithfulness = 4
    else:
        faithfulness = 3
    verdict = "pass" if faithfulness >= 3 and not missing else "fail"
    reasons = []
    if missing:
        reasons.append("cifras ausentes en la evidencia: " + ", ".join(missing[:4]))
    reasons.append(f"cobertura léxica {cov:.2f}")
    return {"faithfulness": faithfulness, "citation_correctness": 4 if not missing else 2,
            "usefulness": 3, "naturalness": 4, "abstention": 4,
            "verdict": verdict, "reasons": reasons}


# ---------------------------------------------------------------------------
# Adaptadores
# ---------------------------------------------------------------------------

def parse_model_spec(spec: str) -> tuple[str, str]:
    provider, _, model = spec.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if not provider or not model:
        raise SystemExit(f"modelo inválido: {spec!r} (formato proveedor:modelo)")
    return provider, model


def _from_db(provider: str, model: str, org_id: str, user_id: str) -> Any | None:
    """Adaptador desde `ai_models` (misma ruta que producción)."""
    try:
        from app.core.db import one, tx
        from app.providers.llm import get_llm_for_model

        with tx(org_id, user_id) as conn:
            row = one(conn, "SELECT id FROM ai_models WHERE provider = :p AND model_name = :m LIMIT 1",
                      p=provider, m=model)
        if row:
            return get_llm_for_model(str(row["id"]), org_id, user_id)
    except Exception:
        return None
    return None


def build_adapter(spec: str, *, offline: bool, org_id: str | None, user_id: str | None,
                  replay_dir: str | None = None, record_dir: str | None = None) -> tuple[Any, dict]:
    """Devuelve (proveedor, metadata sin secretos)."""
    provider, model = parse_model_spec(spec)
    info = {"provider": provider, "model": model, "source": "env", "has_key": False}

    if offline:
        from app.providers.llm import FakeLLM
        info["source"] = "fake"
        return _maybe_cassette(FakeLLM(f"{provider}:{model}"), replay_dir, record_dir, info), info

    if org_id and user_id:
        adapter = _from_db(provider, model, org_id, user_id)
        if adapter is not None:
            info["source"] = "ai_models"
            return _maybe_cassette(adapter, replay_dir, record_dir, info), info

    from app.core.config import get_settings
    from app.providers.llm import AnthropicLLM, OpenAiLLM

    s = get_settings()
    key = os.environ.get(PROVIDER_KEY_ENV.get(provider, "")) or s.LLM_API_KEY
    info["has_key"] = bool(key)
    if not key:
        raise SystemExit(
            f"falta {PROVIDER_KEY_ENV.get(provider, 'LLM_API_KEY')} para {provider}:{model}. "
            "Usa --offline o --replay-dir para correr sin claves."
        )
    if provider == "anthropic":
        adapter = AnthropicLLM(model=model, api_key=key, api_base_url=s.LLM_API_BASE_URL,
                               api_version=s.LLM_API_VERSION)
    else:
        base = {"openai": s.OPENAI_BASE_URL, "gemini": s.GEMINI_BASE_URL,
                "deepseek": s.DEEPSEEK_BASE_URL, "kimi": s.KIMI_BASE_URL}.get(provider, s.LLM_API_BASE_URL)
        adapter = OpenAiLLM(model=model, api_key=key, api_base_url=base)
    return _maybe_cassette(adapter, replay_dir, record_dir, info), info


def _maybe_cassette(adapter: Any, replay_dir: str | None, record_dir: str | None, info: dict) -> Any:
    from app.providers import recording

    if replay_dir:
        info["source"] = "replay"
        return recording.wrap(adapter, mode="replay", path=replay_dir, on_miss="fallback")
    if record_dir:
        info["source"] = "record"
        return recording.wrap(adapter, mode="record", path=record_dir)
    return adapter


# ---------------------------------------------------------------------------
# Evaluación por modelo
# ---------------------------------------------------------------------------

def _evidence_text(result: dict, limit: int = 7000) -> str:
    parts = []
    for it in result.get("used_citations") or result.get("evidence") or []:
        source = f"{it.get('filename') or ''}"
        if it.get("page_number"):
            source += f" p.{it['page_number']}"
        if it.get("start_ms") is not None:
            source += f" {int(it['start_ms']) // 60000}:{(int(it['start_ms']) // 1000) % 60:02d}"
        parts.append(f"[{source}] {it.get('text') or ''}")
    text = "\n".join(parts)
    return text[:limit]


def run_model(spec: str, cases: list[dict], args, judge_llms: list[Any]) -> dict:
    llm, info = build_adapter(spec, offline=args.offline, org_id=str(args.org_id) if args.org_id else None,
                              user_id=str(args.user_id) if args.user_id else None,
                              replay_dir=args.replay_dir, record_dir=args.record_dir)
    from app.core.i18n import negotiate

    locale = negotiate(None, args.locale)
    pipeline = rv._pipeline()
    records: list[dict] = []
    skipped: list[dict] = []

    for case in cases:
        started = time.perf_counter()
        try:
            with rv.tx(args.org_id, args.user_id) as conn:
                case_id = rv._resolve_case_id(conn, case, str(args.case_id) if args.case_id else None)
                try:
                    result = rv._run_rag(conn, case_id, case["question"], locale, llm, pipeline,
                                         False, str(args.org_id), str(args.user_id), args.ocr_mode, args.asr_mode)
                except Exception as exc:
                    result = {"answer": "", "answer_kind": None, "claims": [], "evidence": [],
                              "evidence_count": 0, "used_handles": [], "used_citations": [],
                              "occurrences": [], "uncertainties": [], "unsupported_claims": [],
                              "intent": case.get("intent"), "error": f"{type(exc).__name__}: {exc}"}
        except KeyError as exc:
            skipped.append({"id": case.get("id"), "reason": str(exc)})
            continue
        latency = (time.perf_counter() - started) * 1000
        checks = rv._evaluate(case, result)

        judged = None
        if not args.skip_judge and result.get("answer"):
            judged = judge_panel(result["answer"], _evidence_text(result), judge_llms,
                                 samples=args.judge_samples, question=case["question"],
                                 generator=info.get("provider"))
        records.append({"case": case, "result": result, "checks": checks, "judge": judged,
                        "latency_ms": latency})

    metrics, reasons = rv._aggregate(records)
    judge_rows = [r["judge"] for r in records if r["judge"]]
    valid = [j for j in judge_rows if not j.get("judge_error")]
    for field in RUBRIC_FIELDS:
        values = [j[field] for j in valid if j.get(field) is not None]
        metrics[field] = (sum(values) / len(values) / 5.0) if values else None
        if not values:
            reasons[field] = "judge_error o sin casos juzgables"
    metrics["judge_errors"] = sum(1 for j in judge_rows if j.get("judge_error"))
    metrics["judged_cases"] = len(judge_rows)
    metrics["same_family_cases"] = sum(1 for j in judge_rows if j.get("same_family"))
    verdicts = Counter(j.get("verdict") for j in valid)
    metrics["judge_pass_rate"] = (verdicts.get("pass", 0) / len(valid)) if valid else None
    if valid == []:
        reasons["judge_pass_rate"] = "sin muestras válidas del juez"

    dato_latencies = [r["latency_ms"] for r in records if (r["case"].get("intent") == "dato")]
    dato_budget = max((int(r["case"].get("latency_budget_ms") or 8000) for r in records
                       if r["case"].get("intent") == "dato"), default=8000)
    metrics["dato_p95_ms"] = rv.percentile(dato_latencies, 95)
    metrics["dato_budget_ms"] = dato_budget

    gates = {
        "coherence_pass": metrics.get("coherence_pass") == 1.0,
        "citation_precision": (metrics.get("citation_precision") or 0.0) >= 0.85,
        "faithfulness": (metrics.get("faithfulness") or 0.0) >= 0.90,
        "abstention_accuracy": (metrics.get("abstention_accuracy") or 0.0) >= 0.90,
        "dato_p95": (metrics.get("dato_p95_ms") or 0.0) <= dato_budget,
    }
    return {
        "model": f"{info['provider']}:{info['model']}",
        "adapter": info,
        "metrics": metrics,
        "metric_reasons": reasons,
        "skipped": skipped,
        "gates": gates,
        "gate_passed": all(gates.values()),
        "judge": {"mode": "heuristic" if args.offline else "llm",
                  "model": args.judge, "samples": args.judge_samples,
                  "errors": metrics["judge_errors"],
                  "same_family_cases": metrics["same_family_cases"]},
        "cases": [
            {"id": r["case"]["id"], "intent": r["result"].get("intent"),
             "answer_kind": r["result"].get("answer_kind"),
             "expected_answer_kind": r["case"].get("expected_answer_kind"),
             "pass": r["checks"].get("pass"), "checks": r["checks"],
             "judge": r["judge"], "latency_ms": round(r["latency_ms"], 2)}
            for r in records
        ],
    }


def _markdown(report: dict) -> str:
    lines = [
        "# Matriz cross-model — pipeline de chat",
        "",
        f"- Set dorado: `{report['golden']}` · casos: {report['cases']}",
        f"- Modo: **{report['mode']}** · juez: `{report['judge_model']}` ({report['judge_mode']})",
        "",
        "| Modelo | pass | coherence | cit_precision | faithfulness | abstention | p95 dato | gates |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in report["models"]:
        m = row["metrics"]
        gates = "✅" if row["gate_passed"] else "❌ " + ",".join(k for k, v in row["gates"].items() if not v)
        lines.append(
            f"| {row['model']} | {_pct(m.get('pass_rate'))} | {_pct(m.get('coherence_pass'))} | "
            f"{_num(m.get('citation_precision'))} | {_num(m.get('faithfulness'))} | "
            f"{_pct(m.get('abstention_accuracy'))} | {_num(m.get('dato_p95_ms'))} ms | {gates} |"
        )
    if report.get("notes"):
        lines += ["", "## Notas", ""] + [f"- {n}" for n in report["notes"]]
    return "\n".join(lines) + "\n"


def _pct(v) -> str:
    return "n/a" if v is None else f"{v:.1%}"


def _num(v) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Matriz cross-model con juez semántico portable.")
    p.add_argument("--models", required=True, help="gemini:MODEL,deepseek:MODEL,...")
    p.add_argument("--judge", default=None,
                   help="proveedor:modelo[,proveedor:modelo...] del panel de jueces (distinto al generador)")
    p.add_argument("--judge-samples", type=int, default=3)
    p.add_argument("--skip-judge", action="store_true")
    p.add_argument("--golden", default="evals/golden/chat_pipeline.yaml")
    p.add_argument("--out", default="var/eval_matrix.json")
    p.add_argument("--org-id", type=UUID, default=None)
    p.add_argument("--user-id", type=UUID, default=None)
    p.add_argument("--case-id", type=UUID, default=None)
    p.add_argument("--locale", default="es")
    p.add_argument("--offline", action="store_true",
                   help="Sin claves: FakeLLM + juez heurístico determinista")
    p.add_argument("--replay-dir", default=None)
    p.add_argument("--record-dir", default=None)
    p.add_argument("--ocr-mode", default=None)
    p.add_argument("--asr-mode", default=None)
    args = p.parse_args()

    golden = Path(args.golden)
    cases = rv._load_cases(golden if golden.is_file() else None,
                           golden if golden.is_dir() else golden.parent)
    if not cases:
        print("[ERROR] el set dorado está vacío")
        return 2
    if not args.offline and (args.org_id is None or args.user_id is None):
        print("[ERROR] la matriz con proveedores reales requiere --org-id y --user-id (o usa --offline)")
        return 2

    judge_llms: list[Any] = []
    judge_mode = "heuristic"
    if not args.offline and args.judge and not args.skip_judge:
        for spec in [s.strip() for s in args.judge.split(",") if s.strip()]:
            llm_i, judge_info = build_adapter(spec, offline=False,
                                              org_id=str(args.org_id) if args.org_id else None,
                                              user_id=str(args.user_id) if args.user_id else None,
                                              replay_dir=args.replay_dir, record_dir=args.record_dir)
            judge_llms.append(llm_i)
            print(f"Juez: {judge_info['provider']}:{judge_info['model']} (fuente {judge_info['source']})")
        judge_mode = "llm"

    models: list[dict] = []
    for spec in [s.strip() for s in args.models.split(",") if s.strip()]:
        print(f"[{spec}] ejecutando {len(cases)} casos...")
        models.append(run_model(spec, cases, args, judge_llms))

    notes = []
    if args.offline:
        notes.append("Modo offline: generador FakeLLM y juez heurístico determinista. "
                     "Los gates de faithfulness NO son una evaluación semántica; sirven para CI.")
    if judge_mode == "heuristic" and not args.offline:
        notes.append("Sin --judge: no se ejecutó capa C; faithfulness queda n/a.")
    if any(m["judge"]["same_family_cases"] for m in models):
        notes.append("Hay casos con juez del mismo proveedor que el generador (same_family); "
                     "no cuentan como evaluación independiente.")

    report = {
        "golden": str(golden),
        "cases": len(cases),
        "mode": "offline" if args.offline else "providers",
        "judge_model": args.judge or "heuristic",
        "judge_mode": judge_mode,
        "models": models,
        "notes": notes,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md = out.with_suffix(".md")
    md.write_text(_markdown(report), encoding="utf-8")

    for row in models:
        status = "PASS" if row["gate_passed"] else "FAIL"
        print(f"{row['model']}: {status}  " + "  ".join(
            f"{k}={'ok' if v else 'fail'}" for k, v in row["gates"].items()))
    print(f"Report written to {out} (+ {md})")
    return 0 if all(m["gate_passed"] for m in models) else 1


if __name__ == "__main__":
    raise SystemExit(main())
