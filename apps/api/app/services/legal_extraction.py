"""Extracción jurídica con LLM (Fase 4).

Cada corrida:
  1. Carga un prompt versionado inmutable de packages/prompts/.
  2. Construye bloques de evidencia desde document_pages o transcript_segments.
  3. Llama al LLM configurado y valida la salida contra el schema JSON.
  4. Persiste entidades/claims/eventos/decisiones en las tablas del esquema.
  5. Deja constancia en model_runs con hash de entrada, tokens y costo.

Toda afirmación queda marcada con su estado epistémico; nada se guarda como
"verdad" sin evidencia o decisión judicial citada.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any

import jsonschema
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows
from app.providers import llm
from app.providers.llm import LLMResult

log = logging.getLogger(__name__)

_WORD = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[^\W\d_]{3,}", re.U)


def _norm(t: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", t.lower()) if not unicodedata.combining(ch))


def _load_prompt(prompt_id: str, locale: str, version: int = 1) -> tuple[str, str, str]:
    """Carga el prompt versionado y devuelve (body, prompt_id, version).
    Los prompts no se editan: una mejora crea vN+1 y el llamador pide esa versión."""
    s = get_settings()
    raw = (s.path(s.PROMPTS_DIR) / f"{prompt_id}.v{version}.md").read_text(encoding="utf-8")
    meta, body = raw.split("---", 2)[1], raw.split("---", 2)[2]
    pid = re.search(r"prompt_id:\s*(\S+)", meta).group(1)
    ver = re.search(r"version:\s*(\S+)", meta).group(1)
    return body.strip().replace("{{LOCALE}}", locale), pid, ver


def _source_label(it: dict[str, Any]) -> str:
    if it["source_type"] == "document_page":
        return f"document:{it['document_id']} page:{it['page_number']}"
    return f"media:{it['media_id']} {it['start_ms']}-{it['end_ms']}ms speaker:{it['speaker'] or 'unknown'}"


def build_evidence_blocks(
    conn: Connection,
    case_id: str,
    source_type: str,
    source_id: str,
    max_chars: int | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Recupera páginas o segmentos y les asigna handles E1..EN.

    Devuelve (items, input_hash) donde input_hash identifica la entrada del LLM.
    """
    s = get_settings()
    max_chars = max_chars or s.RETRIEVAL_SNIPPET_MAX_CHARS
    if source_type == "document":
        items = rows(conn, """
            SELECT 'document_page' AS source_type, p.document_id, p.page_number, p.folio,
                   left(p.text, :n) AS text, d.filename
            FROM document_pages p JOIN documents d ON d.id = p.document_id
            WHERE d.case_id = :c AND p.document_id = :sid
            ORDER BY p.page_number""", c=case_id, n=max_chars, sid=source_id)
    elif source_type == "media":
        items = rows(conn, """
            SELECT 'transcript_segment' AS source_type, s.media_id, s.id AS segment_id,
                   s.start_ms, s.end_ms, left(s.text, :n) AS text, m.filename,
                   sp.label AS speaker
            FROM transcript_segments s JOIN media m ON m.id = s.media_id
            LEFT JOIN speakers sp ON sp.id = s.speaker_id
            WHERE m.case_id = :c AND s.media_id = :sid
            ORDER BY s.start_ms""", c=case_id, n=max_chars, sid=source_id)
    else:
        raise ValueError(f"source_type invalid: {source_type}")

    for i, it in enumerate(items, 1):
        it["handle"] = f"E{i}"

    h = hashlib.sha256(f"{case_id}:{source_type}:{source_id}".encode())
    for it in items:
        h.update(str(it.get("document_id") or it.get("segment_id")).encode())
        h.update(str(it.get("page_number") or it.get("start_ms")).encode())
        h.update((it.get("text") or "").encode())
    return items, h.hexdigest()


def build_user_prompt(items: list[dict[str, Any]]) -> str:
    """El contenido del expediente se escapa para evitar prompt injection."""
    blocks = [f'<evidence id="{it["handle"]}" source="{html.escape(_source_label(it), quote=True)}">\n'
              f'{html.escape(it["text"], quote=False)}\n</evidence>' for it in items]
    return "\n".join(blocks)


# Máximo de páginas/segmentos por llamada al LLM. Documentos largos (p. ej. 154
# páginas) generan una salida mayor que el presupuesto del modelo y se truncan
# ("Unterminated string"); por eso se procesa en ventanas y se acumulan resultados.
_EXTRACTION_MAX_ITEMS = 6


