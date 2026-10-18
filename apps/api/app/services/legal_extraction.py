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
from datetime import date
from pathlib import Path
from typing import Any

import jsonschema
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows
from app.domain.procedural_catalog import normalize_subtype
from app.providers import llm
from app.providers.llm import LLMResult
from app.services import procedural_actor_roles as actor_roles

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


def _load_extraction_prompt(conn: Connection, org_id: str | None, prompt_id: str,
                            locale: str, version: int = 1) -> tuple[str, str, str]:
    """Prompt de extracción desde el AGENTE editable del dashboard (si existe) o, si no,
    desde el archivo versionado. Así los prompts se editan en /dashboard/agents."""
    from app.services.builtin_agents import EXTRACTION_AGENTS  # perezoso (evita ciclo)
    agent_name = next((n for (n, pid, _v) in EXTRACTION_AGENTS if pid == prompt_id), None)
    if agent_name and conn is not None and org_id:
        row = one(conn, "SELECT system_prompt, updated_at FROM agents "
                        "WHERE organization_id = :o AND name = :n", o=org_id, n=agent_name)
        if row and row.get("system_prompt"):
            return (str(row["system_prompt"]).replace("{{LOCALE}}", locale),
                    prompt_id, f"agent:{row.get('updated_at')}")
    return _load_prompt(prompt_id, locale, version=version)


def _source_label(it: dict[str, Any]) -> str:
    if it["source_type"] == "document_page":
        return f"document:{it['document_id']} page:{it['page_number']}"
    return f"media:{it['media_id']} {it['start_ms']}-{it['end_ms']}ms speaker:{it['speaker'] or 'unknown'}"


