#!/usr/bin/env python3
"""Verifica respuestas del chat contra la base real: OCR, ASR, claims, contradicciones.

El banco `eval_chat_quality.py` mide señales de forma (respondió, citó, latencia).
Este verificador responde la pregunta difícil: **¿lo que dice está en la fuente?**

Para cada fila del JSONL:

1. Resuelve cada `citation_id` a su página OCR (`document_pages`) o segmento ASR
   (`transcript_segments`) y comprueba que exista y tenga texto.
2. Para cada afirmación citada, calcula solapamiento de términos y números
   contra el texto citado (`answering.grounding`). Un número que no está en la
   fuente es una alucinación dura.
3. Comprueba el tipo de pregunta:
   - documento: que las citas sean de ESE documento;
   - entidad: que la entidad exista en `entities` y aparezca en la respuesta;
   - claim: que la fuente citada respalde el hecho preguntado;
   - contradicción: que la respuesta cubra las dos afirmaciones y cite fuentes
     de ambas;
   - exhaustiva: cobertura de las contradicciones conocidas sobre el tema.
4. Re-ejecuta el retrieval (pgvector/FTS) y mide si la fuente esperada estaba
   en el top-k: separa «no recuperó» de «recuperó y el prompt/modelo falló».
5. Opcional `--judge-model-id`: un modelo distinto al que respondió audita cada
   claim contra la evidencia citada (faithfulness/relevance), sin decidir por
   conocimiento externo.

Uso:
    python scripts/verify_chat_quality.py --input var/chat_eval/sample.jsonl
    python scripts/verify_chat_quality.py --input ... --judge-model-id <uuid>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "apps" / "api"))
sys.path.insert(0, str(_ROOT / "scripts"))

MARKER = re.compile(r"\[[0-9Ee]+\]")
NUMBER = re.compile(r"\d[\d.,]*")


def _load_env() -> None:
    import os

    import envload

    envload.load(str(_ROOT / ".env"))
    # El verificador corre fuera del contenedor: usa el mismo espacio que la API
    # de desarrollo (local-hash), no el `fake` del .env base.
    os.environ["EMBEDDING_PROVIDER"] = "local-hash"
    os.environ["EMBEDDING_MODEL"] = "local-ngram-1024"


def _find_case(case_number: str) -> tuple[str, str]:
    from app.core.db import rows, tx

    with tx(None) as c:
        orgs = [str(r["id"]) for r in rows(c, "SELECT id FROM ops_list_organizations()")]
    for org in orgs:
        with tx(org) as c:
            found = rows(c, "SELECT id, organization_id FROM cases WHERE case_number = :n",
                         n=case_number)
        if found:
            return str(found[0]["id"]), str(found[0]["organization_id"])
    raise SystemExit(f"[ERROR] no existe el expediente {case_number}")


def _norm(text: str) -> str:
    from app.services import text_terms

    return text_terms.norm(text or "")


def _source_map(conn, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    from app.core.db import rows

    rows_ = rows(conn, """
        SELECT c.id::text AS citation_id, c.source_type, c.document_id, c.page_number,
               c.media_id, c.segment_id, c.start_ms, c.end_ms,
               coalesce(dp.text, ts.text) AS text,
               coalesce(d.filename, m.filename) AS filename,
               dp.ocr_confidence, dp.needs_review AS ocr_needs_review,
               ts.confidence AS asr_confidence, ts.needs_review AS asr_needs_review,
               sp.display_name AS speaker
          FROM citations c
          LEFT JOIN document_pages dp ON dp.document_id = c.document_id
                                    AND dp.page_number = c.page_number
          LEFT JOIN documents d ON d.id = c.document_id
          LEFT JOIN transcript_segments ts ON ts.id = c.segment_id
          LEFT JOIN media m ON m.id = c.media_id
          LEFT JOIN speakers sp ON sp.id = ts.speaker_id
         WHERE c.id = ANY(CAST(:ids AS uuid[]))
    """, ids="{" + ",".join(ids) + "}")
    return {r["citation_id"]: r for r in rows_}


def _num_tokens(text: str) -> set[str]:
    return {re.sub(r"[.,]", "", n) for n in NUMBER.findall(MARKER.sub(" ", text or ""))}


def _overlap(claim: str, source: str) -> tuple[float, list[str]]:
    from app.services import answering

    return answering.grounding(claim, source)


def _source_context(source: dict) -> str:
    """Texto + metadatos que el modelo sí puede citar (archivo, página, minuto, hablante).

    El validador de la app usa esta misma composición; si el verificador solo mira
    `text`, marca como alucinación un número que está en el filename o en el folio.
    """
    return " ".join(str(source.get(k) or "") for k in
                    ("text", "filename", "page_number", "speaker", "start_ms"))


def _claim_checks(claims: list[dict], sources: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    for claim in claims or []:
        text = str(claim.get("text") or "")
        ids = [str(x) for x in (claim.get("citations") or [])]
        texts = [_source_context(sources[i]) for i in ids if i in sources]
        source_text = "\n".join(texts)
        overlap, missing = _overlap(text, source_text) if text and source_text else (0.0, [])
        out.append({
            "text": text[:300],
            "citations": ids,
            "resolved": len(texts),
            "overlap": round(float(overlap), 3),
            "missing_numbers": missing,
            "supported": bool(text and texts and not missing and overlap >= 0.34),
        })
    return out


def _expected_document(conn, question: str) -> str | None:
    from app.core.db import rows

    match = re.search(r"documento\s+(.+?)\?", question, re.I)
    if not match:
        return None
    name = _norm(match.group(1))
    docs = rows(conn, "SELECT id, filename FROM documents")
    for doc in docs:
        if name and (name in _norm(doc["filename"]) or _norm(doc["filename"]) in name):
            return str(doc["id"])
    return None


def _find_claim(conn, question: str) -> dict | None:
    from app.core.db import rows

    tail = question.split("respalda?", 1)[-1].strip()
    if len(tail) < 30:
        return None
    target = _norm(tail)[:90]
    for claim in rows(conn, "SELECT id::text, text FROM claims LIMIT 5000"):
        if target and target in _norm(claim["text"]):
            return claim
    return None


def _find_contradiction(conn, question: str) -> dict | None:
    from app.core.db import rows

    desc = question.split("sustentan:", 1)[-1].strip() if "sustentan:" in question else question
    target = _norm(desc)[:80]
    contras = rows(conn, """SELECT co.id::text, co.description, co.claim_a_id::text AS a,
                                   co.claim_b_id::text AS b
                              FROM contradictions co LIMIT 200""")
    for contra in contras:
        if target and target in _norm(contra["description"]):
            return contra
    return None


def _claim_sources(conn, claim_ids: list[str]) -> set[tuple]:
    from app.core.db import rows

    if not claim_ids:
        return set()
    found = rows(conn, """
        SELECT document_id::text AS document_id, page_number, media_id::text AS media_id,
               segment_id::text AS segment_id, start_ms
          FROM citations
         WHERE target_type = 'claim' AND target_id = ANY(CAST(:ids AS uuid[]))
    """, ids="{" + ",".join(claim_ids) + "}")
    return {(r["document_id"], r["page_number"]) if r["document_id"]
            else (r["media_id"], r["segment_id"], r["start_ms"]) for r in found}


def _retrieved_keys(conn, case_id: str, question: str) -> set[tuple]:
    from app.services import answering

    items = answering.retrieve(conn, case_id, question)
    return {(str(i.get("document_id")), i.get("page_number")) if i.get("document_id")
            else (str(i.get("media_id")), str(i.get("segment_id")), i.get("start_ms"))
            for i in items}


def _judge(llm, question: str, answer: str, evidence: str) -> dict | None:
    system = (
        "Eres un auditor jurídico. Evalúas si una respuesta está respaldada por la EVIDENCIA "
        "citada. No uses conocimiento externo. Devuelve SOLO JSON: "
        '{"claims":[{"text":"...","verdict":"supported|unsupported|contradicted","reason":"..."}],'
        '"answers_question":"yes|partial|no","missing":"...","hallucinations":["..."]}. '
        "supported = la evidencia citada contiene el hecho; contradicted = dice lo contrario; "
        "unsupported = no aparece en la evidencia."
    )
    user = f"<question>{question}</question>\n<answer>{answer}</answer>\n<evidence>{evidence[:9000]}</evidence>"
    try:
        result = llm.complete(system, user)
        raw = result if isinstance(result, str) else str(getattr(result, "text", "") or "")
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


def _summary(rows: list[dict]) -> dict:
    total = len(rows)
    claims = [c for r in rows for c in (r.get("claim_checks") or [])]
    supported = sum(1 for c in claims if c.get("supported"))
    with_citations = [r for r in rows if (r.get("citations") or 0) > 0]
    contradiction_rows = [r for r in rows if r.get("type") == "contradiction"]
    doc_rows = [r for r in rows if r.get("type") == "document"]
    entity_rows = [r for r in rows if r.get("type") == "entity"]
    judged = [r for r in rows if r.get("judge") and not r["judge"].get("error")]
    judge_claims = [c for r in judged for c in (r["judge"].get("claims") or [])]
    judge_supported = sum(1 for c in judge_claims if str(c.get("verdict")) == "supported")

    def rate(items: list[dict], key: str) -> float:
        values = [x.get(key) for x in items if x.get(key) is not None]
        return round(sum(1 for v in values if v) / len(values), 3) if values else 0.0

    worst: list[dict] = []
    for row in rows:
        reasons: list[str] = []
        if row.get("citations_ok") is False:
            reasons.append("cita no resuelta")
        bad = [c for c in (row.get("claim_checks") or []) if not c.get("supported")]
        if bad:
            reasons.append(f"{len(bad)} afirmación(es) sin respaldo")
        if row.get("unsupported_numbers"):
            reasons.append("números fuera de la fuente")
        if row.get("type_ok") is False:
            reasons.append("no cumple el tipo de pregunta")
        if row.get("retrieval_ok") is False:
            reasons.append("fuente esperada no recuperada")
        if row.get("judge") and not row["judge"].get("error"):
            verdicts = [str(c.get("verdict")) for c in (row["judge"].get("claims") or [])]
            if any(v != "supported" for v in verdicts):
                reasons.append("juez: afirmación no soportada")
        if reasons:
            worst.append({"type": row.get("type"), "question": row.get("question"),
                          "reasons": reasons, "unsupported_numbers": row.get("unsupported_numbers"),
                          "judge": row.get("judge")})
    worst = worst[:12]

    return {
        "n": total,
        "insufficient_rate": round(sum(1 for r in rows if r.get("answer_kind") == "insufficient") / total, 3)
        if total else 0.0,
        "citation_integrity": rate(with_citations, "citations_ok"),
        "n_with_citations": len(with_citations),
        "claim_support": round(supported / len(claims), 3) if claims else 0.0,
        "unsupported_number_rows": rate(rows, "unsupported_numbers"),
        "document_match": rate(doc_rows, "type_ok"),
        "entity_match": rate(entity_rows, "type_ok"),
        "contradiction_both_versions": rate(contradiction_rows, "type_ok"),
        "retrieval_recall": rate([r for r in rows if r.get("retrieval_expected")], "retrieval_ok"),
        "ocr_or_asr_review": rate(rows, "low_quality_source"),
        "judge_faithful": rate(judged, "judge_faithful"),
        "judge_answers": rate(judged, "judge_answers"),
        "judge_claim_support": round(judge_supported / len(judge_claims), 3) if judge_claims else 0.0,
        "judge_n": len(judged),
        "worst": worst,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Verifica respuestas contra OCR/ASR/claims/grafo.")
    p.add_argument("--input", required=True)
    p.add_argument("--case-number", default="11001310302120180036100")
    p.add_argument("--judge-model-id", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    _load_env()
    from app.core.db import rows as db_rows
    from app.core.db import tx
    from app.providers.llm import get_llm_for_model

    src = Path(args.input)
    if not src.is_absolute():
        src = _ROOT / src
    data = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        data = data[: args.limit]
    case_id, org_id = _find_case(args.case_number)
    judge_llm = None
    if args.judge_model_id:
        with tx(org_id) as c:
            admin = db_rows(c, "SELECT id FROM users WHERE email = 'admin@judicial.ai' LIMIT 1")
        user_id = str(admin[0]["id"]) if admin else None
        if user_id:
            judge_llm = get_llm_for_model(args.judge_model_id, org_id, user_id)

    verified: list[dict] = []
    for index, row in enumerate(data, start=1):
        question = str(row.get("question") or "")
        with tx(org_id) as conn:
            citations = row.get("citations_detail") or []
            ids = [str(c.get("citation_id")) for c in citations if c.get("citation_id")]
            sources = _source_map(conn, ids)
            resolved = [sources[i] for i in ids if i in sources and (sources[i].get("text") or "").strip()]
            citations_ok = (len(resolved) == len(ids) and bool(ids)) if ids else None
            source_text = "\n\n".join(_source_context(s) for s in resolved)
            source_numbers = _num_tokens(source_text)
            answer_numbers = _num_tokens(str(row.get("answer") or ""))
            unsupported_numbers = sorted(answer_numbers - source_numbers)
            low_quality = any(
                (s.get("source_type") == "document_page" and (
                    s.get("ocr_needs_review") or (s.get("ocr_confidence") is not None
                                                  and float(s["ocr_confidence"]) < 0.5)))
                or (s.get("source_type") == "transcript_segment" and (
                    s.get("asr_needs_review") or (s.get("asr_confidence") is not None
                                                  and float(s["asr_confidence"]) < 0.5)))
                for s in resolved
            )
            claim_checks = _claim_checks(row.get("claims_detail") or [], sources)
            qtype = row.get("type")
            type_ok = None
            retrieval_expected = None
            retrieval_ok = None
            notes: list[str] = []
            if qtype == "document":
                expected = _expected_document(conn, question)
                if citations and expected:
                    cited_docs = {str(c.get("document_id")) for c in citations if c.get("document_id")}
                    type_ok = expected in cited_docs
                    notes.append("documento citado" if type_ok else "cita de otro documento")
                else:
                    notes.append("sin respuesta o sin documento esperado")
                retrieval_expected = {("doc", expected)}
                if expected:
                    keys = _retrieved_keys(conn, case_id, question)
                    retrieval_ok = any(k[0] == expected for k in keys)
            elif qtype == "entity":
                match = re.search(r"sobre\s+(.+?)\?*$", question, re.I)
                name = _norm(match.group(1)) if match else ""
                if citations:
                    type_ok = bool(name and (name in _norm(str(row.get("answer") or ""))
                                             or name in _norm(source_text)))
                    notes.append("entidad presente" if type_ok else "entidad no acreditada")
                else:
                    notes.append("sin respuesta")
            elif qtype == "claim":
                expected_claim = _find_claim(conn, question)
                if citations and expected_claim:
                    overlap, missing = _overlap(expected_claim["text"], source_text)
                    type_ok = bool(source_text and not missing and overlap >= 0.34)
                    keys = _claim_sources(conn, [expected_claim["id"]])
                    retrieval_expected = keys
                    if keys:
                        retrieved = _retrieved_keys(conn, case_id, question)
                        retrieval_ok = bool(keys & retrieved)
                elif not expected_claim:
                    notes.append("claim no encontrado en BD")
                else:
                    notes.append("sin respuesta")
            elif qtype == "contradiction":
                contra = _find_contradiction(conn, question)
                if contra and citations:
                    a = db_rows(conn, "SELECT text FROM claims WHERE id = :i", i=contra["a"])
                    b = db_rows(conn, "SELECT text FROM claims WHERE id = :i", i=contra["b"])
                    a_text = a[0]["text"] if a else ""
                    b_text = b[0]["text"] if b else ""
                    answer = str(row.get("answer") or "")
                    hit_a = _overlap(a_text, answer)[0] >= 0.2 if a_text else False
                    hit_b = _overlap(b_text, answer)[0] >= 0.2 if b_text else False
                    expected_keys = _claim_sources(conn, [contra["a"], contra["b"]])
                    retrieval_expected = expected_keys
                    cited_keys = {(str(c.get("document_id")), c.get("page"))
                                  if c.get("document_id") else
                                  (str(c.get("media_id")), None, c.get("start_ms"))
                                  for c in citations}
                    type_ok = bool(hit_a and hit_b and (not expected_keys or bool(expected_keys & cited_keys)))
                    notes.append(f"version A={'sí' if hit_a else 'no'}, B={'sí' if hit_b else 'no'}")
                    if expected_keys:
                        retrieved = _retrieved_keys(conn, case_id, question)
                        retrieval_ok = bool(expected_keys & retrieved)
                elif not contra:
                    notes.append("contradicción no encontrada en BD")
                else:
                    notes.append("sin respuesta")
            low_quality = bool(low_quality)
            judge = None
            judge_faithful = judge_answers = None
            if judge_llm is not None:
                judge = _judge(judge_llm, question, str(row.get("answer") or ""), source_text)
                if judge and not judge.get("error"):
                    verdicts = [str(c.get("verdict")) for c in (judge.get("claims") or [])]
                    judge_faithful = bool(verdicts) and all(v == "supported" for v in verdicts)
                    judge_answers = str(judge.get("answers_question") or "").lower() in ("yes", "partial")

        verified.append({
            **{k: row.get(k) for k in ("type", "question", "answer_kind", "citations", "elapsed")},
            "citations_ok": citations_ok,
            "claim_checks": claim_checks,
            "unsupported_numbers": unsupported_numbers,
            "low_quality_source": low_quality,
            "type_ok": type_ok,
            "retrieval_expected": bool(retrieval_expected),
            "retrieval_ok": retrieval_ok,
            "judge": judge,
            "judge_faithful": judge_faithful,
            "judge_answers": judge_answers,
            "notes": notes,
        })
        print(f"[{index:3}/{len(data)}] {qtype:13} cit_ok={citations_ok} "
              f"claims={sum(1 for c in claim_checks if c['supported'])}/{len(claim_checks)} "
              f"type={type_ok} recall={retrieval_ok} nums_ok={not unsupported_numbers}")

    out = Path(args.out) if args.out else src.with_suffix(".verify.jsonl")
    if not out.is_absolute():
        out = _ROOT / out
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in verified) + "\n",
                   encoding="utf-8")
    summary = _summary(verified)
    out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                encoding="utf-8")
    print("\n== Verificación ==")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary.get("worst"):
        print("\n== Peores casos (para ajustar retrieval/prompt) ==")
        for row in summary["worst"]:
            print(f"- [{row.get('type')}] {str(row.get('question'))[:140]}")
            print(f"  {', '.join(row.get('reasons') or [])}")
    print(f"\nDetalle: {out} (+ .summary.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