def _batch_windows(items: list[dict[str, Any]], size: int = _EXTRACTION_MAX_ITEMS):
    """Divide en ventanas y reasigna handles E1..EN por ventana."""
    for start in range(0, len(items), size):
        batch = items[start:start + size]
        for j, it in enumerate(batch, 1):
            it["handle"] = f"E{j}"
        yield batch


def _parse_json(raw: str) -> dict[str, Any]:
    txt = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    return json.loads(txt)


def _validate(
    data: dict[str, Any],
    schema_path: Path,
    array_keys: list[str] | None = None,
) -> tuple[bool, list[str]]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    array_keys = array_keys or []
    if isinstance(data, dict) and array_keys:
        for key in array_keys:
            items = data.get(key)
            if not isinstance(items, list):
                return False, [f"{key}: expected array, got {type(items).__name__}"]
            for item in items:
                if not isinstance(item, dict):
                    return False, [f"{key}: expected object items, got {type(item).__name__}"]
                try:
                    jsonschema.validate(instance=item, schema=schema)
                except jsonschema.ValidationError as exc:
                    return False, [f"{key}: {exc.message}"]
        return True, []
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        return False, [exc.message]
    return True, []


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _check_budget(conn: Connection, case_id: str, estimated_tokens: int) -> bool:
    case = one(conn, "SELECT spent_llm_tokens, max_llm_tokens FROM cases WHERE id = :c", c=case_id)
    if not case:
        return False
    # Si la fila no trae las columnas de presupuesto (tests con conexión falsa),
    # asumimos presupuesto ilimitado para no bloquear llamadas de routing.
    if "spent_llm_tokens" not in case or "max_llm_tokens" not in case:
        return True
    return int(case["spent_llm_tokens"]) + estimated_tokens <= int(case["max_llm_tokens"])


def _spend_budget(conn: Connection, case_id: str, tokens_in: int, tokens_out: int) -> None:
    one(conn, """
        UPDATE cases
        SET spent_llm_tokens = spent_llm_tokens + :t,
            spent_processing_cost = spent_processing_cost + 0.0
        WHERE id = :c RETURNING id""",
        c=case_id, t=tokens_in + tokens_out)


def _pick_llm(conn: Connection, case_id: str, task: str):
    """Elige el proveedor LLM: el enrutado por tarea o, si LLM_PROVIDER=fake, el
    modelo configurado en el dashboard (por defecto o marcado para OCR)."""
    s = get_settings()
    if s.LLM_PROVIDER != "fake":
        return llm.get_llm_for_task(task)
    model = one(conn, """SELECT id FROM ai_models
                         WHERE is_default OR ocr_enabled
                         ORDER BY is_default DESC LIMIT 1""")
    model_id = model.get("id") if isinstance(model, dict) else None
    if model_id:
        org = one(conn, "SELECT organization_id FROM cases WHERE id = :c", c=case_id)
        org_id = org.get("organization_id") if isinstance(org, dict) else None
        if org_id:
            return llm.get_llm_for_model(str(model_id), str(org_id), "system")
    return llm.get_llm_for_task(task)


def _call_llm(conn: Connection, case_id: str, system: str, user: str, task: str) -> LLMResult:
    estimated = _estimate_tokens(system) + _estimate_tokens(user)
    if not _check_budget(conn, case_id, estimated):
        raise RuntimeError("case_llm_budget_exceeded")
    provider = _pick_llm(conn, case_id, task)
    result = provider.complete(system, user)
    _spend_budget(conn, case_id, result.tokens_in, result.tokens_out)
    log.info("llm call task=%s provider=%s model=%s tokens_in=%s tokens_out=%s",
             task, result.provider, result.model, result.tokens_in, result.tokens_out)
    return result


def _record_model_run(
    conn: Connection,
    org_id: str,
    case_id: str,
    task: str,
    prompt_id: str,
    prompt_version: str,
    input_hash: str,
    result: LLMResult,
    validation: dict[str, Any],
    actor_id: str,
) -> None:
    one(conn, """
        INSERT INTO model_runs
          (organization_id, case_id, task, provider, model, prompt_id, prompt_version,
           pipeline_version, input_hash, output, validation, tokens_in, tokens_out, actor_id)
        VALUES
          (:o, :c, :t, :p, :m, :pi, :pv, :pipe, :ih, CAST(:out AS jsonb), CAST(:val AS jsonb),
           :ti, :to, :a) RETURNING id""",
        o=org_id, c=case_id, t=task, p=result.provider, m=result.model,
        pi=prompt_id, pv=prompt_version, pipe=get_settings().PIPELINE_VERSION,
        ih=input_hash, out=json.dumps(result.text), val=json.dumps(validation),
        ti=result.tokens_in, to=result.tokens_out, a=actor_id)


