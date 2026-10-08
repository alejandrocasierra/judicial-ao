"""Tools de ESCRITURA: corrección humana de OCR/ASR desde el chat.

Por defecto el agente APLICA la corrección directamente en el mismo turno:
llama la tool con `confirm=true` → se aplica el cambio y se propaga:
BD (human_corrected) → reviews (salida original preservada) → lexicón →
pgvector (reindex) → knowledge graph.

La vista previa (`confirm` ausente) sigue disponible para diagnóstico, pero el
flujo conversacional ya no la exige.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.services import audit, indexing, ocr_confidence, ocr_lexicon
from app.services import speakers as speakers_service
from app.services.case_tools import ToolContext, evidence_item, register
from app.services.case_tools.read import _err, _uuid
from app.workers.dispatcher import create_job, enqueue_if_pending
from app.workers.handlers.file_ingest import enqueue_graph_refresh

log = logging.getLogger(__name__)


def _preview(text: str, **kwargs: Any) -> list[dict[str, Any]]:
    return [evidence_item("W", "correction_preview", text, requires_confirmation=True, **kwargs)]


def _need_identity(ctx: ToolContext) -> list[dict[str, Any]] | None:
    if not ctx.org_id or not ctx.actor_id:
        return _err("No hay identidad de usuario para registrar la corrección (falta org/actor en el contexto)")
    return None


@register("correct_ocr_page",
          "Corrige el texto OCR de una página y lo APLICA directamente: llama esta tool con confirm=true en el "
          "mismo turno, SIN pedirle confirmación al usuario. Propaga a la BD, pgvector y el grafo (registro en "
          "reviews). Sin confirm sólo devuelve una vista previa (diagnóstico).",
          {"document_id": {"type": "string", "format": "uuid"},
           "page_number": {"type": "integer"},
           "new_text": {"type": "string", "description": "Texto corregido completo de la página"},
           "reason": {"type": "string", "description": "Por qué se corrige (lo dijo el usuario)"},
           "confirm": {"type": "boolean", "default": False,
                       "description": "pasa true para aplicar la corrección directamente"}},
          kind="write")
def correct_ocr_page(conn: Connection, case_id: str, ctx: ToolContext, document_id: str, page_number: int,
                     new_text: str, reason: str = "", confirm: bool = False) -> list[dict[str, Any]]:
    doc_id = _uuid(document_id)
    if not doc_id:
        return _err("document_id inválido")
    if not new_text or not new_text.strip():
        return _err("new_text no puede estar vacío")
    page = one(conn, """SELECT p.id, p.text, p.ocr_confidence, d.filename FROM document_pages p
        JOIN documents d ON d.id = p.document_id
        WHERE p.document_id = :d AND p.page_number = :n AND d.case_id = :c""",
               d=doc_id, n=page_number, c=case_id)
    if not page:
        return _err("Página no encontrada en este expediente")
    old_text = page["text"] or ""
    if old_text.strip() == new_text.strip():
        return _err("El texto propuesto es idéntico al actual; no hay nada que corregir")
    if not confirm:
        return _preview(
            f"Corrección propuesta para «{page['filename']}» página {page_number}.\n"
            f"--- TEXTO ACTUAL (OCR) ---\n{old_text[:800]}\n--- TEXTO CORREGIDO ---\n{new_text[:800]}\n"
            "Pide al usuario que confirme; si confirma, llama esta tool con confirm=true.",
            document_id=doc_id, page_number=page_number, filename=page["filename"],
            old_text=old_text, new_text=new_text)
    identity_error = _need_identity(ctx)
    if identity_error:
        return identity_error
    new_conf = ocr_confidence.confidence_after_edit(page.get("ocr_confidence"), old_text, new_text)
    updated = one(conn, """UPDATE document_pages
        SET text = :t, ocr_confidence = :conf, needs_review = false,
            human_corrected = true, corrected_at = now(), corrected_by = :u
        WHERE id = :pid RETURNING id""", t=new_text, conf=new_conf, u=ctx.actor_id, pid=str(page["id"]))
    if not updated:
        return _err("No se pudo aplicar la corrección (la página cambió)")
    one(conn, """INSERT INTO reviews (organization_id, case_id, entity_type, entity_id, action,
                 reviewer_id, reason, original_output, human_output)
        VALUES (:o, :c, 'document_page', :e, 'EDIT', :r, :reason,
                CAST(:orig AS jsonb), CAST(:human AS jsonb)) RETURNING id""",
        o=ctx.org_id, c=case_id, e=str(page["id"]), r=ctx.actor_id,
        reason=reason or "Corrección OCR desde el chat", orig=json.dumps({"text": old_text}),
        human=json.dumps({"text": new_text}))
    learned = ocr_lexicon.learn_terms(conn, ctx.org_id, new_text)
    audit.record(conn, org_id=ctx.org_id, actor_id=ctx.actor_id, action="document.page_corrected",
                 entity_type="document", entity_id=doc_id,
                 after={"page": page_number, "learned_terms": learned, "via": "chat_tool"})
    # Propagación diferida a post-commit: reindexar/encolar en otra conexión mientras
    # esta transacción sigue abierta se auto-bloquearía (row locks + advisory de audit).
    ctx.post_commit.append(lambda: _reindex_document(ctx, case_id, doc_id))
    ctx.post_commit.append(lambda: _enqueue_graph(ctx, case_id))
    return [evidence_item("W", "correction_result",
                          f"✅ Corregida la página {page_number} de «{page['filename']}». "
                          f"Actualizado en la BD; al cerrar la operación se reindexa en pgvector y "
                          f"se encola la reconstrucción del grafo. Términos aprendidos en el lexicón: {learned}.",
                          document_id=doc_id, page_number=page_number, filename=page["filename"],
                          learned_terms=learned, reindex_scheduled=True, graph_refresh_scheduled=True)]


@register("correct_transcript_segment",
          "Corrige el texto de un segmento de transcripción (ASR) y lo APLICA directamente: llamada con "
          "confirm=true en el mismo turno, SIN pedir confirmación al usuario. Propaga a la BD, pgvector y el "
          "grafo (reviews). Sin confirm sólo devuelve vista previa.",
          {"media_id": {"type": "string", "format": "uuid"},
           "segment_id": {"type": "string", "format": "uuid"},
           "new_text": {"type": "string", "description": "Texto corregido del segmento"},
           "reason": {"type": "string"},
           "confirm": {"type": "boolean", "default": False,
                       "description": "pasa true para aplicar la corrección directamente"}},
          kind="write")
def correct_transcript_segment(conn: Connection, case_id: str, ctx: ToolContext, media_id: str, segment_id: str,
                               new_text: str, reason: str = "", confirm: bool = False) -> list[dict[str, Any]]:
    mid, sid = _uuid(media_id), _uuid(segment_id)
    if not mid or not sid:
        return _err("media_id o segment_id inválido")
    if not new_text or not new_text.strip():
        return _err("new_text no puede estar vacío")
    seg = one(conn, """SELECT s.id, s.text, s.start_ms, m.filename FROM transcript_segments s
        JOIN media m ON m.id = s.media_id
        WHERE s.id = :s AND s.media_id = :m AND m.case_id = :c""", s=sid, m=mid, c=case_id)
    if not seg:
        return _err("Segmento no encontrado en este expediente")
    old_text = seg["text"] or ""
    if old_text.strip() == new_text.strip():
        return _err("El texto propuesto es idéntico al actual; no hay nada que corregir")
    if not confirm:
        return _preview(
            f"Corrección propuesta para «{seg['filename']}» (segmento {sid}).\n"
            f"--- TEXTO ACTUAL (ASR) ---\n{old_text[:800]}\n--- TEXTO CORREGIDO ---\n{new_text[:800]}\n"
            "Pide al usuario que confirme; si confirma, llama esta tool con confirm=true.",
            media_id=mid, segment_id=sid, filename=seg["filename"], old_text=old_text, new_text=new_text)
    identity_error = _need_identity(ctx)
    if identity_error:
        return identity_error
    updated = one(conn, """UPDATE transcript_segments SET text = :t, confidence = 1.0, needs_review = false
        WHERE id = :s RETURNING id""", t=new_text, s=sid)
    if not updated:
        return _err("No se pudo aplicar la corrección (el segmento cambió)")
    one(conn, """INSERT INTO reviews (organization_id, case_id, entity_type, entity_id, action,
                 reviewer_id, reason, original_output, human_output)
        VALUES (:o, :c, 'transcript_segment', :e, 'EDIT', :r, :reason,
                CAST(:orig AS jsonb), CAST(:human AS jsonb)) RETURNING id""",
        o=ctx.org_id, c=case_id, e=sid, r=ctx.actor_id,
        reason=reason or "Corrección ASR desde el chat", orig=json.dumps({"text": old_text}),
        human=json.dumps({"text": new_text}))
    learned = ocr_lexicon.learn_terms(conn, ctx.org_id, new_text)
    audit.record(conn, org_id=ctx.org_id, actor_id=ctx.actor_id, action="media.segment_corrected",
                 entity_type="media", entity_id=mid,
                 after={"segment": sid, "learned_terms": learned, "via": "chat_tool"})
    ctx.post_commit.append(lambda: _reindex_media(ctx, case_id, mid))
    ctx.post_commit.append(lambda: _enqueue_graph(ctx, case_id))
    return [evidence_item("W", "correction_result",
                          f"✅ Corregido el segmento de «{seg['filename']}». "
                          f"Actualizado en la BD; al cerrar la operación se reindexa en pgvector y "
                          f"se encola la reconstrucción del grafo. Términos aprendidos en el lexicón: {learned}.",
                          media_id=mid, segment_id=sid, filename=seg["filename"],
                          learned_terms=learned, reindex_scheduled=True, graph_refresh_scheduled=True)]


@register("rename_speaker",
          "Renombra a un hablante (diarización) en TODA la transcripción cambiando su nombre visible. "
          "Úsalo cuando el usuario diga el nombre correcto de un hablante ('el juez es X', 'no se llama "
          "así, es…'). APLICA directamente: llamada con confirm=true en el mismo turno, SIN pedir "
          "confirmación al usuario. Propaga a la BD, pgvector y el grafo (reviews). Sin confirm sólo "
          "devuelve vista previa. Si no sabes el speaker_id, usa list_speakers.",
          {"speaker_id": {"type": "string", "format": "uuid"},
           "new_name": {"type": "string", "description": "Nombre correcto del hablante"},
           "reason": {"type": "string"},
           "confirm": {"type": "boolean", "default": False}},
          kind="write")
def rename_speaker(conn: Connection, case_id: str, ctx: ToolContext, speaker_id: str,
                   new_name: str, reason: str = "", confirm: bool = False) -> list[dict[str, Any]]:
    sid = _uuid(speaker_id)
    if not sid:
        return _err("speaker_id inválido")
    if not new_name or not new_name.strip():
        return _err("new_name no puede estar vacío")
    spk = one(conn, """SELECT s.id, s.label, s.display_name FROM speakers s
        WHERE s.id = :s AND s.case_id = :c""", s=sid, c=case_id)
    if not spk:
        return _err("Hablante no encontrado en este expediente")
    old_name = spk["display_name"] or spk["label"]
    if old_name.strip() == new_name.strip():
        return _err("El nombre propuesto es idéntico al actual; no hay nada que cambiar")
    if not confirm:
        return _preview(
            f"Renombrar al hablante «{spk['label']}» ({old_name}) a «{new_name}» en toda la transcripción.",
            speaker_id=sid, label=spk["label"], old_name=old_name, new_name=new_name)
    identity_error = _need_identity(ctx)
    if identity_error:
        return identity_error
    one(conn, "UPDATE speakers SET display_name = :n, resolution_status = 'CONFIRMED', "
              "resolution_source = 'human', version = version + 1 WHERE id = :s RETURNING id",
        n=new_name.strip(), s=sid)
    one(conn, """INSERT INTO reviews (organization_id, case_id, entity_type, entity_id, action,
                 reviewer_id, reason, original_output, human_output)
        VALUES (:o, :c, 'speaker', :e, 'EDIT', :r, :reason, CAST(:orig AS jsonb), CAST(:human AS jsonb))
        RETURNING id""",
        o=ctx.org_id, c=case_id, e=sid, r=ctx.actor_id,
        reason=reason or "Renombrado de hablante desde el chat",
        orig=json.dumps({"display_name": old_name}), human=json.dumps({"display_name": new_name.strip()}))
    audit.record(conn, org_id=ctx.org_id, actor_id=ctx.actor_id, action="speaker.renamed",
                 entity_type="speaker", entity_id=sid,
                 after={"display_name": new_name.strip(), "via": "chat_tool"})
    ctx.post_commit.append(lambda: _reindex_speaker_media(ctx, case_id, sid))
    ctx.post_commit.append(lambda: _enqueue_graph(ctx, case_id))
    return [evidence_item("W", "correction_result",
                          f"✅ Hablante «{spk['label']}» renombrado a «{new_name}». Actualizado en la BD; "
                          "se reindexan sus audios en pgvector y se encola la reconstrucción del grafo.",
                          speaker_id=sid, old_name=old_name, new_name=new_name,
                          reindex_scheduled=True, graph_refresh_scheduled=True)]


@register("merge_speakers",
          "Fusiona DOS hablantes que en realidad son la misma persona (la diarización los separó en dos). "
          "Reasigna TODOS los segmentos del hablante 'merge_speaker_id' al hablante 'keep_speaker_id' y borra el "
          "duplicado. Úsalo cuando el usuario diga p. ej. 'SPEAKER_01 y SPEAKER_03 son la misma persona' o 'el mismo "
          "hablante aparece dos veces'. APLICA directamente: llamada con confirm=true en el mismo turno, SIN pedir "
          "confirmación. Propaga a la BD, pgvector (reindexa los audios) y el grafo (reviews). Sin confirm sólo "
          "devuelve vista previa. Usa list_speakers para obtener los speaker_id (label, nombre y nº de segmentos).",
          {"keep_speaker_id": {"type": "string", "format": "uuid", "description": "Hablante que se CONSERVA"},
           "merge_speaker_id": {"type": "string", "format": "uuid", "description": "Hablante DUPLICADO que se elimina"},
           "reason": {"type": "string"},
           "confirm": {"type": "boolean", "default": False}},
          kind="write")
def merge_speakers(conn: Connection, case_id: str, ctx: ToolContext, keep_speaker_id: str,
                   merge_speaker_id: str, reason: str = "", confirm: bool = False) -> list[dict[str, Any]]:
    keep = _uuid(keep_speaker_id)
    merge = _uuid(merge_speaker_id)
    if not keep or not merge:
        return _err("speaker_id inválido")
    if keep == merge:
        return _err("No se puede fusionar un hablante consigo mismo")
    k = one(conn, "SELECT id, label, display_name FROM speakers WHERE id = :i AND case_id = :c", i=keep, c=case_id)
    m = one(conn, "SELECT id, label, display_name FROM speakers WHERE id = :i AND case_id = :c", i=merge, c=case_id)
    if not k or not m:
        return _err("Hablante no encontrado en este expediente")
    cnt = one(conn, "SELECT count(*) AS n FROM transcript_segments WHERE speaker_id = :s", s=merge)["n"]
    kname = k["display_name"] or k["label"]
    mname = m["display_name"] or m["label"]
    if not confirm:
        return _preview(
            f"Fusionar «{mname}» ({m['label']}, {cnt} segmentos) en «{kname}» ({k['label']}): los segmentos pasarán "
            f"a {k['label']} y se borrará el duplicado.",
            keep_speaker_id=keep, merge_speaker_id=merge, moved_segments=cnt)
    identity_error = _need_identity(ctx)
    if identity_error:
        return identity_error
    media_ids = speakers_service.merge(conn, case_id, keep, merge)
    one(conn, """INSERT INTO reviews (organization_id, case_id, entity_type, entity_id, action,
                 reviewer_id, reason, original_output, human_output)
        VALUES (:o, :c, 'speaker', :e, 'EDIT', :r, :reason, CAST(:orig AS jsonb), CAST(:human AS jsonb))
        RETURNING id""",
        o=ctx.org_id, c=case_id, e=keep, r=ctx.actor_id,
        reason=reason or "Fusión de hablantes desde el chat",
        orig=json.dumps({"merged_speaker_id": merge, "merged_label": m["label"]}),
        human=json.dumps({"keep_speaker_id": keep, "keep_label": k["label"]}))
    audit.record(conn, org_id=ctx.org_id, actor_id=ctx.actor_id, action="speaker.merged", entity_type="speaker",
                 entity_id=keep, after={"merged_id": merge, "merged_label": m["label"], "moved_segments": cnt,
                                        "media_affected": len(media_ids), "via": "chat_tool"})
    ctx.post_commit.append(lambda: _reindex_speaker_media(ctx, case_id, keep))
    ctx.post_commit.append(lambda: _enqueue_graph(ctx, case_id))
    return [evidence_item("W", "correction_result",
                          f"✅ Fusionado «{mname}» en «{kname}»: {cnt} segmentos reasignados y el duplicado eliminado. "
                          "Se reindexan los audios en pgvector y se encola la reconstrucción del grafo.",
                          keep_speaker_id=keep, merge_speaker_id=merge, moved_segments=cnt,
                          reindex_scheduled=True, graph_refresh_scheduled=True)]


@register("suggest_reprocess",
          "Diagnostica la calidad del OCR/ASR de un archivo ('¿cómo podemos mejorar este OCR?'): "
          "confianza por motor, páginas/segmentos marcados para revisión y recomendación. "
          "Con confirm=true encola el reproceso con el modo indicado.",
          {"document_id": {"type": "string", "format": "uuid"},
           "media_id": {"type": "string", "format": "uuid"},
           "mode": {"type": "string", "enum": ["basico", "document_ai"],
                    "description": "Modo destino al confirmar el reproceso de un documento"},
           "confirm": {"type": "boolean", "default": False}},
          kind="write")
def suggest_reprocess(conn: Connection, case_id: str, ctx: ToolContext, document_id: str | None = None,
                      media_id: str | None = None, mode: str | None = None,
                      confirm: bool = False) -> list[dict[str, Any]]:
    if not document_id and not media_id:
        return _err("Se requiere document_id o media_id")
    if document_id:
        return _document_diagnostic(conn, case_id, ctx, document_id, mode, confirm)
    return _media_diagnostic(conn, case_id, ctx, media_id, confirm)


def _document_diagnostic(conn: Connection, case_id: str, ctx: ToolContext, document_id: str,
                         mode: str | None, confirm: bool) -> list[dict[str, Any]]:
    doc_id = _uuid(document_id)
    if not doc_id:
        return _err("document_id inválido")
    doc = one(conn, "SELECT id, filename, ocr_mode, processing_status, page_count FROM documents WHERE id = :d AND case_id = :c",
              d=doc_id, c=case_id)
    if not doc:
        return _err("Documento no encontrado en este expediente")
    stats = one(conn, """SELECT count(*) AS pages, avg(p.ocr_confidence) AS avg_conf,
        count(*) FILTER (WHERE p.needs_review) AS needs_review,
        count(*) FILTER (WHERE p.human_corrected) AS human_corrected
        FROM document_pages p WHERE p.document_id = :d""", d=doc_id)
    versions = rows(conn, """SELECT mode, count(*) AS pages, avg(ocr_confidence) AS avg_conf
        FROM document_ocr_versions WHERE document_id = :d GROUP BY mode""", d=doc_id)
    avg_conf = round(float(stats["avg_conf"] or 0), 3)
    current = doc["ocr_mode"] or "basico"
    other = "document_ai" if current == "basico" else "basico"
    available = {v["mode"] for v in versions}
    if stats["needs_review"]:
        recommendation = (f"Hay {stats['needs_review']} página(s) marcadas para revisión. "
                          f"Opciones: (a) corregirlas manualmente con correct_ocr_page, "
                          f"o (b) reprocesar con el motor '{other}'.")
    elif other not in available:
        recommendation = (f"Confianza media {avg_conf} con el motor '{current}'. "
                          f"Se puede comparar reprocesando con '{other}'.")
    else:
        recommendation = f"Confianza media {avg_conf}; ya se procesó con ambos motores. La corrección manual es la vía."
    diag = (f"Diagnóstico de «{doc['filename']}»: {stats['pages']} páginas, confianza media {avg_conf}, "
            f"{stats['needs_review']} para revisión, {stats['human_corrected']} corregidas por humanos, "
            f"motor actual '{current}', motores disponibles {sorted(available) or [current]}. "
            f"Recomendación: {recommendation}")
    if not confirm:
        return _preview(diag + " Si el usuario quiere reprocesar, llama esta tool con confirm=true y mode='"
                        + (mode or other) + "'.",
                        document_id=doc_id, filename=doc["filename"], avg_confidence=avg_conf,
                        needs_review=stats["needs_review"], recommended_mode=mode or other)
    identity_error = _need_identity(ctx)
    if identity_error:
        return identity_error
    target_mode = mode or other
    if target_mode not in ("basico", "document_ai"):
        return _err("mode debe ser 'basico' o 'document_ai'")
    ok, msg = _enqueue_reprocess(conn, ctx, case_id, doc_id, target_mode)
    if not ok:
        return _err(msg)
    audit.record(conn, org_id=ctx.org_id, actor_id=ctx.actor_id, action="document.reprocess_requested",
                 entity_type="document", entity_id=doc_id, after={"ocr_mode": target_mode, "via": "chat_tool"})
    return [evidence_item("W", "correction_result",
                          f"✅ Reproceso de «{doc['filename']}» programado con el motor '{target_mode}'. "
                          "Al terminar se actualizan la BD, pgvector y el grafo automáticamente.",
                          document_id=doc_id, filename=doc["filename"], ocr_mode=target_mode, job_enqueued=True)]


def _media_diagnostic(conn: Connection, case_id: str, ctx: ToolContext, media_id: str,
                      confirm: bool) -> list[dict[str, Any]]:
    mid = _uuid(media_id)
    if not mid:
        return _err("media_id inválido")
    media = one(conn, "SELECT id, filename, title, asr_mode, processing_status FROM media WHERE id = :m AND case_id = :c",
                m=mid, c=case_id)
    if not media:
        return _err("Video/audio no encontrado en este expediente")
    stats = one(conn, """SELECT count(*) AS segments, avg(confidence) AS avg_conf,
        count(*) FILTER (WHERE needs_review) AS needs_review
        FROM transcript_segments WHERE media_id = :m""", m=mid)
    avg_conf = round(float(stats["avg_conf"] or 0), 3)
    recommendation = (f"Hay {stats['needs_review']} segmento(s) para revisión; corrígelos con correct_transcript_segment "
                      "o reprocesa el audio." if stats["needs_review"]
                      else f"Confianza media {avg_conf}. Si alguna frase está mal, corrige el segmento puntual.")
    diag = (f"Diagnóstico de «{media['title'] or media['filename']}»: {stats['segments']} segmentos, "
            f"confianza media {avg_conf}, {stats['needs_review']} para revisión. Recomendación: {recommendation}")
    if not confirm:
        return _preview(diag + " Si el usuario quiere reprocesar el audio, llama esta tool con confirm=true.",
                        media_id=mid, filename=media["filename"], avg_confidence=avg_conf,
                        needs_review=stats["needs_review"])
    identity_error = _need_identity(ctx)
    if identity_error:
        return identity_error
    ok, msg = _enqueue_reprocess(conn, ctx, case_id, mid, None)
    if not ok:
        return _err(msg)
    audit.record(conn, org_id=ctx.org_id, actor_id=ctx.actor_id, action="media.reprocess_requested",
                 entity_type="media", entity_id=mid, after={"via": "chat_tool"})
    return [evidence_item("W", "correction_result",
                          f"✅ Reproceso de «{media['title'] or media['filename']}» programado. "
                          "Al terminar se actualizan la BD, pgvector y el grafo automáticamente.",
                          media_id=mid, filename=media["filename"], job_enqueued=True)]


@register("reprocess_low_confidence",
          "Reprocesa en LOTE las páginas con MENOR confianza usando el OTRO motor OCR. "
          "Úsala cuando el usuario diga 'reprocesa las páginas de baja confianza' o 'mejora las peores hojas'. "
          "Sin confirm muestra el plan (páginas, documentos y motor destino); con confirm=true encola el "
          "reproceso de esos documentos. OJO: 'document_ai' usa Google y tiene costo.",
          {"limit": {"type": "integer", "default": 20, "description": "Máximo de páginas a considerar"},
           "max_confidence": {"type": "number", "default": 0.9, "description": "Umbral 0..1 (confianza ≤ este valor)"},
           "mode": {"type": "string", "enum": ["basico", "document_ai"], "description": "Filtrar por motor actual"},
           "target_mode": {"type": "string", "enum": ["basico", "document_ai"],
                           "description": "Motor destino (por defecto, el otro respecto al actual)"},
           "reason": {"type": "string"},
           "confirm": {"type": "boolean", "default": False}},
          kind="write")
def reprocess_low_confidence(conn: Connection, case_id: str, ctx: ToolContext, limit: int = 20,
                             max_confidence: float = 0.9, mode: str | None = None,
                             target_mode: str | None = None, reason: str = "",
                             confirm: bool = False) -> list[dict[str, Any]]:
    mc = min(1.0, max(0.0, float(max_confidence if max_confidence is not None else 0.9)))
    params: dict[str, Any] = {"c": case_id, "k": max(1, min(int(limit or 20), 200)), "mc": mc,
                              "mode": mode if mode in ("basico", "document_ai") else None}
    pages = rows(conn, """
        SELECT d.id AS document_id, d.filename, d.ocr_mode, v.page_number, v.mode, v.ocr_confidence
        FROM document_ocr_versions v
        JOIN documents d ON d.id = v.document_id
        WHERE d.case_id = :c AND v.ocr_confidence <= :mc
          AND (CAST(:mode AS text) IS NULL OR v.mode = :mode)
        ORDER BY v.ocr_confidence ASC, d.filename, v.page_number
        LIMIT :k""", **params)
    if not pages:
        return [evidence_item("PG", "corpus_summary",
                              f"No hay páginas con confianza ≤ {round(mc * 100)}% en este expediente.")]

    by_doc: dict[str, dict[str, Any]] = {}
    for r in pages:
        did = str(r["document_id"])
        d = by_doc.setdefault(did, {"filename": r["filename"], "pages": 0, "modes": set(),
                                    "current": r["ocr_mode"] or "basico"})
        d["pages"] += 1
        d["modes"].add(r["mode"])

    plan: list[dict[str, Any]] = []
    for did, d in by_doc.items():
        if target_mode in ("basico", "document_ai"):
            tgt = target_mode
        else:
            base = sorted(d["modes"])[0] if len(d["modes"]) == 1 else d["current"]
            tgt = "document_ai" if base == "basico" else "basico"
        plan.append({"document_id": did, "filename": d["filename"], "pages": d["pages"], "target_mode": tgt})

    detail = ", ".join(f"{p['filename']} → {p['target_mode']} ({p['pages']}p)" for p in plan[:8])
    head = (f"{len(pages)} página(s) con confianza ≤ {round(mc * 100)}% en {len(plan)} documento(s). "
            f"Se reprocesarán con: {detail}" + (" …" if len(plan) > 8 else ""))
    if not confirm:
        return _preview(head + " Llama a esta tool con confirm=true para encolarlo.",
                        pages=len(pages), documents=len(plan),
                        plan=[{k: p[k] for k in ("document_id", "filename", "target_mode", "pages")} for p in plan])
    identity_error = _need_identity(ctx)
    if identity_error:
        return identity_error
    enqueued = 0
    for p in plan:
        ok, msg = _enqueue_reprocess(conn, ctx, case_id, p["document_id"], p["target_mode"])
        if ok:
            enqueued += 1
        else:
            log.warning("no se pudo encolar el reproceso de %s: %s", p["document_id"], msg)
    audit.record(conn, org_id=ctx.org_id, actor_id=ctx.actor_id, action="document.reprocess_batch",
                 entity_type="case", entity_id=case_id,
                 after={"documents": enqueued, "pages": len(pages), "max_confidence": mc, "via": "chat_tool"})
    return [evidence_item("W", "correction_result",
                          f"✅ Reproceso programado para {enqueued} documento(s) "
                          f"({len(pages)} páginas con confianza ≤ {round(mc * 100)}%). "
                          "Al terminar se actualizan BD, pgvector y el grafo.",
                          documents=enqueued, pages=len(pages), job_enqueued=True)]


# ---------------------------------------------------------------------------
# Propagación (fuera de la transacción del loop; tolerante a fallos)
# ---------------------------------------------------------------------------
def _reindex_document(ctx: ToolContext, case_id: str, doc_id: str) -> bool:
    try:
        with tx(ctx.org_id, ctx.actor_id) as c:
            indexing.index_document(c, ctx.org_id, case_id, doc_id, actor_id=ctx.actor_id)
        return True
    except Exception:
        log.exception("no se pudo reindexar el documento %s tras corrección del chat", doc_id)
        return False


def _reindex_media(ctx: ToolContext, case_id: str, mid: str) -> bool:
    try:
        with tx(ctx.org_id, ctx.actor_id) as c:
            indexing.index_media(c, ctx.org_id, case_id, mid, actor_id=ctx.actor_id)
        return True
    except Exception:
        log.exception("no se pudo reindexar el media %s tras corrección del chat", mid)
        return False


def _reindex_speaker_media(ctx: ToolContext, case_id: str, speaker_id: str) -> bool:
    """Reindexa en pgvector todos los audios donde habla un hablante renombrado."""
    try:
        with tx(ctx.org_id, ctx.actor_id) as c:
            media_ids = [str(r["media_id"]) for r in rows(
                c, "SELECT DISTINCT media_id FROM transcript_segments WHERE speaker_id = :s", s=speaker_id)]
        for mid in media_ids:
            with tx(ctx.org_id, ctx.actor_id) as c:
                indexing.index_media(c, ctx.org_id, case_id, mid, actor_id=ctx.actor_id)
        return True
    except Exception:
        log.exception("no se pudieron reindexar los media del hablante %s", speaker_id)
        return False


def _enqueue_graph(ctx: ToolContext, case_id: str) -> None:
    try:
        enqueue_graph_refresh(ctx.org_id, case_id, ctx.actor_id)
    except Exception:
        log.exception("no se pudo encolar graph_build tras corrección del chat (caso %s)", case_id)


def _enqueue_reprocess(conn: Connection, ctx: ToolContext, case_id: str, file_id: str,
                       ocr_mode: str | None) -> tuple[bool, str]:
    """Crea el job de reproceso en la transacción del llamador y difiere el encolado
    a post-commit (encolar antes de confirmar bloquearía al executor: la fila del
    job aún no es visible para otras conexiones)."""
    s = get_settings()
    try:
        if ocr_mode:
            one(conn, "UPDATE documents SET ocr_mode = :m, processing_status = 'OCR_PENDING' WHERE id = :d AND case_id = :c RETURNING id",
                m=ocr_mode, d=file_id, c=case_id)
        else:
            one(conn, "UPDATE media SET processing_status = 'PENDING' WHERE id = :d AND case_id = :c RETURNING id",
                d=file_id, c=case_id)
        job = create_job(conn, org_id=ctx.org_id, case_id=case_id, job_type="file_ingest",
                         input_ids=[file_id], actor_id=ctx.actor_id,
                         key_parts=["file_ingest", case_id, file_id, "reprocess_chat", int(time.time() // 60)],
                         pipeline_version=s.PIPELINE_VERSION, model_version=s.LLM_MODEL)
        ctx.post_commit.append(lambda: _safe_enqueue(job, ctx))
        return True, ""
    except Exception as exc:
        log.exception("no se pudo programar el reproceso de %s", file_id)
        return False, f"No se pudo programar el reproceso: {exc}"


def _safe_enqueue(job: dict, ctx: ToolContext) -> None:
    try:
        enqueue_if_pending(job, ctx.org_id, ctx.actor_id)
    except Exception:  # broker caído: el job queda QUEUED y lo recoge el sweeper
        log.exception("no se pudo encolar el job %s tras la corrección del chat", job.get("id"))
