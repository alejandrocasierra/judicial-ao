#!/usr/bin/env python3
"""Golden eval runner del pipeline de chat (contrato §1.15, capas A y B).

Usa el pipeline REAL de producción:

    intent.classify() -> answering.retrieve() -> síntesis LLM -> compose.render()

No usa `legacy_retrieve(k=500)`: recupera con el embudo de producción y compone
con el compositor determinista, que es quien decide `answer_kind`, el número de
citas visibles y la coherencia entre respuesta y ocurrencias.

Métricas por caso y agregadas (por intención y global):

- retrieval_recall, citation_recall, citation_precision, noise_ratio
- contains_hit_rate, forbidden_hit, coherence_pass
- abstention_accuracy, answer_kind_match
- latencia p50/p95

Salida: JSON (--out) + resumen Markdown (`<out>.md`).

Uso:
  python scripts/run_evals.py --case-id <uuid> --org-id <uuid> --user-id <uuid>
  python scripts/run_evals.py --org-id <uuid> --user-id <uuid> --golden evals/golden/chat_pipeline.yaml --llm fake
  python scripts/run_evals.py ... --retrieval-only --out var/eval_retrieval.json
  python scripts/run_evals.py ... --record-dir evals/cassettes --out var/eval_report.json
  python scripts/run_evals.py ... --replay-dir evals/cassettes --out var/eval_replay.json
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))

log = logging.getLogger("run_evals")

_env_path = Path(os.environ.get("ENV_FILE") or (_ROOT / ".env"))
if _env_path.exists():
    import envload  # noqa: E402

    envload.load(str(_env_path), override=bool(os.environ.get("ENV_FILE")))

from app.core.config import get_settings  # noqa: E402
from app.core.db import rows, tx  # noqa: E402
from app.core.i18n import negotiate  # noqa: E402
from app.providers.llm import FakeLLM, get_llm  # noqa: E402
from app.services import answering  # noqa: E402

INTENTS = ("dato", "navegar", "analisis", "procedimental", "agregacion", "lectura", "correccion")
ANSWER_KINDS = ("direct", "partial", "insufficient", "navigational", "correction")


# ---------------------------------------------------------------------------
# Carga de casos
# ---------------------------------------------------------------------------

def _load_cases(golden: Path | None = None, golden_dir: Path | None = None) -> list[dict]:
    """Carga casos YAML/JSONL. Un archivo explícito manda sobre el directorio."""
    files: list[Path] = []
    if golden is not None:
        files = [golden] if golden.is_file() else sorted(golden.glob("*.y*ml"))
    if not files and golden_dir is not None:
        files = sorted(golden_dir.glob("*.y*ml"))
    cases: list[dict] = []
    for f in files:
        if f.suffix.lower() == ".jsonl":
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    c = json.loads(line)
                    c["source_file"] = f.name
                    cases.append(c)
            continue
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for c in data.get("cases", []):
            c["source_file"] = f.name
            cases.append(c)
    return cases


# ---------------------------------------------------------------------------
# Coincidencia de citas y métricas puras (testeables sin DB)
# ---------------------------------------------------------------------------

def _citation_matches(item: dict, expected: dict, start_tolerance_ms: int = 1500) -> bool:
    """¿La evidencia `item` corresponde a la fuente esperada?

    Soporta filename_contains, document_id, media_id, segment_id, page_number
    (alias `page`) y start_ms con tolerancia (los segmentos ASR no empiezan
    exactamente en el mismo milisegundo entre versiones de indexación).
    """
    filename = (item.get("filename") or "").lower()
    if expected.get("filename_contains") and expected["filename_contains"].lower() not in filename:
        return False
    if expected.get("document_id") and str(item.get("document_id")) != str(expected["document_id"]):
        return False
    if expected.get("media_id") and str(item.get("media_id")) != str(expected["media_id"]):
        return False
    if expected.get("segment_id") and str(item.get("segment_id")) != str(expected["segment_id"]):
        return False
    page = expected.get("page_number", expected.get("page"))
    if page is not None and item.get("page_number") != page:
        return False
    if expected.get("start_ms") is not None:
        actual = item.get("start_ms")
        if actual is None or abs(int(actual) - int(expected["start_ms"])) > start_tolerance_ms:
            return False
    return True


def _matches_any(item: dict, expected: list[dict]) -> bool:
    return any(_citation_matches(item, e) for e in expected or [])


_NEGATION_WINDOW = 45
_NEGATIONS = ("no ", "nunca", "sin ", "no se", "no puede", "no puedo", "no está", "no es",
              "falta", "carece", "no consta", "no hay", "tampoco")


def forbidden_hit(answer: str, phrases: list[str]) -> bool:
    """¿La respuesta afirma alguna frase prohibida?

    Se descarta la aparición cuando va precedida por una negación cercana
    («no puedo afirmar que el pago está probado»), porque repetir la frase para
    negarla no es afirmarla.
    """
    folded = (answer or "").lower()
    for phrase in phrases or []:
        needle = (phrase or "").lower().strip()
        if not needle:
            continue
        start = folded.find(needle)
        while start != -1:
            prefix = folded[max(0, start - _NEGATION_WINDOW):start]
            if not any(neg in prefix for neg in _NEGATIONS):
                return True
            start = folded.find(needle, start + 1)
    return False


def _is_relevant(item: dict, must_cite: list[dict], must_not_cite: list[dict]) -> bool | None:
    """Relevancia observable sin anotación humana.

    - Si hay `must_cite`, una cita solo es relevante si coincide con uno.
    - Si no hay `must_cite` pero sí `must_not_cite`, es relevante si NO coincide.
    - Sin criterios no se puede juzgar: None.
    """
    if must_cite:
        return _matches_any(item, must_cite)
    if must_not_cite:
        return not _matches_any(item, must_not_cite)
    return None


def citation_precision(used: list[dict], must_cite: list[dict], must_not_cite: list[dict]) -> float | None:
    """Citas usadas relevantes / citas usadas. None si no hay citas o no hay criterio."""
    if not used:
        return None
    verdicts = [_is_relevant(it, must_cite or [], must_not_cite or []) for it in used]
    if all(v is None for v in verdicts):
        return None
    relevant = sum(1 for v in verdicts if v)
    return relevant / len(used)


def noise_ratio(used: list[dict], must_cite: list[dict], must_not_cite: list[dict]) -> float | None:
    """Citas irrelevantes por respuesta (conteo, no fracción). None sin criterio."""
    verdicts = [_is_relevant(it, must_cite or [], must_not_cite or []) for it in used]
    if all(v is None for v in verdicts):
        return None
    return float(sum(1 for v in verdicts if v is False))


def _citation_key(item: dict) -> tuple:
    if item.get("document_id"):
        return ("doc", str(item["document_id"]), item.get("page_number"))
    return ("seg", str(item.get("media_id") or item.get("segment_id")), item.get("segment_id") or item.get("start_ms"))


def coherence_checks(case: dict, result: dict) -> dict:
    """Invariantes de coherencia del contrato §2.1 que no dependen del LLM."""
    kind = result.get("answer_kind")
    claims = result.get("claims") or []
    used = result.get("used_citations") or []
    occurrences = result.get("occurrences") or []
    reasons: list[str] = []

    expected_abstention = bool(case.get("expected_abstention") or case.get("must_abstain"))
    if expected_abstention and kind != "insufficient":
        reasons.append(f"se esperaba abstención y answer_kind={kind}")
    if kind == "insufficient":
        if claims:
            reasons.append("insufficient con claims")
        if used:
            reasons.append("insufficient con citas")
        if occurrences:
            reasons.append("insufficient con ocurrencias")
    if kind == "navigational" and claims:
        reasons.append("navigational con claims de respuesta")

    max_visible = case.get("max_visible_citations")
    if max_visible is not None and len(used) > int(max_visible):
        reasons.append(f"{len(used)} citas visibles > máximo {max_visible}")

    keys = [_citation_key(it) for it in used]
    if len(keys) != len(set(keys)):
        reasons.append("citas duplicadas por fuente")

    return {"coherence_pass": not reasons, "coherence_reasons": reasons}


def abstention_correct(case: dict, result: dict) -> bool | None:
    """True/False si el caso permite juzgar abstención; None si no aplica."""
    kind = result.get("answer_kind")
    expected = case.get("expected_answer_kind")
    expected_abstention = case.get("expected_abstention")
    if expected_abstention is None and case.get("must_abstain"):
        expected_abstention = True
    if expected_abstention:
        return kind == "insufficient" and not (result.get("used_citations") or [])
    if expected in (None, "insufficient"):
        return None if expected is None else False
    return kind != "insufficient"


def answer_kind_match(case: dict, result: dict) -> bool | None:
    expected = case.get("expected_answer_kind")
    if not expected:
        return None
    actual = result.get("answer_kind")
    if isinstance(expected, (list, tuple)):
        return actual in expected
    return actual == expected


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    data = sorted(float(v) for v in values)
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * p / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return data[lo]
    return data[lo] * (hi - pos) + data[hi] * (pos - lo)


def _resolve_used(result: dict) -> list[dict]:
    if result.get("used_citations") is not None:
        return result["used_citations"]
    evidence = result.get("evidence") or []
    by_handle = {it.get("handle"): it for it in evidence}
    handles = result.get("used_handles")
    if handles is None:
        handles = [h for cl in result.get("claims") or [] for h in cl.get("citations") or []]
    return [by_handle[h] for h in handles if h in by_handle]


def _evaluate(case: dict, result: dict, retrieval_only: bool = False) -> dict:
    """Evalúa un caso. Mantiene las claves históricas (abstain/pass) para no
    romper consumidores anteriores."""
    checks: dict[str, Any] = {}

    if case.get("must_abstain") and not case.get("expected_abstention"):
        abstained = result.get("evidence_count", 0) == 0 and not result.get("claims")
        checks["abstain"] = abstained
        checks["abstention_correct"] = abstained
        coerced = {**result, "answer_kind": result.get("answer_kind") or ("insufficient" if abstained else None)}
        checks.update(coherence_checks(case, coerced))
        checks["pass"] = abstained and checks["coherence_pass"]
        return checks

    answer = (result.get("answer") or "").lower()
    evidence = result.get("evidence") or []
    used = _resolve_used(result)
    must_cite = case.get("must_cite") or []
    must_not_cite = case.get("must_not_cite") or []

    checks["retrieval_recall"] = (
        sum(1 for exp in must_cite if any(_citation_matches(it, exp) for it in evidence)) / len(must_cite)
        if must_cite else 1.0
    )

    if retrieval_only:
        checks["citation_recall"] = None
        checks["citation_precision"] = None
        checks["noise_ratio"] = None
        checks["coherence_pass"] = True
        checks["pass"] = checks["retrieval_recall"] >= 1.0
        return checks

    checks["citation_recall"] = (
        sum(1 for exp in must_cite if any(_citation_matches(it, exp) for it in used)) / len(must_cite)
        if must_cite else 1.0
    )
    checks["citation_precision"] = citation_precision(used, must_cite, must_not_cite)
    checks["noise_ratio"] = noise_ratio(used, must_cite, must_not_cite)

    forbidden = list(case.get("forbidden_statements") or []) + list(case.get("must_not_claim") or [])
    checks["forbidden_hit"] = forbidden_hit(answer, forbidden)

    expected = case.get("must_contain") or case.get("expected_contains") or []
    checks["contains_hit_rate"] = (
        sum(1 for e in expected if e.lower() in answer) / len(expected) if expected else 1.0
    )

    checks.update(coherence_checks(case, result))
    checks["abstention_correct"] = abstention_correct(case, result)
    checks["answer_kind_match"] = answer_kind_match(case, result)

    checks["pass"] = (
        checks["citation_recall"] >= 1.0
        and checks["retrieval_recall"] >= 1.0
        and not checks["forbidden_hit"]
        and checks["contains_hit_rate"] >= 1.0
        and checks["coherence_pass"]
        and checks["abstention_correct"] is not False
        and checks["answer_kind_match"] is not False
    )
    return checks


# ---------------------------------------------------------------------------
# Pipeline real
# ---------------------------------------------------------------------------

def _load_module(name: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _pipeline() -> dict:
    return {
        "intent": _load_module("app.services.intent"),
        "compose": _load_module("app.services.compose"),
        "occurrences": _load_module("app.services.occurrences"),
        "text_terms": _load_module("app.services.text_terms"),
    }


def _classify(pipeline: dict, question: str):
    intent_mod = pipeline.get("intent")
    if intent_mod is None:
        return None, None
    decision = intent_mod.classify(question)
    return decision, getattr(decision.intent, "value", str(decision.intent))


def _retrieve(conn, case_id: str, question: str, decision, ocr_mode=None, asr_mode=None,
              diagnostics: dict | None = None) -> list[dict]:
    kwargs: dict = {"ocr_mode": ocr_mode, "asr_mode": asr_mode}
    params = inspect.signature(answering.retrieve).parameters
    if decision is not None:
        if "plan" in params:
            kwargs["plan"] = None
        if "intent" in params:
            kwargs["intent"] = decision.intent
    if diagnostics is not None and "diagnostics" in params:
        kwargs["diagnostics"] = diagnostics
    return answering.retrieve(conn, case_id, question, **kwargs)


def _resolve_case_id(conn, case: dict, default_case_id: str | None) -> str:
    ref = case.get("case_ref")
    if ref:
        found = rows(conn, "SELECT id FROM cases WHERE case_number = :n LIMIT 1", n=str(ref))
        if not found:
            raise KeyError(f"no existe un expediente con case_number={ref!r} (caso {case.get('id')})")
        return str(found[0]["id"])
    if not default_case_id:
        raise KeyError(f"el caso {case.get('id')} no tiene case_ref y no se pasó --case-id")
    return default_case_id


def _navigate(conn, case_id: str, question: str, locale: str, pipeline: dict,
              org_id: str | None, user_id: str | None) -> dict:
    """Intención navegar: locate + ocurrencias agrupadas. 0 LLM, como producción."""
    s = get_settings()
    term = None
    if pipeline.get("text_terms") is not None:
        term = pipeline["text_terms"].specific_term(question)
    term = term or question
    locs: list[dict] = []
    try:
        from app.services import case_tools
        from app.services.case_tools import ToolContext

        ctx = ToolContext(org_id=org_id, actor_id=user_id, locale=locale)
        locs = case_tools.execute(conn, case_id, "locate", {"term": term, "k": s.NAV_MAX_OCCURRENCES * 8}, ctx=ctx)
    except Exception as exc:  # la capa de evidencia no debe tumbar el eval
        return {"answer": "", "answer_kind": "navigational", "claims": [], "evidence": [], "evidence_count": 0,
                "used_handles": [], "used_citations": [], "occurrences": [], "occurrences_term": term,
                "uncertainties": [], "unsupported_claims": [], "degraded": True, "error": str(exc)}

    occ_mod = pipeline.get("occurrences")
    if occ_mod is not None:
        text, cites = occ_mod.build(term, locs, locale, max_items=s.NAV_MAX_OCCURRENCES)
    else:
        cites = locs[: s.NAV_MAX_OCCURRENCES]
        lines = []
        for it in cites:
            if it.get("source_type") == "document_page":
                lines.append(f"- {it.get('filename')} · página {it.get('page_number')}")
            else:
                start = it.get("start_ms")
                mmss = f"{int(start) // 60000}:{(int(start) // 1000) % 60:02d}" if start is not None else ""
                lines.append(f"- {it.get('filename')} · {mmss} · {it.get('speaker') or ''}".rstrip(" ·"))
        header = f"Menciones del término «{term}». No son la respuesta."
        text = header + ("\n" + "\n".join(lines) if lines else "")
    return {"answer": text, "answer_kind": "navigational", "claims": [], "evidence": locs,
            "evidence_count": len(locs), "used_handles": [], "used_citations": [], "occurrences": cites,
            "occurrences_term": term, "uncertainties": [], "unsupported_claims": []}


def _run_rag(conn, case_id: str, question: str, locale: str, llm, pipeline: dict,
             retrieval_only: bool, org_id: str | None, user_id: str | None,
             ocr_mode: str | None, asr_mode: str | None) -> dict:
    s = get_settings()
    t_classify = time.perf_counter()
    decision, intent_name = _classify(pipeline, question)
    classify_ms = (time.perf_counter() - t_classify) * 1000

    if intent_name == "navegar" and not retrieval_only:
        result = _navigate(conn, case_id, question, locale, pipeline, org_id, user_id)
        result.update({"intent": intent_name, "intent_source": getattr(decision, "source", None) if decision else None,
                       "latency_ms": classify_ms, "stage_ms": {"classify": classify_ms, "retrieve": 0.0,
                                                               "synthesis": 0.0, "compose": 0.0}})
        return result

    t_retrieve = time.perf_counter()
    diagnostics: dict = {}
    role_items: list[dict] = []
    if intent_name == "agregacion":
        try:
            from app.services import role_answer
            role_items = role_answer.authoritative_evidence(str(org_id), str(user_id), case_id, question)
        except Exception as exc:  # el seed de roles es opcional en la capa de eval
            log.warning("role_answer seed no disponible: %s", exc)
    items = _retrieve(conn, case_id, question, decision, ocr_mode=ocr_mode, asr_mode=asr_mode,
                      diagnostics=diagnostics)
    if role_items:
        items = answering.merge_items(role_items, items)
    retrieve_ms = (time.perf_counter() - t_retrieve) * 1000

    base = {"intent": intent_name, "intent_source": getattr(decision, "source", None) if decision else None,
            "evidence": items, "evidence_count": len(items), "claims": [], "used_handles": [],
            "used_citations": [], "occurrences": [], "uncertainties": [], "unsupported_claims": [],
            "coverage": diagnostics.get("coverage"),
            "stage_ms": {"classify": classify_ms, "retrieve": retrieve_ms, "synthesis": 0.0, "compose": 0.0}}

    if retrieval_only:
        base["pass_evidence"] = True
        return base

    compose_mod = pipeline.get("compose")
    if compose_mod is None:
        raise RuntimeError(
            "app.services.compose no existe todavía: el pipeline de producción requiere el compositor "
            "(workstream B). Usa --retrieval-only para medir solo recuperación."
        )
    if not items:
        composed = compose_mod.render(
            question, intent=decision.intent if decision else None,
            claims=[], evidence=[], uncertainties=[], locale=locale,
            max_citations=s.MAX_VISIBLE_CITATIONS, max_per_file=s.MAX_CITATIONS_PER_FILE,
        )
        base.update({"answer": composed.answer, "answer_kind": composed.answer_kind,
                     "used_handles": list(composed.used_handles), "uncertainties": list(composed.uncertainties)})
        return base

    system, _prompt_id, _prompt_version = answering.system_prompt(locale)
    t_synth = time.perf_counter()
    llm_result = llm.complete(system, answering.build_user_prompt(question, items))
    synthesis_ms = (time.perf_counter() - t_synth) * 1000
    validated = answering.parse_and_validate(llm_result.text, items, s.ANSWER_MIN_GROUNDING_OVERLAP)

    t_compose = time.perf_counter()
    try:
        composed = compose_mod.render(
            question, intent=decision.intent if decision else None,
            claims=validated["claims"], evidence=items, uncertainties=validated["uncertainties"],
            locale=locale, max_citations=s.MAX_VISIBLE_CITATIONS, max_per_file=s.MAX_CITATIONS_PER_FILE,
            coverage=diagnostics.get("coverage"),
        )
    except TypeError:
        # Firma mínima del contrato, por si el compositor aún no acepta presupuestos.
        composed = compose_mod.render(
            question, intent=decision.intent if decision else None,
            claims=validated["claims"], evidence=items, uncertainties=validated["uncertainties"], locale=locale,
        )
    compose_ms = (time.perf_counter() - t_compose) * 1000

    by_handle = {it.get("handle"): it for it in items}
    used_handles = list(getattr(composed, "used_handles", []) or [])
    used_set = set(used_handles)
    rendered_claims = [cl for cl in validated["claims"]
                       if used_set & set(cl.get("citations") or [])]
    base.update({
        "answer": composed.answer,
        "answer_kind": composed.answer_kind,
        "claims": rendered_claims,
        "used_handles": used_handles,
        "used_citations": [by_handle[h] for h in used_handles if h in by_handle],
        "uncertainties": list(getattr(composed, "uncertainties", validated["uncertainties"]) or []),
        "unsupported_claims": validated["unsupported_claims"],
        "model": getattr(llm_result, "model", None),
        "provider": getattr(llm_result, "provider", None),
        "stage_ms": {"classify": classify_ms, "retrieve": retrieve_ms,
                     "synthesis": synthesis_ms, "compose": compose_ms},
    })
    return base


def _run_agent(conn, case_id: str, question: str, locale: str, case: dict) -> dict:
    from app.services import agent

    result = agent.run_agent_query(conn, case_id, question, locale)
    evidence = result.get("evidence") or []
    claims = result.get("claims") or []
    by_handle = {it.get("handle"): it for it in evidence}
    used_handles = [h for cl in claims for h in cl.get("citations") or []]
    return {"answer": result.get("answer", ""), "answer_kind": None, "claims": claims,
            "evidence": evidence, "evidence_count": len(evidence),
            "used_handles": used_handles,
            "used_citations": [by_handle[h] for h in used_handles if h in by_handle],
            "occurrences": [], "uncertainties": result.get("uncertainties") or [],
            "unsupported_claims": result.get("unsupported_claims") or [],
            "intent": case.get("intent"), "stage_ms": {}}


def _make_llm(mode: str) -> Any:
    if mode == "fake":
        return FakeLLM(get_settings().LLM_MODEL)
    return get_llm()


def _wrap_llm(llm, args):
    from app.providers import recording

    if args.replay_dir:
        return recording.wrap(llm, mode="replay", path=args.replay_dir,
                              on_miss="error" if args.strict_replay else "fallback")
    if args.record_dir:
        return recording.wrap(llm, mode="record", path=args.record_dir)
    return llm


# ---------------------------------------------------------------------------
# Agregación del informe
# ---------------------------------------------------------------------------

def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _aggregate(records: list[dict]) -> tuple[dict, dict[str, str]]:
    total = len(records)
    checks = [r["checks"] for r in records]
    latencies = [r["result"].get("latency_ms", 0.0) for r in records]
    metrics: dict[str, Any] = {}
    reasons: dict[str, str] = {}

    def _set(name: str, values: list, reason: str = "sin casos aplicables"):
        value = _mean(values)
        metrics[name] = value
        if value is None:
            reasons[name] = reason

    _set("retrieval_recall", [c.get("retrieval_recall") for c in checks])
    _set("citation_recall", [c.get("citation_recall") for c in checks])
    _set("citation_precision", [c.get("citation_precision") for c in checks],
         "ningún caso usó citas o no hay criterio de relevancia")
    _set("noise_ratio", [c.get("noise_ratio") for c in checks],
         "ningún caso permite juzgar ruido de citas")
    _set("contains_hit_rate", [c.get("contains_hit_rate") for c in checks])
    _set("coherence_pass", [1.0 if c.get("coherence_pass") else 0.0 for c in checks])
    _set("abstention_accuracy", [1.0 if c.get("abstention_correct") else 0.0
                                 for c in checks if c.get("abstention_correct") is not None],
         "ningún caso permite juzgar abstención")
    _set("answer_kind_match", [1.0 if c.get("answer_kind_match") else 0.0
                               for c in checks if c.get("answer_kind_match") is not None],
         "los casos no declaran expected_answer_kind")

    metrics["forbidden_hit"] = sum(1 for c in checks if c.get("forbidden_hit"))
    metrics["latency_p50_ms"] = percentile(latencies, 50)
    metrics["latency_p95_ms"] = percentile(latencies, 95)
    if not latencies:
        reasons["latency_p50_ms"] = reasons["latency_p95_ms"] = "sin latencias registradas"
    metrics["pass_rate"] = sum(1 for r in records if r["checks"].get("pass")) / total if total else None
    metrics["cases"] = total
    metrics["passed"] = sum(1 for r in records if r["checks"].get("pass"))
    return metrics, reasons


def _per_intent(records: list[dict]) -> dict:
    out: dict[str, dict] = {}
    groups: dict[str, list[dict]] = {}
    for r in records:
        key = r["case"].get("intent") or "sin_intent"
        groups.setdefault(key, []).append(r)
    for intent, items in sorted(groups.items()):
        metrics, reasons = _aggregate(items)
        out[intent] = {"metrics": metrics, "reasons": reasons}
    return out


def _markdown(report: dict) -> str:
    m = report["metrics"]
    reasons = report.get("metric_reasons") or {}

    def fmt(v, pct=False, suffix=""):
        if v is None:
            return "n/a"
        return f"{v:.1%}" if pct else f"{v:.2f}{suffix}"

    lines = [
        "# Golden eval — pipeline de chat",
        "",
        f"- Casos: **{m.get('cases', 0)}** · aprobados: **{m.get('passed', 0)}** · pass rate: **{fmt(m.get('pass_rate'), pct=True)}**",
        f"- llm: `{report.get('llm')}` · estrategia: `{report.get('strategy')}` · retrieval_only: `{report.get('retrieval_only')}`",
    ]
    if report.get("skipped"):
        lines.append(f"- Omitidos por falta de expediente aplicable: **{len(report['skipped'])}**")
    lines += [
        "",
        "## Métricas globales",
        "",
        "| Métrica | Valor | Nota |",
        "|---|---|---|",
    ]
    labels = [
        ("retrieval_recall", False), ("citation_recall", False), ("citation_precision", False),
        ("noise_ratio", False), ("contains_hit_rate", False), ("coherence_pass", True),
        ("abstention_accuracy", True), ("answer_kind_match", True),
    ]
    for name, pct in labels:
        lines.append(f"| {name} | {fmt(m.get(name), pct=pct)} | {reasons.get(name, '')} |")
    lines.append(f"| forbidden_hit | {m.get('forbidden_hit', 0)} | conteo de casos con frase prohibida |")
    lines.append(f"| latencia p50 | {fmt(m.get('latency_p50_ms'))} ms | |")
    lines.append(f"| latencia p95 | {fmt(m.get('latency_p95_ms'))} ms | |")
    lines += ["", "## Por intención", "", "| Intención | Casos | Pass | recall | cit_precision | coherencia | p95 |",
              "|---|---|---|---|---|---|---|"]
    for intent, data in (report.get("by_intent") or {}).items():
        mi = data["metrics"]
        lines.append(
            f"| {intent} | {mi.get('cases', 0)} | {fmt(mi.get('pass_rate'), pct=True)} | "
            f"{fmt(mi.get('citation_recall'))} | {fmt(mi.get('citation_precision'))} | "
            f"{fmt(mi.get('coherence_pass'), pct=True)} | {fmt(mi.get('latency_p95_ms'))} ms |"
        )
    failures = [r for r in report["cases"] if not r["pass"]]
    if failures:
        lines += ["", "## Fallos", ""]
        for r in failures[:40]:
            lines.append(f"- **{r['id']}** ({r.get('intent')}): {', '.join(r.get('failures') or [])}")
    return "\n".join(lines) + "\n"


def _write_report(report: dict, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = out_path.with_suffix(".md")
    md_path.write_text(_markdown(report), encoding="utf-8")
    return md_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Golden eval del pipeline real (retrieve + compose).")
    p.add_argument("--case-id", type=UUID, default=None, help="Expediente por defecto si el caso no trae case_ref")
    p.add_argument("--org-id", required=True, type=UUID)
    p.add_argument("--user-id", required=True, type=UUID)
    p.add_argument("--golden", default=None, help="Archivo YAML/JSONL o directorio del set dorado")
    p.add_argument("--golden-dir", default="evals/golden")
    p.add_argument("--strategy", choices=["rag", "agent"], default=None)
    p.add_argument("--retrieval-only", action="store_true")
    p.add_argument("--threshold", type=float, default=1.0)
    p.add_argument("--out", default="var/eval_report.json")
    p.add_argument("--locale", default="es")
    p.add_argument("--llm", choices=["fake", "provider"], default="fake",
                   help="fake = determinista sin red; provider = proveedor configurado")
    p.add_argument("--record-dir", default=None, help="Graba cassettes JSONL en este directorio")
    p.add_argument("--replay-dir", default=None, help="Reproduce cassettes JSONL desde este directorio")
    p.add_argument("--strict-replay", action="store_true",
                   help="Falla si una llamada no tiene cassette (por defecto cae al proveedor)")
    p.add_argument("--ocr-mode", default=None)
    p.add_argument("--asr-mode", default=None)
    args = p.parse_args()

    golden = Path(args.golden) if args.golden else None
    cases = _load_cases(golden, Path(args.golden_dir))
    if not cases:
        print("[WARN] no golden cases found")
        return 0

    locale = negotiate(None, args.locale)
    pipeline = _pipeline()
    if pipeline.get("compose") is None and not args.retrieval_only:
        print("[ERROR] app.services.compose no existe todavía; usa --retrieval-only para capa B.")
        return 2
    if pipeline.get("intent") is None:
        print("[WARN] app.services.intent no existe: la intención se toma del set dorado.")

    llm = _wrap_llm(_make_llm(args.llm), args)
    records: list[dict] = []
    skipped: list[dict] = []
    for case in cases:
        strategy = args.strategy or case.get("strategy", "rag")
        started = time.perf_counter()
        try:
            case_id = None
            with tx(args.org_id, args.user_id) as conn:
                case_id = _resolve_case_id(conn, case, str(args.case_id) if args.case_id else None)
                question = case["question"]
                if strategy == "agent":
                    result = _run_agent(conn, case_id, question, locale, case)
                    result["intent"] = result.get("intent") or case.get("intent")
                else:
                    try:
                        result = _run_rag(conn, case_id, question, locale, llm, pipeline,
                                          args.retrieval_only, str(args.org_id), str(args.user_id),
                                          args.ocr_mode, args.asr_mode)
                    except Exception as exc:
                        result = {"answer": "", "answer_kind": None, "claims": [], "evidence": [],
                                  "evidence_count": 0, "used_handles": [], "used_citations": [],
                                  "occurrences": [], "uncertainties": [], "unsupported_claims": [],
                                  "intent": case.get("intent"), "error": f"{type(exc).__name__}: {exc}"}
        except KeyError as exc:
            skipped.append({"id": case.get("id"), "reason": str(exc)})
            continue
        result["latency_ms"] = (time.perf_counter() - started) * 1000
        checks = _evaluate(case, result, retrieval_only=args.retrieval_only)
        failures: list[str] = []
        if checks.get("forbidden_hit"):
            failures.append("forbidden_hit")
        for key in ("abstain", "coherence_pass", "abstention_correct", "answer_kind_match"):
            if checks.get(key) is False:
                failures.append(key)
        if checks.get("citation_recall") is not None and checks["citation_recall"] < 1.0:
            failures.append("citation_recall")
        if checks.get("retrieval_recall") is not None and checks["retrieval_recall"] < 1.0:
            failures.append("retrieval_recall")
        if checks.get("contains_hit_rate") is not None and checks["contains_hit_rate"] < 1.0:
            failures.append("contains")
        records.append({"case": case, "result": result, "checks": checks, "failures": failures})

    metrics, reasons = _aggregate(records)
    report = {
        "total": len(records),
        "passed": metrics["passed"],
        "pass_rate": metrics["pass_rate"],
        "skipped": skipped,
        "llm": args.llm,
        "strategy": args.strategy or "por caso (default rag)",
        "retrieval_only": args.retrieval_only,
        "pipeline": {"intent": pipeline.get("intent") is not None,
                     "compose": pipeline.get("compose") is not None,
                     "occurrences": pipeline.get("occurrences") is not None},
        "metrics": metrics,
        "metric_reasons": reasons,
        "by_intent": _per_intent(records),
        "cases": [
            {
                "id": r["case"]["id"],
                "intent": r["result"].get("intent") or r["case"].get("intent"),
                "expected_intent": r["case"].get("intent"),
                "question": r["case"]["question"],
                "answer_kind": r["result"].get("answer_kind"),
                "expected_answer_kind": r["case"].get("expected_answer_kind"),
                "answer": (r["result"].get("answer") or "")[:600],
                "pass": r["checks"].get("pass"),
                "failures": r["failures"],
                "checks": r["checks"],
                "latency_ms": round(r["result"].get("latency_ms", 0.0), 2),
                "stage_ms": r["result"].get("stage_ms", {}),
                "error": r["result"].get("error"),
            }
            for r in records
        ],
    }
    if hasattr(llm, "stats"):
        report["recording"] = llm.stats()

    out_path = Path(args.out)
    md_path = _write_report(report, out_path)

    print(f"Golden eval: {report['passed']}/{report['total']} passed ({(report['pass_rate'] or 0):.0%})")
    if skipped:
        print(f"  omitidos: {len(skipped)} casos sin expediente aplicable en esta base")
    for name in ("retrieval_recall", "citation_recall", "citation_precision", "noise_ratio",
                 "coherence_pass", "abstention_accuracy", "answer_kind_match"):
        value = metrics.get(name)
        print(f"  {name}={'n/a' if value is None else f'{value:.3f}'}")
    print(f"  p50={metrics.get('latency_p50_ms')} ms  p95={metrics.get('latency_p95_ms')} ms")
    print(f"Report written to {out_path} (+ {md_path})")
    return 0 if report["pass_rate"] >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