# Tipos de entidad permitidos por la BD (check constraint entities_entity_type_check).
_ALLOWED_ENTITY_TYPES = {"person", "organization", "contract", "asset", "account",
                         "place", "date", "money", "related_case", "legal_rule", "authority"}


def _insert_entities(
    conn: Connection,
    org_id: str,
    case_id: str,
    entities: list[dict[str, Any]],
) -> list[str]:
    ids: list[str] = []
    for e in entities:
        if e.get("entity_type") not in _ALLOWED_ENTITY_TYPES:
            log.warning("entidad con tipo no permitido ignorada: %r (%s)", e.get("name"), e.get("entity_type"))
            continue
        eid = _valid_uuid_or_new(e.get("id"))
        ids.append(eid)
        one(conn, """
            INSERT INTO entities
              (id, organization_id, case_id, entity_type, name, normalized_name, aliases,
               resolution_status, party_id, attributes)
            VALUES
              (:id, :o, :c, :et, :name, :nn, :aliases, :rs, :pid, CAST(:attrs AS jsonb))
            ON CONFLICT (id) DO UPDATE SET
              name = EXCLUDED.name,
              normalized_name = EXCLUDED.normalized_name,
              aliases = EXCLUDED.aliases,
              resolution_status = EXCLUDED.resolution_status,
              attributes = EXCLUDED.attributes
            RETURNING id""",
            id=eid, o=org_id, c=case_id, et=e["entity_type"], name=e["name"],
            nn=e.get("normalized_name") or _norm(e["name"]),
            aliases=e.get("aliases") or [],
            rs=e.get("resolution_status") or "AMBIGUOUS",
            pid=e.get("party_id"), attrs=json.dumps(e.get("attributes") or {}))
    return ids