def _source_folder_path(conn: Connection, case_id: str, source_type: str, source_id: str) -> str | None:
    """Devuelve la ruta de carpetas ('A / B / C') de un documento o media."""
    table = "documents" if source_type == "document" else "media" if source_type == "media" else None
    if not table:
        return None
    row = one(conn, f"SELECT folder_id FROM {table} WHERE case_id = :c AND id = :id", c=case_id, id=source_id)
    folder_id = row.get("folder_id") if row else None
    if not folder_id:
        return None
    rows_f = rows(conn, "SELECT id, parent_id, name FROM case_folders WHERE case_id = :c", c=case_id)
    by_id = {str(f["id"]): f for f in rows_f}
    parts: list[str] = []
    cur: str | None = str(folder_id)
    seen: set[str] = set()
    while cur and cur in by_id and cur not in seen:
        seen.add(cur)
        parts.append(by_id[cur]["name"])
        parent = by_id[cur]["parent_id"]
        cur = str(parent) if parent else None
    return " / ".join(reversed(parts)) if parts else None


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
        # Texto efectivo: corrección humana > versión del modo configurado > document_pages actual.
        items = rows(conn, """
            SELECT 'document_page' AS source_type, p.document_id, p.page_number, p.folio,
                   left(CASE
                     WHEN p.human_corrected THEN p.text
                     WHEN d.ocr_mode IN ('basico', 'document_ai') AND v.text IS NOT NULL THEN v.text
                     ELSE p.text
                   END, :n) AS text, d.filename
            FROM document_pages p
            JOIN documents d ON d.id = p.document_id
            LEFT JOIN document_ocr_versions v
              ON v.document_id = p.document_id
             AND v.page_number = p.page_number
             AND v.mode = d.ocr_mode
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


def build_user_prompt(items: list[dict[str, Any]], with_segment: bool = False) -> str:
    """El contenido del expediente se escapa para evitar prompt injection.

    Con ``with_segment`` cada bloque de evidencia incluye el segmento estructural
    (encabezado/cuerpo/decision/anexos) detectado por document_segmentation.
    """
    blocks = []
    for it in items:
        attrs = f'id="{it["handle"]}" source="{html.escape(_source_label(it), quote=True)}"'
        if with_segment and it.get("segment"):
            attrs += f' segment="{html.escape(str(it["segment"]), quote=True)}"'
        blocks.append(f"<evidence {attrs}>\n{html.escape(it['text'], quote=False)}\n</evidence>")
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


def _annotate_segments(items: list[dict[str, Any]]) -> int:
    """Anota cada item con su segmento estructural (si la segmentación está activa).

    La segmentación (encabezado/cuerpo/decisión/anexos) es una capa previa a la
    extracción: le dice al LLM DÓNDE está la decisión, la autoridad y las fechas.
    """
    if not get_settings().DOCUMENT_SEGMENTATION_ENABLED:
        return 0
    from app.services import document_segmentation
    return document_segmentation.annotate_segments(items)


def _extract_json_block(txt: str) -> str | None:
    """Recorta el primer objeto/arreglo JSON balanceado (el modelo suele añadir
    prosa o cercas de código alrededor)."""
    start = next((i for i, ch in enumerate(txt) if ch in "{["), None)
    if start is None:
        return None
    open_ch = txt[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(txt)):
        ch = txt[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return txt[start:i + 1]
    return None


def _parse_json(raw: str) -> dict[str, Any]:
    """Parseo tolerante: muchos modelos (p. ej. Gemini Flash-Lite) devuelven JSON
    casi válido — con cercas, prosa o comas finales — que `json.loads` rechaza."""
    txt = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        return json.loads(txt, strict=False)
    except json.JSONDecodeError:
        pass
    block = _extract_json_block(txt)
    if block is not None and block != txt:
        try:
            return json.loads(block, strict=False)
        except json.JSONDecodeError:
            pass
    # Reparación mecánica segura: comas finales antes de } o ].
    candidate = re.sub(r",\s*([}\]])", r"\1", block if block is not None else txt)
    return json.loads(candidate, strict=False)


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

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Fechas placeholder que los modelos usan cuando no encuentran una fecha real.
_REJECT_DATES = {"1899-12-31", "1899-12-30", "1900-01-01", "1901-01-01",
                 "1969-12-31", "1970-01-01", "0001-01-01", "9999-12-31"}


def _is_year_sentinel(iso: str | None) -> bool:
    """El modelo escribe YYYY-01-01 o YYYY-12-31 cuando solo conoce el AÑO.
    Un acto judicial nunca cae en festivo, así que esos días son centinelas."""
    return bool(iso) and len(iso) >= 10 and iso[5:] in ("01-01", "12-31")


def _safe_date(value: Any) -> str | None:
    """Devuelve una fecha ISO válida (YYYY-MM-DD) o None.

    Rechaza placeholders comunes (1899-12-31, 1900-01-01), fechas futuras y
    fechas imposibles. El modelo a veces devuelve 'unknown'/''/placeholders.
    """
    if isinstance(value, str):
        v = value.strip()
        if _DATE_RE.match(v) and v not in _REJECT_DATES:
            try:
                d = date.fromisoformat(v)
                today = date.today()
                # Rechazar fechas futuras (> 1 año) y años imposibles (>= 1900).
                if 1900 <= d.year <= today.year + 1:
                    return v
            except ValueError:
                pass
    return None


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
        # Dedup EN ORIGEN: si ya existe una entidad con el mismo nombre normalizado en el
        # caso, se reutiliza su id (no se crea otra fila). El tipo se conserva del existente
        # y los nombres alternos se acumulan como alias.
        nn = e.get("normalized_name") or _norm(e["name"])
        with_nn = one(conn, "SELECT id, name FROM entities WHERE case_id = :c AND normalized_name = :nn LIMIT 1",
                      c=case_id, nn=nn)
        eid = str(with_nn["id"]) if with_nn else _valid_uuid_or_new(e.get("id"))
        ids.append(eid)
        src_aliases = set(e.get("aliases") or [])
        if with_nn and with_nn["name"] and with_nn["name"] != e["name"]:
            src_aliases.add(with_nn["name"])
        src_aliases.discard(e["name"])
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
            nn=nn,
            aliases=sorted(src_aliases),
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
    source_type: str | None = None,
    source_id: str | None = None,
) -> list[str]:
    document_date, incorporation_date = _source_dates(conn, case_id, source_type, source_id)
    ids: list[str] = []
    for e in events:
        eid = _valid_uuid_or_new(e.get("id"))
        ids.append(eid)
        one(conn, """
            INSERT INTO events
              (id, organization_id, case_id, event_type, event_date, document_date, incorporation_date,
               date_precision, description, timeline_confidence, confidence, participants)
            VALUES
              (:id, :o, :c, :et, :ed, :docd, :incd, :dp, :desc, :tc, :conf, :parts)
            ON CONFLICT (id) DO UPDATE SET
              event_type = EXCLUDED.event_type,
              event_date = EXCLUDED.event_date,
              document_date = EXCLUDED.document_date,
              incorporation_date = EXCLUDED.incorporation_date,
              date_precision = EXCLUDED.date_precision,
              description = EXCLUDED.description,
              timeline_confidence = EXCLUDED.timeline_confidence,
              confidence = EXCLUDED.confidence,
              participants = EXCLUDED.participants
            RETURNING id""",
            id=eid, o=org_id, c=case_id, et=e["event_type"], ed=_safe_date(e.get("event_date")),
            docd=document_date, incd=incorporation_date,
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
    system, prompt_id, prompt_version = _load_extraction_prompt(conn, org_id, "link_evidence", locale)
    items, input_hash = build_evidence_blocks(conn, case_id, source_type, source_id)
    if not items:
        return {"evidence": 0, "links": 0, "model_run": None, "warning": "no_evidence"}

    # Coste: antes se enviaban TODOS los facts del caso en CADA llamada (O(fuentes×facts);
    # p. ej. 6.4M tokens). Se priorizan los facts SIN evidencia vinculada y se acota el
    # número enviado (config LINK_EVIDENCE_MAX_FACTS).
    facts = rows(conn, """
        SELECT f.id, f.proposition FROM facts f
        WHERE f.case_id = :c AND f.review_status <> 'REJECTED'
        ORDER BY (EXISTS (SELECT 1 FROM evidence_links el WHERE el.fact_id = f.id)) ASC,
                 f.updated_at DESC
        LIMIT :n""", c=case_id, n=get_settings().LINK_EVIDENCE_MAX_FACTS)
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
    # v2: ignora etiquetas de formulario/ruido y exige tipos consistentes (menos duplicados).
    system, prompt_id, prompt_version = _load_extraction_prompt(conn, org_id, "extract_entities", locale, version=2)
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
    # v2: anti-ruido (sin encabezados/formularios) y anti-duplicado (una fila por aserción).
    system, prompt_id, prompt_version = _load_extraction_prompt(conn, org_id, "extract_claims", locale, version=2)
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
    # v2: anti-ruido (sin leyes/fechas de documento/encabezados) y anti-duplicado.
    system, prompt_id, prompt_version = _load_extraction_prompt(conn, org_id, "extract_events", locale, version=2)
    all_items, input_hash = build_evidence_blocks(conn, case_id, source_type, source_id)
    if not all_items:
        return {"events": 0, "citations": 0, "model_run": None, "warning": "no_evidence"}

    schema_path = get_settings().path("packages/schemas/event.schema.json")
    n_events = n_citations = 0
    last_validation: dict[str, Any] | None = None
    _annotate_segments(all_items)
    for items in _batch_windows(all_items):
        user = build_user_prompt(items, with_segment=True)
        result = _call_llm(conn, case_id, system, user, "extract_events")
        data, validation = _validate_and_parse(result, schema_path, conn, case_id, actor_id, ["events"])
        _record_model_run(conn, org_id, case_id, "extract_events", prompt_id, prompt_version,
                          input_hash, result, validation, actor_id)
        last_validation = validation
        if not validation["schema_valid"]:
            continue
        events = data.get("events") or []
        evidence_map = {it["handle"]: it for it in items}
        ids = _insert_events(conn, org_id, case_id, events, source_type, source_id)
        for eid, e in zip(ids, events, strict=False):
            _insert_citations(conn, org_id, case_id, "event", eid, e.get("sources") or [], evidence_map)
        n_events += len(ids)
        n_citations += sum(len((e.get("sources") or [])) for e in events)

    return {"events": n_events, "citations": n_citations, "model_run": last_validation}


def _insert_procedural_events(conn: Connection, org_id: str, case_id: str, document_id: str | None,
                              events: list[dict[str, Any]],
                              fallback_date: str | None = None,
                              source_type: str | None = None,
                              source_id: str | None = None) -> list[str]:
    document_date, incorporation_date = _source_dates(conn, case_id, source_type, source_id)
    ids: list[str] = []
    parties_by_role = actor_roles.parties_by_role(conn, case_id)
    for e in events:
        eid = _valid_uuid_or_new(e.get("id"))
        ids.append(eid)
        # Fallback: si la actuación no trae fecha explícita, usa la fecha del documento.
        event_date = _safe_date(e.get("event_date"))
        date_precision = e.get("date_precision") or "day"
        if not event_date and fallback_date and e.get("date_type") in (None, "actuacion", "documento"):
            event_date = fallback_date
            date_precision = "day"
            if e.get("timeline_confidence") == "source_backed":
                e["timeline_confidence"] = "inferred"
        if not event_date:
            date_precision = "unknown"
        elif _is_year_sentinel(event_date):
            # Solo se conoce el año: no mostrar un día inventado (2025-01-01 -> "2025").
            date_precision = "year"

        raw_subtype = e.get("subtype")
        canonical_subtype = normalize_subtype(raw_subtype)
        if canonical_subtype is None and raw_subtype is not None:
            log.warning("subtype procesal no reconocido: %r (case=%s)", raw_subtype, case_id)
        subtype_raw = raw_subtype if (raw_subtype is not None and canonical_subtype != raw_subtype) else None

        parts: set[str] = set(_uuid_list(e.get("participants") or []))
        parts.update(parties_by_role.get(actor_roles.role_code_for_actor(e.get("actor")), []))

        one(conn, """
            INSERT INTO events
              (id, organization_id, case_id, kind, event_type, subtype, subtype_raw, instance, actor, authority,
               date_type, procedural_effect, document_id, page_number, event_date, document_date,
               incorporation_date, date_precision, description, timeline_confidence, confidence, participants)
            VALUES
              (:id, :o, :c, 'procedural', :et, :st, :st_raw, :inst, :act, :auth, :dt, :eff, :doc, :pg, :ed, :docd,
               :incd, :dp, :desc, :tc, :conf, :parts)
            ON CONFLICT (id) DO UPDATE SET
              kind = 'procedural', event_type = EXCLUDED.event_type, subtype = EXCLUDED.subtype,
              subtype_raw = EXCLUDED.subtype_raw, instance = EXCLUDED.instance, actor = EXCLUDED.actor,
              authority = EXCLUDED.authority, date_type = EXCLUDED.date_type,
              procedural_effect = EXCLUDED.procedural_effect, document_id = EXCLUDED.document_id,
              page_number = EXCLUDED.page_number, event_date = EXCLUDED.event_date,
              document_date = EXCLUDED.document_date, incorporation_date = EXCLUDED.incorporation_date,
              date_precision = EXCLUDED.date_precision, description = EXCLUDED.description,
              timeline_confidence = EXCLUDED.timeline_confidence, confidence = EXCLUDED.confidence,
              participants = EXCLUDED.participants
            RETURNING id""",
            id=eid, o=org_id, c=case_id, et=e.get("event_type") or "otro", st=canonical_subtype,
            st_raw=subtype_raw, inst=e.get("instance"), act=e.get("actor"), auth=e.get("authority"),
            dt=e.get("date_type") or "actuacion", eff=e.get("procedural_effect"),
            doc=document_id, pg=e.get("page_number"), ed=event_date,
            docd=document_date, incd=incorporation_date,
            dp=date_precision, desc=e["description"],
            tc=e.get("timeline_confidence") or "source_backed", conf=e.get("confidence"),
            parts=sorted(parts))
    return ids


def _source_metadata(conn: Connection, case_id: str, source_type: str, source_id: str) -> dict[str, Any]:
    """Metadatos de contexto para la extracción: fecha del documento y ruta de carpetas."""
    out: dict[str, Any] = {"document_date": None, "folder_path": None, "filename": None}
    if source_type == "document":
        row = one(conn, "SELECT filename, document_date FROM documents WHERE case_id = :c AND id = :id",
                  c=case_id, id=source_id)
        if row:
            out["filename"] = row.get("filename")
            out["document_date"] = str(row.get("document_date")) if row.get("document_date") else None
    elif source_type == "media":
        row = one(conn, "SELECT filename FROM media WHERE case_id = :c AND id = :id",
                  c=case_id, id=source_id)
        if row:
            out["filename"] = row.get("filename")
    else:
        return out
    folder_path = _source_folder_path(conn, case_id, source_type, source_id)
    if folder_path:
        out["folder_path"] = folder_path
    return out


def _source_dates(conn: Connection, case_id: str, source_type: str | None,
                  source_id: str | None) -> tuple[str | None, str | None]:
    """Devuelve (document_date, incorporation_date) para un documento o medio.

    document_date: fecha del documento según metadatos; si no existe, fecha de carga.
    incorporation_date: fecha de carga/incorporación del documento o medio.
    """
    if source_type == "document" and source_id:
        row = one(conn, "SELECT document_date, created_at::date AS incorporation_date "
                        "FROM documents WHERE case_id = :c AND id = :id",
                  c=case_id, id=source_id)
        if row:
            inc = str(row["incorporation_date"]) if row.get("incorporation_date") else None
            doc = str(row["document_date"]) if row.get("document_date") else inc
            return doc, inc
    elif source_type == "media" and source_id:
        row = one(conn, "SELECT created_at::date AS incorporation_date FROM media "
                        "WHERE case_id = :c AND id = :id",
                  c=case_id, id=source_id)
        if row:
            inc = str(row["incorporation_date"]) if row.get("incorporation_date") else None
            return None, inc
    return None, None


def _metadata_prompt(metadata: dict[str, Any]) -> str:
    """Bloque de metadatos que se antepone al prompt de extracción."""
    parts: list[str] = []
    if metadata.get("folder_path"):
        parts.append(f'Ubicación en el expediente: "{metadata["folder_path"]}".')
    if metadata.get("document_date"):
        parts.append(f'Fecha del documento: {metadata["document_date"]}.')
    if metadata.get("filename"):
        parts.append(f'Nombre de archivo: {metadata["filename"]}.')
    if not parts:
        return ""
    return "\n".join(["CONTEXTO DEL DOCUMENTO:", *parts, "", "EVIDENCIA:"]) + "\n"


def extract_procedural_events(
    conn: Connection,
    org_id: str,
    case_id: str,
    source_type: str,
    source_id: str,
    actor_id: str,
    locale: str | None = None,
) -> dict[str, Any]:
    """Extrae ACTUACIONES PROCESALES del documento/media y las persiste (kind='procedural')."""
    locale = locale or get_settings().DEFAULT_LOCALE
    # v4: segmentos (encabezado/cuerpo/decisión/anexos) y catálogo canónico de subtipos.
    system, prompt_id, prompt_version = _load_extraction_prompt(conn, org_id, "extract_procedural_events", locale, version=4)
    all_items, input_hash = build_evidence_blocks(conn, case_id, source_type, source_id)
    if not all_items:
        return {"events": 0, "citations": 0, "model_run": None, "warning": "no_evidence"}

    _annotate_segments(all_items)
    document_id = source_id if source_type == "document" else None
    metadata = _source_metadata(conn, case_id, source_type, source_id)
    fallback_date = metadata.get("document_date")
    schema_path = get_settings().path("packages/schemas/procedural_event.schema.json")
    n_events = n_citations = 0
    last_validation: dict[str, Any] | None = None
    for items in _batch_windows(all_items):
        user = _metadata_prompt(metadata) + build_user_prompt(items, with_segment=True)
        result = _call_llm(conn, case_id, system, user, "extract_procedural_events")
        data, validation = _validate_and_parse(result, schema_path, conn, case_id, actor_id, ["events"])
        _record_model_run(conn, org_id, case_id, "extract_procedural_events", prompt_id, prompt_version,
                          input_hash, result, validation, actor_id)
        last_validation = validation
        if not validation["schema_valid"]:
            continue
        events = data.get("events") or []
        evidence_map = {it["handle"]: it for it in items}
        # INVARIANTES (capa 1): una actuación solo se persiste si (a) cita al menos una
        # evidencia real del lote y (b) tiene fecha (propia o del documento). Así no nacen
        # actuaciones "sin fuente" ni "sin fecha" (las que antes llegaban a la línea de tiempo).
        kept: list[dict[str, Any]] = []
        for e in events:
            valid_sources = [h for h in (e.get("sources") or []) if h in evidence_map]
            if not valid_sources:
                log.warning("actuación SIN FUENTE válida, descartada (case=%s): %r",
                            case_id, (e.get("description") or "")[:80])
                continue
            e["sources"] = valid_sources
            if e.get("date_type") in (None, "actuacion") and not (_safe_date(e.get("event_date")) or fallback_date):
                log.warning("actuación SIN FECHA, descartada (case=%s): %r",
                            case_id, (e.get("description") or "")[:80])
                continue
            kept.append(e)
        ids = _insert_procedural_events(conn, org_id, case_id, document_id, kept, fallback_date,
                                        source_type, source_id)
        for eid, e in zip(ids, kept, strict=False):
            _insert_citations(conn, org_id, case_id, "event", eid, e.get("sources") or [], evidence_map)
        n_events += len(ids)
        n_citations += sum(len((e.get("sources") or [])) for e in kept)

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
    system, prompt_id, prompt_version = _load_extraction_prompt(conn, org_id, "extract_decisions", locale)
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
    system, prompt_id, prompt_version = _load_extraction_prompt(conn, org_id, "detect_contradictions", locale)

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
