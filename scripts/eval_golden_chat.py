#!/usr/bin/env python3
"""Línea base y comparación del chat-investigador sobre el set dorado (Fase 0).

Mide el chat REAL (API en Docker) con las 3 preguntas del usuario sobre el
expediente 11001310302120180036100 y deja evidencia reproducible en:

    var/eval/golden_before.json   datos completos (respuesta sin truncar)
    var/eval/golden_before.md     tabla PASS/FAIL por criterio + diagnóstico

Reglas del banco:
- el evaluador NO aborta cuando un caso falla: mide y sigue (T2);
- la respuesta se guarda COMPLETA (T3), no truncada a 500 caracteres;
- sin secretos en los artefactos: no se escriben credenciales ni tokens.

Uso:
    python scripts/eval_golden_chat.py --label before
    python scripts/eval_golden_chat.py --api http://127.0.0.1:8000/v1 --only INV-01,INV-03
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DEFAULT = _ROOT / "evals" / "golden" / "chat_investigacion.yaml"
DEFAULT_API = "http://127.0.0.1:8000/v1"
DEFAULT_TIMEOUT_S = 240
LOGIN_ATTEMPTS = 3


# --------------------------------------------------------------------------- puras
def normalize(text: object) -> str:
    """Minúsculas, sin diacríticos y con espacios colapsados.

    Sirve para comparar `must_contain` y `forbidden_phrases` sin que un acento o
    una mayúscula decida el resultado: "PAGARÉ" y "pagare" son el mismo término.
    """
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.lower().split())


def contains_term(text: str, term: object) -> bool:
    """Coincidencia de contenido, no de subcadena numérica.

    `"15"` no puede pasar por el `0015` de un nombre de archivo: los términos
    puramente numéricos exigen límites de dígito. El resto se compara como frase
    normalizada.
    """
    haystack = normalize(text)
    needle = normalize(term)
    if not needle:
        return False
    if needle.isdigit():
        return re.search(rf"(?<!\d){re.escape(needle)}(?!\d)", haystack) is not None
    return needle in haystack


def _criterion(cid: str, passed: bool, detail: str) -> dict:
    return {"id": cid, "passed": bool(passed), "detail": detail}


def _citation_matches(citation: dict, spec: dict) -> bool:
    """Una cita satisface el spec si TODAS las claves del spec coinciden."""
    for key, expected in (spec or {}).items():
        if key == "filename_contains":
            if normalize(expected) not in normalize(citation.get("filename") or ""):
                return False
        elif key in ("page_number", "page"):
            if citation.get("page", citation.get("page_number")) != expected:
                return False
        elif key == "start_ms":
            if citation.get("start_ms") != expected:
                return False
        elif key == "source_type":
            if citation.get("source_type") != expected:
                return False
        elif normalize(expected) not in normalize(str(citation.get(key) or "")):
            return False
    return True


def _filenames(citations: list[dict]) -> list[str]:
    return [str(c.get("filename") or c.get("document_id") or c.get("media_id") or "?")
            for c in citations or []]


def _describe_specs(specs: list[dict]) -> str:
    parts = []
    for spec in specs or []:
        bits = [f"{k}={v}" for k, v in (spec or {}).items()]
        parts.append(", ".join(bits) or "?")
    return " | ".join(parts)


def evaluate_case(case: dict, result: dict) -> dict:
    """Evalúa un caso contra su respuesta. Nunca lanza: un error es un FAIL."""
    criteria: list[dict] = []
    answer = str((result or {}).get("answer") or "")
    citations = list((result or {}).get("citations_detail") or [])
    answer_norm = normalize(answer)

    error = (result or {}).get("error")
    timeout = bool((result or {}).get("timeout"))
    if error or timeout:
        detail = f"timeout: {error}" if timeout else f"error: {error}"
        criteria.append(_criterion("respuesta", False, detail))

    expected_kind = case.get("expected_answer_kind")
    if expected_kind:
        obtained = (result or {}).get("answer_kind")
        criteria.append(_criterion(
            "answer_kind", obtained == expected_kind,
            f"obtenido={obtained} esperado={expected_kind}"))

    for term in case.get("must_contain") or []:
        present = contains_term(answer_norm, term)
        criteria.append(_criterion(
            f"must_contain:{term}", present,
            "presente" if present else "no aparece en la respuesta"))

    specs = case.get("must_cite_any") or []
    if specs:
        matched = None
        for citation in citations:
            for spec in specs:
                if _citation_matches(citation, spec):
                    matched = citation
                    break
            if matched:
                break
        if matched:
            detail = f"coincide: {matched.get('filename') or matched.get('media_id') or '?'}"
        elif citations:
            detail = f"ninguna cita coincide; citadas: {', '.join(_filenames(citations)[:8])}"
        else:
            detail = f"sin citas; se exigía alguna de: {_describe_specs(specs)}"
        criteria.append(_criterion("must_cite_any", matched is not None, detail))

    if case.get("min_citations") is not None:
        minimo = int(case["min_citations"])
        criteria.append(_criterion(
            "min_citations", len(citations) >= minimo,
            f"{len(citations)} citas (mínimo {minimo})"))

    if case.get("min_length") is not None:
        minimo = int(case["min_length"])
        criteria.append(_criterion(
            "min_length", len(answer) >= minimo,
            f"{len(answer)} caracteres (mínimo {minimo})"))

    headings = case.get("required_headings") or []
    if not headings:
        criteria.append(_criterion("required_headings", True, "sin encabezados exigidos"))
    for heading in headings:
        criteria.append(_criterion(
            f"required_headings:{heading}", normalize(heading) in answer_norm,
            "presente" if normalize(heading) in answer_norm else "encabezado ausente"))

    phrases = case.get("forbidden_phrases") or []
    found = [p for p in phrases if normalize(p) in answer_norm]
    criteria.append(_criterion(
        "forbidden_phrases", not found,
        f"encontradas: {', '.join(found)}" if found else "ninguna frase prohibida"))

    failed = [c["id"] for c in criteria if not c["passed"]]
    return {
        "id": case.get("id"),
        "question": case.get("question"),
        "passed": not failed,
        "failed": failed,
        "criteria": criteria,
        "length": len(answer),
        "answer_kind": (result or {}).get("answer_kind"),
        "intent": (result or {}).get("intent"),
        "elapsed": (result or {}).get("elapsed"),
        "timeout": timeout,
        "error": error,
    }


def evaluate_cases(cases: list[dict], results: list[dict]) -> dict:
    """Evalúa todos los casos; un resultado ausente cuenta como error, no aborta."""
    entries: list[dict] = []
    for index, case in enumerate(cases):
        result = results[index] if index < len(results) else {"error": "sin resultado"}
        evaluation = evaluate_case(case, result)
        answer = str((result or {}).get("answer") or "")
        citations = list((result or {}).get("citations_detail") or [])
        entries.append({
            "id": evaluation["id"],
            "question": case.get("question"),
            "passed": evaluation["passed"],
            "failed": evaluation["failed"],
            "criteria": evaluation["criteria"],
            "answer": answer,
            "answer_kind": evaluation["answer_kind"],
            "intent": evaluation["intent"],
            "degraded": bool((result or {}).get("degraded")),
            "cached": bool((result or {}).get("cached")),
            "citations_detail": citations,
            "citations": len(citations),
            "claims": list((result or {}).get("claims") or []),
            "claims_count": len((result or {}).get("claims") or []),
            "evidence_count": (result or {}).get("evidence_count"),
            "uncertainties": list((result or {}).get("uncertainties") or []),
            "elapsed": evaluation["elapsed"],
            "timeout": evaluation["timeout"],
            "error": evaluation["error"],
            "length": evaluation["length"],
            "session": (result or {}).get("session"),
            "model_run_id": (result or {}).get("model_run_id"),
            "expected_answer_kind": case.get("expected_answer_kind"),
            "latency_budget_ms": case.get("latency_budget_ms"),
        })
    passed = sum(1 for e in entries if e["passed"])
    return {
        "cases": entries,
        "summary": {
            "n": len(entries),
            "passed": passed,
            "failed": len(entries) - passed,
            "timeouts": sum(1 for e in entries if e["timeout"]),
            "errors": sum(1 for e in entries if e["error"]),
            "elapsed_total": round(sum(float(e["elapsed"] or 0) for e in entries), 2),
        },
        "passed": bool(entries) and passed == len(entries),
    }


# ------------------------------------------------------------------------ informe
def _cell(value: object) -> str:
    return " ".join(str(value if value is not None else "—").split()).replace("|", "\\|")


def _missing_sources(case: dict, entry: dict) -> list[str]:
    """Fuentes exigidas que no aparecieron en las citas, por familia."""
    missing: list[str] = []
    for spec in case.get("must_cite_any") or []:
        name = str(spec.get("filename_contains") or "")
        if any(_citation_matches(c, spec) for c in entry["citations_detail"]):
            continue
        if name.lower().endswith((".mp4", ".wav", ".mp3", ".m4a")) or "grabacion" in name.lower():
            missing.append(f"ASR:{name}")
        else:
            missing.append(f"OCR:{name}")
    return missing


def _diagnostico(cases: list[dict], entries: list[dict]) -> list[str]:
    lines: list[str] = []
    cartel = normalize("Encontré material relacionado")
    truncado = normalize("Se omitieron afirmaciones")

    for case, entry in zip(cases, entries, strict=False):
        answer = normalize(entry["answer"])
        abre_cartel = answer.startswith(cartel)
        contiene_cartel = cartel in answer
        contiene_truncado = truncado in answer
        missing = _missing_sources(case, entry)
        if entry["error"] or entry["timeout"]:
            estado = f"sin respuesta ({entry['error']})"
        else:
            estado = "PASS" if entry["passed"] else f"FAIL: {', '.join(entry['failed'])}"
        lines.append(
            f"- **{entry['id']}** — {estado}. "
            f"Intención enrutada: `{entry['intent']}`; answer_kind: `{entry['answer_kind']}` "
            f"(esperado `{entry['expected_answer_kind']}`). "
            f"Cartel \"Encontré material relacionado\": "
            f"{'abre la respuesta' if abre_cartel else ('aparece dentro' if contiene_cartel else 'ausente')}. "
            f"Truncamiento \"Se omitieron afirmaciones\": "
            f"{'presente' if contiene_truncado else 'ausente'}. "
            f"claims={entry['claims_count']}, evidence_count={entry['evidence_count']}, "
            f"uncertainties={len(entry['uncertainties'])}, degradado={entry['degraded']}, "
            f"citas={entry['citations']}."
        )
        if missing:
            lines.append(f"  - Fuentes exigidas que faltaron: {', '.join(missing)}.")
        if entry["claims_count"] == 0:
            lines.append("  - `facts`/claims: la respuesta no trae afirmaciones estructuradas "
                         "(0 claims); la capa de hechos no está alimentando el chat.")

    sin_claims = [e["id"] for e in entries if e["claims_count"] == 0]
    if sin_claims:
        lines.append(f"- Capa `facts`: {len(sin_claims)}/{len(entries)} respuestas con 0 claims "
                     f"({', '.join(sin_claims)}).")
    fallos = [e["id"] for e in entries if not e["passed"]]
    lines.append(
        "- Lectura de la línea base: "
        + ("ningún caso cumple todos los criterios todavía; "
           if fallos else "todos los casos cumplen los criterios; ")
        + f"{len(fallos)}/{len(entries)} casos en FAIL ({', '.join(fallos) or 'ninguno'}).")
    return lines


def render_markdown(report: dict) -> str:
    """Informe legible: tabla por caso, PASS/FAIL por criterio y diagnóstico."""
    label = report.get("label") or "before"
    summary = report.get("summary") or {}
    cases = report.get("cases") or []
    out: list[str] = [
        f"# Línea base del chat-investigador — `{label}`",
        "",
        f"- **API**: `{report.get('api', '—')}`",
        f"- **Expediente**: `{report.get('case_ref', '—')}` (`case_id = {report.get('case_id', '—')}`)",
        f"- **Generado**: {report.get('generated_at', '—')}",
        f"- **Resumen**: {summary.get('passed', 0)} PASS / {summary.get('failed', 0)} FAIL "
        f"de {summary.get('n', 0)} · timeouts={summary.get('timeouts', 0)} · "
        f"errores={summary.get('errors', 0)} · tiempo total={summary.get('elapsed_total', 0)} s",
        "",
        "> Un FAIL mide; no bloquea. Los umbrales vienen del contrato F0 y no se ajustan aquí.",
        "",
        "## Resumen por caso",
        "",
        "| Caso | answer_kind | intent | Citas | Longitud | Tiempo (s) | Resultado |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in cases:
        out.append(
            f"| {_cell(entry['id'])} | {_cell(entry['answer_kind'])} | {_cell(entry['intent'])} | "
            f"{entry['citations']} | {entry['length']} | {_cell(entry['elapsed'])} | "
            f"{'PASS' if entry['passed'] else 'FAIL'} |")

    for entry in cases:
        out += [
            "",
            f"## {entry['id']} — {'PASS' if entry['passed'] else 'FAIL'}",
            "",
            f"**Pregunta**: {entry['question']}",
            "",
            f"`answer_kind={entry['answer_kind']}` · `intent={entry['intent']}` · "
            f"`degraded={entry['degraded']}` · `cached={entry['cached']}` · "
            f"`citas={entry['citations']}` · `claims={entry['claims_count']}` · "
            f"`evidence_count={entry['evidence_count']}` · `longitud={entry['length']}` · "
            f"`elapsed={entry['elapsed']} s` · `timeout={entry['timeout']}`",
            "",
            "| Criterio | Resultado | Detalle |",
            "|---|---|---|",
        ]
        for criterion in entry["criteria"]:
            out.append(f"| {_cell(criterion['id'])} | "
                       f"{'PASS' if criterion['passed'] else 'FAIL'} | "
                       f"{_cell(criterion['detail'])} |")

    out += ["", "## Diagnóstico", ""]
    out += _diagnostico(_cases_from_report(report), cases)
    out.append("")
    return "\n".join(out)


def _cases_from_report(report: dict) -> list[dict]:
    """Reconstruye lo mínimo que necesita el diagnóstico desde el informe."""
    return [{
        "id": entry.get("id"),
        "must_cite_any": entry.get("must_cite_any") or [],
    } for entry in report.get("cases") or []]


# ----------------------------------------------------------------------- red/API
def _load_env() -> None:
    """Carga .env sin imprimir valores (nunca se loguean credenciales)."""
    try:
        import envload
    except ImportError:  # pragma: no cover — el script corre con scripts/ en sys.path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import envload
    envload.load(os.environ.get("ENV_FILE") or str(_ROOT / ".env"))


def _health_url(api: str) -> str:
    base = api.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/health"


def _request(method: str, url: str, *, timeout: float, attempts: int = 4, **kwargs):
    """Reintenta 429/5xx y errores de red con backoff; devuelve la última respuesta."""
    import requests

    response = None
    for attempt in range(attempts):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException:
            if attempt == attempts - 1:
                raise
            time.sleep(min(30.0, 2 ** attempt * 3.0))
            continue
        if response.status_code != 429 and response.status_code < 500:
            return response
        wait = response.headers.get("Retry-After")
        try:
            delay = float(wait) if wait else min(30.0, 2 ** attempt * 3.0)
        except ValueError:
            delay = min(30.0, 2 ** attempt * 3.0)
        if attempt < attempts - 1:
            print(f"    [retry] {response.status_code} en {url} · espera {delay:.1f}s")
            time.sleep(delay)
    return response


def _fail(code: int, message: str) -> "SystemExit":
    print(f"[ERROR] {message}")
    return SystemExit(code)


def _check_health(api: str) -> None:
    import requests

    url = _health_url(api)
    try:
        response = requests.get(url, timeout=10)
    except requests.RequestException as exc:
        raise _fail(2, f"el API no responde en {url}: {exc}") from exc
    if response.status_code != 200:
        raise _fail(2, f"el API responde {response.status_code} en {url} (se exige 200)")
    print(f"health OK: {url} -> {response.text.strip()[:120]}")


def _login(api: str) -> dict:
    """Autentica con las credenciales del .env; 3 intentos y salida 2 si falla."""
    candidates = [
        (os.environ.get("BOOTSTRAP_ADMIN_EMAIL") or "admin@judicial.ai",
         os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")),
        (f"gestor.alfa@{os.environ.get('SEED_EMAIL_DOMAIN', 'example.test')}",
         os.environ.get("SEED_DEFAULT_PASSWORD", "")),
    ]
    candidates = [(email, password) for email, password in candidates if password]
    if not candidates:
        raise _fail(2, "sin credenciales: define BOOTSTRAP_ADMIN_PASSWORD o SEED_DEFAULT_PASSWORD")

    last = "sin intento"
    for attempt in range(LOGIN_ATTEMPTS):
        for email, password in candidates:
            try:
                response = _request("POST", f"{api}/auth/login", timeout=30, attempts=2,
                                    json={"email": email, "password": password})
            except Exception as exc:  # noqa: BLE001 — la red no debe tumbar el banco
                last = f"red: {exc}"
                continue
            if response.ok:
                print(f"login OK como {email}")
                return {"Authorization": f"Bearer {response.json()['access_token']}"}
            last = f"{response.status_code}"
            if response.status_code not in (429, 500, 502, 503, 504):
                continue
        if attempt < LOGIN_ATTEMPTS - 1:
            delay = min(30.0, 2 ** attempt * 3.0)
            print(f"[retry] login falló ({last}) · reintento en {delay:.0f}s")
            time.sleep(delay)
    raise _fail(2, f"login falló tras {LOGIN_ATTEMPTS} intentos (último: {last})")


def _case_id(api: str, headers: dict, case_number: str) -> str:
    response = _request("GET", f"{api}/cases", timeout=30, headers=headers)
    if not response.ok:
        raise _fail(2, f"no se pudo listar expedientes: HTTP {response.status_code}")
    payload = response.json()
    items = payload if isinstance(payload, list) else payload.get("items", [])
    for case in items:
        if str(case.get("case_number")) == case_number:
            return str(case["id"])
    raise _fail(2, f"la API no expone el expediente {case_number}")


def ask(api: str, headers: dict, case_id: str, case: dict, timeout: float) -> dict:
    """Crea sesión y envía el mensaje. Devuelve la respuesta COMPLETA o el error."""
    import requests

    question = str(case.get("question") or "")
    try:
        created = _request("POST", f"{api}/cases/{case_id}/chats", timeout=30,
                           headers=headers, json={"title": question[:80]})
    except requests.RequestException as exc:
        return {"error": f"create_session:{exc}", "question": question}
    if created.status_code == 401:
        headers["Authorization"] = _login(api)["Authorization"]
        created = _request("POST", f"{api}/cases/{case_id}/chats", timeout=30,
                           headers=headers, json={"title": question[:80]})
    if not created.ok:
        return {"error": f"create_session:{created.status_code}", "question": question}
    session = created.json()["id"]

    started = time.monotonic()
    try:
        sent = _request("POST", f"{api}/cases/{case_id}/chats/{session}/messages",
                        timeout=timeout, headers=headers, json={"content": question})
    except requests.Timeout:
        return {"error": f"timeout>{int(timeout)}s", "question": question, "session": session,
                "timeout": True, "elapsed": round(time.monotonic() - started, 2)}
    except requests.RequestException as exc:
        return {"error": f"send:{exc}", "question": question, "session": session,
                "elapsed": round(time.monotonic() - started, 2)}
    elapsed = round(time.monotonic() - started, 2)
    if sent.status_code == 401:
        headers["Authorization"] = _login(api)["Authorization"]
        sent = _request("POST", f"{api}/cases/{case_id}/chats/{session}/messages",
                        timeout=timeout, headers=headers, json={"content": question})
        elapsed = round(time.monotonic() - started, 2)
    if not sent.ok:
        return {"error": f"send:{sent.status_code}", "question": question,
                "session": session, "elapsed": elapsed}

    body = sent.json()
    answer = str(body.get("answer") or "")
    return {
        "question": question,
        "session": session,
        "elapsed": elapsed,
        "answer": answer,
        "answer_kind": body.get("answer_kind"),
        "intent": body.get("intent"),
        "degraded": bool(body.get("degraded")),
        "claims": body.get("claims") or [],
        "citations_detail": body.get("citations") or [],
        "evidence_count": body.get("evidence_count"),
        "model_run_id": body.get("model_run_id"),
        "uncertainties": body.get("uncertainties") or [],
        "cached": bool(body.get("cached")),
    }


# -------------------------------------------------------------------------- main
def load_golden(path: Path) -> dict:
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("cases"):
        raise SystemExit(f"[ERROR] set dorado inválido: {path}")
    return data


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Línea base del chat-investigador (F0).")
    parser.add_argument("--api", default=DEFAULT_API, help="Base de la API (con /v1)")
    parser.add_argument("--golden", default=str(GOLDEN_DEFAULT))
    parser.add_argument("--label", default="before", choices=("before", "after"))
    parser.add_argument("--only", default=None, help="Ids separados por coma: INV-01,INV-03")
    parser.add_argument("--out", default=None, help="Ruta del .json (default var/eval/golden_<label>.json)")
    parser.add_argument("--md", default=None, help="Ruta del .md (default: hermano del .json)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                        help="Timeout por mensaje en segundos (default 240)")
    parser.add_argument("--skip-health", action="store_true",
                        help="No verificar /health antes de medir (no recomendado)")
    args = parser.parse_args(argv)

    _load_env()
    golden = load_golden(Path(args.golden))
    cases = list(golden.get("cases") or [])
    case_ref = str(golden.get("case_ref") or "")

    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        cases = [c for c in cases if str(c.get("id")) in wanted]
        if not cases:
            raise _fail(2, f"--only no coincide con ningún caso del set: {args.only}")

    if not args.skip_health:
        _check_health(args.api)

    headers = _login(args.api)
    case_id = _case_id(args.api, headers, case_ref)
    print(f"expediente {case_ref} -> {case_id} · casos: {len(cases)}")

    results: list[dict] = []
    for index, case in enumerate(cases, start=1):
        cid = case.get("id")
        print(f"[{index}/{len(cases)}] {cid} · enviando…")
        result = ask(args.api, headers, case_id, case, args.timeout)
        result.setdefault("question", case.get("question"))
        results.append(result)
        if result.get("error"):
            print(f"    -> {result['error']}")
        else:
            print(f"    -> answer_kind={result.get('answer_kind')} "
                  f"citas={len(result.get('citations_detail') or [])} "
                  f"len={len(result.get('answer') or '')} {result.get('elapsed')}s")

    evaluated = evaluate_cases(cases, results)
    out = Path(args.out) if args.out else _ROOT / "var" / "eval" / f"golden_{args.label}.json"
    if not out.is_absolute():
        out = _ROOT / out
    md = Path(args.md) if args.md else out.with_suffix(".md")
    if not md.is_absolute():
        md = _ROOT / md

    report = {
        "label": args.label,
        "api": args.api,
        "golden": str(Path(args.golden)),
        "case_ref": case_ref,
        "case_id": case_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timeout_s": args.timeout,
        "summary": evaluated["summary"],
        "passed": evaluated["passed"],
        "cases": [dict(entry, must_cite_any=case.get("must_cite_any") or [])
                  for entry, case in zip(evaluated["cases"], cases, strict=False)],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md.write_text(render_markdown(report), encoding="utf-8")

    print("\n== Resumen ==")
    for entry in report["cases"]:
        estado = "PASS" if entry["passed"] else f"FAIL ({', '.join(entry['failed'])})"
        print(f"  {entry['id']}: answer_kind={entry['answer_kind']} "
              f"citas={entry['citations']} len={entry['length']} {estado}")
    print(f"json -> {out}")
    print(f"md   -> {md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