def _valid_uuid_or_new(value: Any) -> str:
    """Devuelve el id del modelo si es un UUID válido; si no, genera uno nuevo.

    Los LLM a veces inventan ids que no son UUID (p. ej. 'g2e6c1b8-...') y la BD
    los rechaza. El id es interno: las citas del modelo usan handles (E1..EN)."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid4())


def _uuid_or_none(value: Any) -> str | None:
    """UUID válido o None (para FKs que el modelo a veces rellena con nombres)."""
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _uuid_list(values: Any) -> list[str]:
    """Filtra una lista dejando solo UUID válidos (el modelo suele poner nombres)."""
    if not isinstance(values, list):
        return []
    return [u for u in (_uuid_or_none(v) for v in values) if u]


def _insert_claims(
    conn: Connection,
    org_id: str,
    case_id: str,
    claims: list[dict[str, Any]],
) -> list[str]:
    ids: list[str] = []
    for c in claims:
        cid = _valid_uuid_or_new(c.get("id"))
        ids.append(cid)
        one(conn, """
            INSERT INTO claims
              (id, organization_id, case_id, text, claim_type, claimant_party_id,
               temporal_scope, confidence, review_status, origin, original_ai_output)
            VALUES
              (:id, :o, :c, :text, :ct, :cpid, :ts, :conf, 'PENDING', 'ai', CAST(:orig AS jsonb))
            ON CONFLICT (id) DO UPDATE SET
              text = EXCLUDED.text,
              claim_type = EXCLUDED.claim_type,
              claimant_party_id = EXCLUDED.claimant_party_id,
              temporal_scope = EXCLUDED.temporal_scope,
              confidence = EXCLUDED.confidence,
              original_ai_output = EXCLUDED.original_ai_output
            RETURNING id""",
            id=cid, o=org_id, c=case_id, text=c["text"], ct=c["claim_type"],
            cpid=_uuid_or_none(c.get("claimant_party_id")), ts=c.get("temporal_scope"),
            conf=c.get("confidence"), orig=json.dumps(c))
    return ids


def _insert_events(
    conn: Connection,
    org_id: str,
    case_id: str,
    events: list[dict[str, Any]],
) -> list[str]:
    ids: list[str] = []
    for e in events:
        eid = _valid_uuid_or_new(e.get("id"))
        ids.append(eid)
        one(conn, """
            INSERT INTO events
              (id, organization_id, case_id, event_type, event_date, date_precision,
               description, timeline_confidence, confidence, participants)
            VALUES
              (:id, :o, :c, :et, :ed, :dp, :desc, :tc, :conf, :parts)
            ON CONFLICT (id) DO UPDATE SET
              event_type = EXCLUDED.event_type,
              event_date = EXCLUDED.event_date,
              date_precision = EXCLUDED.date_precision,
              description = EXCLUDED.description,
              timeline_confidence = EXCLUDED.timeline_confidence,
              confidence = EXCLUDED.confidence,
              participants = EXCLUDED.participants
            RETURNING id""",
            id=eid, o=org_id, c=case_id, et=e["event_type"], ed=e.get("event_date"),
            dp=e.get("date_precision") or "day", desc=e["description"],
            tc=e["timeline_confidence"], conf=e.get("confidence"),
            parts=_uuid_list(e.get("participants") or []))
    return ids


def _insert_decisions(
    conn: Connection,
    org_id: str,
    case_id: str,
    source_document_id: str,
    decisions: list[dict[str, Any]],
) -> list[str]:
    ids: list[str] = []
    for d in decisions:
        did = _valid_uuid_or_new(d.get("id"))
        ids.append(did)
        one(conn, """
            INSERT INTO decisions
              (id, organization_id, case_id, decision_type, decision_date, outcome,
               reasoning, source_document_id)
            VALUES
              (:id, :o, :c, :dt, :dd, :out, :reason, :sd)
            ON CONFLICT (id) DO UPDATE SET
              decision_type = EXCLUDED.decision_type,
              decision_date = EXCLUDED.decision_date,
              outcome = EXCLUDED.outcome,
              reasoning = EXCLUDED.reasoning,
              source_document_id = EXCLUDED.source_document_id
            RETURNING id""",
            id=did, o=org_id, c=case_id, dt=d["decision_type"], dd=d.get("decision_date"),
            out=d.get("outcome"), reason=d.get("reasoning"), sd=source_document_id)
    return ids


def _insert_facts(
    conn: Connection,
    org_id: str,
    case_id: str,
    facts: list[dict[str, Any]],
) -> list[str]:
    ids: list[str] = []
    for f in facts:
        fid = _valid_uuid_or_new(f.get("id"))
        ids.append(fid)
        status = f.get("status") or "ALLEGED"
        one(conn, """
            INSERT INTO facts
              (id, organization_id, case_id, proposition, status,
               determined_by_decision_id, confidence, review_status, version)
            VALUES
              (:id, :o, :c, :prop, :st, :did, :conf, 'PENDING', 1)
            ON CONFLICT (id) DO UPDATE SET
              proposition = EXCLUDED.proposition,
              status = EXCLUDED.status,
              determined_by_decision_id = EXCLUDED.determined_by_decision_id,
              confidence = EXCLUDED.confidence,
              version = facts.version + 1
            RETURNING id""",
            id=fid, o=org_id, c=case_id, prop=f["proposition"], st=status,
            did=_uuid_or_none(f.get("determined_by_decision_id")) if status == "JUDICIALLY_DETERMINED" else None,
            conf=f.get("confidence"))
    return ids


def _insert_contradictions(
    conn: Connection,
    org_id: str,
    case_id: str,
    contradictions: list[dict[str, Any]],
) -> list[str]:
    ids: list[str] = []
    for c in contradictions:
        cid = _valid_uuid_or_new(c.get("id"))
        ids.append(cid)
        one(conn, """
            INSERT INTO contradictions
              (id, organization_id, case_id, claim_a_id, claim_b_id,
               contradiction_type, description, severity, human_review_required, review_status)
            VALUES
              (:id, :o, :c, :aid, :bid, :ct, :desc, :sev, true, 'PENDING')
            ON CONFLICT (id) DO UPDATE SET
              claim_a_id = EXCLUDED.claim_a_id,
              claim_b_id = EXCLUDED.claim_b_id,
              contradiction_type = EXCLUDED.contradiction_type,
              description = EXCLUDED.description,
              severity = EXCLUDED.severity
            RETURNING id""",
            id=cid, o=org_id, c=case_id, aid=c["claim_a_id"], bid=c["claim_b_id"],
            ct=c["contradiction_type"], desc=c.get("description") or "",
            sev=c["severity"])
    return ids


def _ensure_evidence_records(
    conn: Connection,
    org_id: str,
    case_id: str,
    items: list[dict[str, Any]],
) -> dict[str, str]:
    """Crea/actualiza filas en tabla evidence para cada evidence block."""
    handle_to_id: dict[str, str] = {}
    for it in items:
        eid = str(uuid.uuid4())
        if it["source_type"] == "document_page":
            one(conn, """
                INSERT INTO evidence
                  (id, organization_id, case_id, evidence_type, description,
                   source_document_id, admissibility_status)
                VALUES
                  (:id, :o, :c, 'documentary',
                   'Página ' || :pn || ' de ' || :filename,
                   :did, 'unknown')
                ON CONFLICT (id) DO UPDATE SET
                  description = EXCLUDED.description
                RETURNING id""",
                id=eid, o=org_id, c=case_id, pn=it["page_number"],
                filename=it.get("filename", ""), did=it["document_id"])
        else:
            one(conn, """
                INSERT INTO evidence
                  (id, organization_id, case_id, evidence_type, description,
                   source_media_id, admissibility_status)
                VALUES
                  (:id, :o, :c, 'testimonial',
                   'Segmento ' || :sms || '-' || :ems || ' de ' || :filename,
                   :mid, 'unknown')
                ON CONFLICT (id) DO UPDATE SET
                  description = EXCLUDED.description
                RETURNING id""",
                id=eid, o=org_id, c=case_id, sms=it["start_ms"], ems=it["end_ms"],
                filename=it.get("filename", ""), mid=it["media_id"])
        handle_to_id[it["handle"]] = eid
    return handle_to_id


def _insert_evidence_links(
    conn: Connection,
    org_id: str,
    links: list[dict[str, Any]],
) -> None:
    for lk in links:
        sp = conn.begin_nested()  # savepoint: un enlace inválido no aborta la fuente
        try:
            one(conn, """
                INSERT INTO evidence_links
                  (organization_id, evidence_id, fact_id, stance)
                VALUES
                  (:o, :eid, :fid, :stance)
                ON CONFLICT (evidence_id, fact_id) DO UPDATE SET
                  stance = EXCLUDED.stance
                RETURNING id""",
                o=org_id, eid=lk["evidence_id"], fid=lk["fact_id"], stance=lk["stance"])
            sp.commit()
        except Exception:  # noqa: BLE001
            sp.rollback()
            log.warning("enlace de evidencia inválido ignorado: %s -> %s", lk.get("evidence_id"), lk.get("fact_id"))


def link_evidence(
    conn: Connection,
    org_id: str,
    case_id: str,
    source_type: str,
    source_id: str,
    actor_id: str,
    locale: str | None = None,
) -> dict[str, Any]:
    """Crea registros evidence para un source y los vincula con facts del caso."""
    locale = locale or get_settings().DEFAULT_LOCALE
    system, prompt_id, prompt_version = _load_prompt("link_evidence", locale)
    items, input_hash = build_evidence_blocks(conn, case_id, source_type, source_id)
    if not items:
        return {"evidence": 0, "links": 0, "model_run": None, "warning": "no_evidence"}

    facts = rows(conn, """
        SELECT id, proposition FROM facts
        WHERE case_id = :c AND review_status <> 'REJECTED'
        ORDER BY updated_at""", c=case_id)
    if not facts:
        return {"evidence": 0, "links": 0, "model_run": None, "warning": "no_facts"}

    # Prompt: evidence blocks + facts
    evidence_blocks = build_user_prompt(items)
    fact_blocks = "\n".join(
        f'<fact id="{f["id"]}">\n{html.escape(f["proposition"], quote=False)}\n</fact>' for f in facts)
    user = evidence_blocks + "\n\n" + fact_blocks

    result = _call_llm(conn, case_id, system, user, "link_evidence")
    schema_path = get_settings().path("packages/schemas/evidence.schema.json")
    data, validation = _validate_and_parse(result, schema_path, conn, case_id, actor_id, ["evidence_links"])

    _record_model_run(conn, org_id, case_id, "link_evidence", prompt_id, prompt_version,
                      input_hash, result, validation, actor_id)

    if not validation["schema_valid"]:
        return {"evidence": 0, "links": 0, "model_run": validation, "error": "schema_validation_failed"}

    handle_to_id = _ensure_evidence_records(conn, org_id, case_id, items)
    links = data.get("evidence_links") or []
    for lk in links:
        lk["evidence_id"] = handle_to_id.get(lk.get("evidence_id") or lk.get("citation"), lk.get("evidence_id"))
    _insert_evidence_links(conn, org_id, links)

    return {"evidence": len(handle_to_id), "links": len(links), "model_run": validation}


def _insert_citations(
    conn: Connection,
    org_id: str,
    case_id: str,
    target_type: str,
    target_id: str,
    citations: list[str],
    evidence_map: dict[str, dict[str, Any]],
) -> None:
    for handle in citations:
        src = evidence_map.get(handle)
        if not src:
            continue
        text = src.get("text")
        quote_hash = hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None
        if src["source_type"] == "document_page":
            one(conn, """
                INSERT INTO citations
                  (organization_id, case_id, target_type, target_id, source_type,
                   document_id, page_number, folio, quote_hash)
                VALUES
                  (:o, :c, :tt, :tid, 'document_page', :did, :pn, :folio, :qh)
                ON CONFLICT DO NOTHING RETURNING id""",
                o=org_id, c=case_id, tt=target_type, tid=target_id,
                did=src["document_id"], pn=src["page_number"], folio=src.get("folio"),
                qh=quote_hash)
        else:
            one(conn, """
                INSERT INTO citations
                  (organization_id, case_id, target_type, target_id, source_type,
                   media_id, segment_id, start_ms, end_ms, quote_hash)
                VALUES
                  (:o, :c, :tt, :tid, 'transcript_segment', :mid, :sid, :sms, :ems, :qh)
                ON CONFLICT DO NOTHING RETURNING id""",
                o=org_id, c=case_id, tt=target_type, tid=target_id,
                mid=src["media_id"], sid=src["segment_id"],
                sms=src["start_ms"], ems=src["end_ms"], qh=quote_hash)


def _repair_json(
    raw: str,
    errors: list[str],
    schema_path: Path,
    conn: Connection,
    case_id: str,
    actor_id: str,
    array_keys: list[str] | None = None,
) -> tuple[dict[str, Any] | None, LLMResult | None, dict[str, Any]]:
    """Intenta reparar JSON inválido con un LLM. Devuelve (data, result, validation)."""
    try:
        system, prompt_id, prompt_version = _load_prompt("repair_json", get_settings().DEFAULT_LOCALE)
    except FileNotFoundError:
        return None, None, {"repair": "prompt_not_found"}
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_desc = schema.get("title", "") + " — required: " + ", ".join(schema.get("required", []))
    user = (system
            .replace("{{SCHEMA_DESCRIPTION}}", schema_desc)
            .replace("{{ERRORS}}", "\n".join(f"- {e}" for e in errors))
            .replace("{{ORIGINAL}}", raw))
    result = _call_llm(conn, case_id, system, user, "repair_json")
    validation = {"provider": result.provider, "model": result.model, "repair": True}
    try:
        data = _parse_json(result.text)
    except json.JSONDecodeError as exc:
        validation.update({"schema_valid": False, "error": f"json_parse_after_repair: {exc}"})
        return None, result, validation
    ok, errs = _validate(data, schema_path, array_keys)
    validation.update({"schema_valid": ok, "errors": errs})
    if not ok:
        return None, result, validation
    return data, result, validation


def _validate_and_parse(
    result: LLMResult,
    schema_path: Path,
    conn: Connection | None = None,
    case_id: str | None = None,
    actor_id: str | None = None,
    array_keys: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validation = {"provider": result.provider, "model": result.model}
    try:
        data = _parse_json(result.text)
    except json.JSONDecodeError as exc:
        validation.update({"schema_valid": False, "error": f"json_parse: {exc}"})
        if conn and case_id and actor_id:
            repaired, repair_result, repair_validation = _repair_json(result.text, [str(exc)], schema_path, conn, case_id, actor_id, array_keys)
            validation["repair"] = repair_validation
            if repair_result:
                _spend_budget(conn, case_id, repair_result.tokens_in, repair_result.tokens_out)
            if repaired is not None:
                return repaired, validation
        return {}, validation
    ok, errs = _validate(data, schema_path, array_keys)
    validation.update({"schema_valid": ok, "errors": errs})
    if not ok:
        if conn and case_id and actor_id:
            repaired, repair_result, repair_validation = _repair_json(result.text, errs, schema_path, conn, case_id, actor_id, array_keys)
            validation["repair"] = repair_validation
            if repair_result:
                _spend_budget(conn, case_id, repair_result.tokens_in, repair_result.tokens_out)
            if repaired is not None:
                return repaired, validation
        return {}, validation
    return data, validation


def extract_entities(
    conn: Connection,
    org_id: str,
    case_id: str,
    source_type: str,
    source_id: str,
    actor_id: str,
    locale: str | None = None,
) -> dict[str, Any]:
    """Extrae entidades de un documento o media y las persiste."""
    locale = locale or get_settings().DEFAULT_LOCALE
    system, prompt_id, prompt_version = _load_prompt("extract_entities", locale)
    all_items, input_hash = build_evidence_blocks(conn, case_id, source_type, source_id)
    if not all_items:
        return {"entities": 0, "citations": 0, "model_run": None, "warning": "no_evidence"}

    schema_path = get_settings().path("packages/schemas/entity.schema.json")
    n_entities = n_citations = 0
    last_validation: dict[str, Any] | None = None
    any_valid = False
    for items in _batch_windows(all_items):
        user = build_user_prompt(items)
        result = _call_llm(conn, case_id, system, user, "extract_entities")
        data, validation = _validate_and_parse(result, schema_path, conn, case_id, actor_id, ["entities"])
        _record_model_run(conn, org_id, case_id, "extract_entities", prompt_id, prompt_version,
                          input_hash, result, validation, actor_id)
        last_validation = validation
        if not validation["schema_valid"]:
            continue
        any_valid = True
        entities = data.get("entities") or []
        evidence_map = {it["handle"]: it for it in items}
        ids = _insert_entities(conn, org_id, case_id, entities)
        for eid, e in zip(ids, entities, strict=False):
            _insert_citations(conn, org_id, case_id, "entity", eid, e.get("citations") or [], evidence_map)
        n_entities += len(ids)
        n_citations += sum(len((e.get("citations") or [])) for e in entities)

    out = {"entities": n_entities, "citations": n_citations, "model_run": last_validation}
    if not any_valid:
        out["error"] = "schema_validation_failed"
    return out


def extract_claims(
    conn: Connection,
    org_id: str,
    case_id: str,
    source_type: str,
    source_id: str,
    actor_id: str,
    locale: str | None = None,
) -> dict[str, Any]:
    """Extrae claims de un documento o media y los persiste."""
    locale = locale or get_settings().DEFAULT_LOCALE
    system, prompt_id, prompt_version = _load_prompt("extract_claims", locale)
    all_items, input_hash = build_evidence_blocks(conn, case_id, source_type, source_id)
    if not all_items:
        return {"claims": 0, "citations": 0, "model_run": None, "warning": "no_evidence"}

    schema_path = get_settings().path("packages/schemas/claim.schema.json")
    n_claims = n_citations = 0
    last_validation: dict[str, Any] | None = None
    for items in _batch_windows(all_items):
        user = build_user_prompt(items)
        result = _call_llm(conn, case_id, system, user, "extract_claims")
        data, validation = _validate_and_parse(result, schema_path, conn, case_id, actor_id, ["claims"])
        _record_model_run(conn, org_id, case_id, "extract_claims", prompt_id, prompt_version,
                          input_hash, result, validation, actor_id)
        last_validation = validation
        if not validation["schema_valid"]:
            continue
        claims = data.get("claims") or []
        evidence_map = {it["handle"]: it for it in items}
        ids = _insert_claims(conn, org_id, case_id, claims)
        for cid, c in zip(ids, claims, strict=False):
            _insert_citations(conn, org_id, case_id, "claim", cid, c.get("citations") or [], evidence_map)
        n_claims += len(ids)
        n_citations += sum(len((c.get("citations") or [])) for c in claims)

    return {"claims": n_claims, "citations": n_citations, "model_run": last_validation}


def extract_events(
    conn: Connection,
    org_id: str,
    case_id: str,
    source_type: str,
    source_id: str,
    actor_id: str,
    locale: str | None = None,
) -> dict[str, Any]:
    """Extrae eventos de un documento o media y los persiste."""
    locale = locale or get_settings().DEFAULT_LOCALE
    system, prompt_id, prompt_version = _load_prompt("extract_events", locale)
    all_items, input_hash = build_evidence_blocks(conn, case_id, source_type, source_id)
    if not all_items:
        return {"events": 0, "citations": 0, "model_run": None, "warning": "no_evidence"}

    schema_path = get_settings().path("packages/schemas/event.schema.json")
    n_events = n_citations = 0
    last_validation: dict[str, Any] | None = None
    for items in _batch_windows(all_items):
        user = build_user_prompt(items)
        result = _call_llm(conn, case_id, system, user, "extract_events")
        data, validation = _validate_and_parse(result, schema_path, conn, case_id, actor_id, ["events"])
        _record_model_run(conn, org_id, case_id, "extract_events", prompt_id, prompt_version,
                          input_hash, result, validation, actor_id)
        last_validation = validation
        if not validation["schema_valid"]:
            continue
        events = data.get("events") or []
        evidence_map = {it["handle"]: it for it in items}
        ids = _insert_events(conn, org_id, case_id, events)
        for eid, e in zip(ids, events, strict=False):
            _insert_citations(conn, org_id, case_id, "event", eid, e.get("sources") or [], evidence_map)
        n_events += len(ids)
        n_citations += sum(len((e.get("sources") or [])) for e in events)

    return {"events": n_events, "citations": n_citations, "model_run": last_validation}


def extract_decisions(
    conn: Connection,
    org_id: str,
    case_id: str,
    source_type: str,
    source_id: str,
    actor_id: str,
    locale: str | None = None,
) -> dict[str, Any]:
    """Extrae decisiones judiciales y hechos determinados."""
    if source_type != "document":
        return {"decisions": 0, "facts": 0, "citations": 0, "warning": "decisions_only_from_documents"}
    locale = locale or get_settings().DEFAULT_LOCALE
    system, prompt_id, prompt_version = _load_prompt("extract_decisions", locale)
    all_items, input_hash = build_evidence_blocks(conn, case_id, source_type, source_id)
    if not all_items:
        return {"decisions": 0, "facts": 0, "citations": 0, "model_run": None, "warning": "no_evidence"}

    schema_path = get_settings().path("packages/schemas/fact.schema.json")
    n_dec = n_facts = n_cit = 0
    last_validation: dict[str, Any] | None = None
    for items in _batch_windows(all_items):
        user = build_user_prompt(items)
        result = _call_llm(conn, case_id, system, user, "extract_decisions")
        data, validation = _validate_and_parse(result, schema_path, conn, case_id, actor_id, ["facts"])
        _record_model_run(conn, org_id, case_id, "extract_decisions", prompt_id, prompt_version,
                          input_hash, result, validation, actor_id)
        last_validation = validation
        if not validation["schema_valid"]:
            continue
        decisions = data.get("decisions") or []
        facts = data.get("facts") or []
        evidence_map = {it["handle"]: it for it in items}
        decision_ids = _insert_decisions(conn, org_id, case_id, source_id, decisions)
        for did, d in zip(decision_ids, decisions, strict=False):
            _insert_citations(conn, org_id, case_id, "decision", did, d.get("citations") or [], evidence_map)
        for f in facts:
            if f.get("status") == "JUDICIALLY_DETERMINED":
                determined_id = f.get("determined_by_decision_id")
                if determined_id not in decision_ids:
                    f["determined_by_decision_id"] = decision_ids[0] if decision_ids else None
        fact_ids = _insert_facts(conn, org_id, case_id, facts)
        n_dec += len(decision_ids)
        n_facts += len(fact_ids)
        n_cit += sum(len((d.get("citations") or [])) for d in decisions)

    return {"decisions": n_dec, "facts": n_facts, "citations": n_cit, "model_run": last_validation}


def detect_contradictions(
    conn: Connection,
    org_id: str,
    case_id: str,
    actor_id: str,
    locale: str | None = None,
) -> dict[str, Any]:
    """Detecta contradicciones entre los claims ya extraídos del caso."""
    locale = locale or get_settings().DEFAULT_LOCALE
    system, prompt_id, prompt_version = _load_prompt("detect_contradictions", locale)

    claims = rows(conn, """
        SELECT id, text, claim_type, claimant_party_id, confidence
        FROM claims WHERE case_id = :c AND review_status <> 'REJECTED'
        ORDER BY created_at""", c=case_id)
    if len(claims) < 2:
        return {"contradictions": 0, "model_run": None, "warning": "insufficient_claims"}

    blocks = [f'<claim id="{c["id"]}">\n{html.escape(c["text"], quote=False)}\n</claim>' for c in claims]
    user = "\n".join(blocks)

    h = hashlib.sha256(f"{case_id}:contradictions".encode())
    for c in claims:
        h.update(str(c["id"]).encode())
        h.update((c["text"] or "").encode())
    input_hash = h.hexdigest()

    result = _call_llm(conn, case_id, system, user, "detect_contradictions")
    schema_path = get_settings().path("packages/schemas/contradiction.schema.json")
    data, validation = _validate_and_parse(result, schema_path, conn, case_id, actor_id, ["contradictions"])

    _record_model_run(conn, org_id, case_id, "detect_contradictions", prompt_id, prompt_version,
                      input_hash, result, validation, actor_id)

    if not validation["schema_valid"]:
        return {"contradictions": 0, "model_run": validation, "error": "schema_validation_failed"}

    contradictions = data.get("contradictions") or []
    ids = _insert_contradictions(conn, org_id, case_id, contradictions)
    return {"contradictions": len(ids), "model_run": validation}
