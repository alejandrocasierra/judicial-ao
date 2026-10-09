"""Pipeline de procesamiento de audio/video: ASR + diarización + identificación visual.

Salida: speakers (con display_name del hablante) y transcript_segments en BD.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.providers.asr import get_asr_provider
from app.services.diarization import diarize
from app.services.storage import incoming_dir, key_from_uri, storage
from app.services.teams_visual_id import extract_active_speaker_timeline

log = logging.getLogger(__name__)

# Fracción mínima de frames de un turno que deben coincidir con un nombre para
# asignarlo (evita nombres ruidosos por detecciones puntuales).
_MIN_NAME_AGREEMENT = 0.5

# Patrones de auto-presentación en audiencias (para hablantes SIN video: el juez
# suele tener la cámara apagada, así que no hay cuadro resaltado que leer).
_NAME_RX = r"([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?:\s+(?:de\s+|del\s+|la\s+|los\s+)?[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+){1,3})"
# El keyword va con (?i:...) (insensible), pero el NOMBRE es sensible a mayúsculas: así no se
# captura "soy yo yo yo digo" (que antes salía por usar re.IGNORECASE en todo el patrón).
_SELF_INTRO_PATTERNS = [
    re.compile(r"(?i:qui[eé]nes?\s+les\s+habla[,\s]+)" + _NAME_RX),
    re.compile(r"(?i:\bles\s+habla[,\s]+)" + _NAME_RX),
    re.compile(r"(?i:\bmi\s+nombre\s+es[,\s]+)" + _NAME_RX),
    re.compile(r"(?i:\bme\s+llamo[,\s]+)" + _NAME_RX),
    re.compile(r"(?i:\bsoy\s+)" + _NAME_RX),
]

# Palabras de relleno que NO forman parte de un nombre propio (evita "yo yo yo digo").
_NAME_FILLER = {"yo", "tu", "usted", "digo", "dice", "dijo", "dicen", "estoy", "soy", "es",
                "que", "pues", "bueno", "entonces", "esto", "esta", "aqui"}

# Palabras que no forman parte de un nombre (evita "soy el juez 21").
_NAME_STOPWORDS = {"el", "la", "los", "las", "juez", "doctor", "doctora", "señor", "señora",
                   "apoderado", "apoderada", "secretario", "secretaria", "magistrado"}


def _clean_intro_name(raw: str) -> str | None:
    """Normaliza y valida el nombre extraído de una auto-presentación."""
    name = " ".join(raw.split()).strip(" ,.;:-")
    words = name.split()
    # Quita conectores iniciales ("el juez", "la doctora") si quedaron pegados.
    while words and words[0].lower() in _NAME_STOPWORDS:
        words.pop(0)
    if len(words) < 2:
        return None
    low = [w.lower() for w in words]
    # Rechaza basura: palabras de relleno o nombre sin al menos 2 palabras distintas.
    if any(w in _NAME_FILLER for w in low) or len(set(low)) < 2:
        return None
    return " ".join(words)


def _extract_self_intro_names(joined: list[dict[str, Any]]) -> dict[str, str]:
    """Mapea label de hablante -> nombre auto-presentado en su intervención."""
    out: dict[str, str] = {}
    for seg in joined:
        for rx in _SELF_INTRO_PATTERNS:
            m = rx.search(seg["text"])
            if not m:
                continue
            name = _clean_intro_name(m.group(1))
            if name:
                out[seg["speaker_label"]] = name
                break
    return out


def _resolve_label_names(
    diarization: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
) -> dict[str, str]:
    """Mapea cada label de diarización (SPEAKER_xx) al nombre resaltado más votado.

    Para cada turno de diarización se recogen los nombres del hablante activo cuyo
    timestamp cae dentro del turno; se agregación por label y se exige mayoría.
    """
    votes: dict[str, Counter] = defaultdict(Counter)
    for d in diarization:
        for entry in timeline:
            if d["start_ms"] <= entry["timestamp_ms"] <= d["end_ms"]:
                votes[d["label"]][entry["name"]] += 1
    label_names: dict[str, str] = {}
    for label, counter in votes.items():
        if not counter:
            continue
        name, count = counter.most_common(1)[0]
        total = sum(counter.values())
        if count / total >= _MIN_NAME_AGREEMENT:
            label_names[label] = name
    return label_names


def _assign_speakers_to_segments(
    asr_segments: list[dict[str, Any]],
    diarization: list[dict[str, Any]],
    label_names: dict[str, str],
) -> list[dict[str, Any]]:
    """Une cada segmento ASR con el speaker de diarización de mayor solape y le pone nombre."""
    def _best_speaker(start_ms: int, end_ms: int) -> dict[str, Any] | None:
        best = None
        best_overlap = 0
        for d in diarization:
            overlap = max(0, min(end_ms, d["end_ms"]) - max(start_ms, d["start_ms"]))
            if overlap > best_overlap:
                best_overlap = overlap
                best = d
        return best

    out: list[dict[str, Any]] = []
    for seg in asr_segments:
        diar = _best_speaker(seg["start_ms"], seg["end_ms"])
        label = diar["label"] if diar else "UNKNOWN"
        out.append({
            "start_ms": seg["start_ms"],
            "end_ms": seg["end_ms"],
            "text": seg["text"],
            "confidence": seg.get("confidence", 0.0),
            "words": seg.get("words", []),
            "speaker_label": label,
            "visual_name": label_names.get(label),
        })
    return out


def _get_or_create_speaker(
    conn: Connection,
    org_id: str,
    case_id: str,
    label: str,
    display_name: str | None,
) -> str:
    """Crea el speaker o ACTUALIZA su display_name cuando la resolución de máquina cambia.

    Al re-diarizar, pyannote puede renumerar las etiquetas (`SPEAKER_00`, `SPEAKER_01`…).
    Si solo rellenáramos nombres vacíos, quedarían nombres viejos cruzados. Se actualiza
    el nombre resuelto por MÁQUINA (visual/auto-presentación) cuando difiere, pero NUNCA
    se pisa un nombre confirmado por una persona (`resolution_status='CONFIRMED'` o
    `resolution_source='human'`)."""
    existing = one(conn, "SELECT id, display_name, resolution_status, resolution_source "
                         "FROM speakers WHERE case_id = :c AND label = :l",
                   c=case_id, l=label)
    if existing:
        human = existing["resolution_status"] == "CONFIRMED" or existing["resolution_source"] == "human"
        if display_name and not human and display_name != existing["display_name"]:
            one(conn, """UPDATE speakers SET display_name = :n, resolution_status = 'PROBABLE',
                             resolution_source = 'teams_visual_id', confidence = 0.7
                         WHERE id = :i RETURNING id""",
                n=display_name, i=str(existing["id"]))
        return str(existing["id"])
    result = one(conn, """
        INSERT INTO speakers (organization_id, case_id, label, display_name, resolution_status, resolution_source, confidence)
        VALUES (:o, :c, :l, :n, :s, :src, :conf)
        RETURNING id
    """,
        o=org_id, c=case_id, l=label, n=display_name,
        s="PROBABLE" if display_name else "UNRESOLVED",
        src="teams_visual_id" if display_name else "diarization",
        conf=0.7 if display_name else 0.0,
    )
    return str(result["id"])


def _progress_writer(org_id: str, user_id: str, media_id: str):
    """Callback que publica el avance del ASR en `media_asr_progress`.

    Usa una transacción aparte porque la transacción del pipeline bloquea la fila de
    `media` hasta el commit final. Errores de progreso nunca tumban el ASR."""
    def cb(fraction: float, detail: str = "") -> None:
        pct = max(0, min(100, int(round(fraction * 100))))
        try:
            with tx(org_id, user_id) as c2:
                one(c2, """
                    INSERT INTO media_asr_progress (media_id, organization_id, pct, detail, updated_at)
                    VALUES (:m, :o, :p, :det, now())
                    ON CONFLICT (media_id) DO UPDATE
                      SET pct = EXCLUDED.pct, detail = EXCLUDED.detail, updated_at = now()
                    RETURNING media_id
                """, m=media_id, o=org_id, p=pct, det=detail)
        except Exception:  # noqa: BLE001
            log.debug("no se pudo publicar el progreso ASR de %s", media_id, exc_info=True)
    return cb


def _clear_progress(org_id: str, user_id: str, media_id: str) -> None:
    try:
        with tx(org_id, user_id) as c2:
            one(c2, "DELETE FROM media_asr_progress WHERE media_id = :m RETURNING media_id", m=media_id)
    except Exception:  # noqa: BLE001
        log.debug("no se pudo limpiar el progreso ASR de %s", media_id, exc_info=True)


def process_media(
    conn: Connection,
    media_id: str,
    org_id: str,
    case_id: str,
    user_id: str,
    do_diarization: bool = True,
) -> dict[str, Any]:
    """Ejecuta ASR (+ diarización/visión si `do_diarization`).

    La diarización e identificación visual cargan pyannote + el audio completo en
    memoria; ejecutarlas en el MISMO proceso que Whisper agota la RAM del servidor
    (OOM/SIGKILL). Por eso `file_ingest`/`media_asr` las piden en una etapa aparte:
    `process_media(..., do_diarization=False)` hace SOLO ASR y `apply_diarization`
    (job/proceso separado) asigna los hablantes después."""
    s = get_settings()
    allowed_statuses = {"UPLOADED", "ASR_PENDING", "ASR_COMPLETE", "REVIEW_REQUIRED"}
    media = one(conn, """
        UPDATE media
        SET processing_status = 'ASR_RUNNING'
        WHERE id = :m AND case_id = :c AND processing_status = ANY(:allowed)
        RETURNING id, storage_uri, filename, mime_type, media_type, sha256
    """, m=media_id, c=case_id, allowed=list(allowed_statuses))
    if media is None:
        existing = one(conn, "SELECT processing_status FROM media WHERE id = :m AND case_id = :c", m=media_id, c=case_id)
        if existing is None:
            raise ValueError(f"media {media_id} not found")
        log.info("media %s already processed (status=%s); skipping", media_id, existing["processing_status"])
        return {"segments": 0, "speakers": 0, "skipped": True}

    # Reprocesamiento idempotente: los segmentos son artefactos derivados.
    conn.execute(text("DELETE FROM transcript_segments WHERE media_id = :m"), {"m": media_id})

    # Archivo local COMPARTIDO (api<->worker): si la subida lo dejó aquí, se procesa sin
    # re-descargar de GCS (importante con internet lento). Se borra al terminar.
    local = incoming_dir() / str(media["sha256"])
    tmpdir: Path | None = None
    try:
        if local.exists():
            media_path = local
        else:
            key = key_from_uri(media["storage_uri"])
            tmpdir = Path(tempfile.mkdtemp(prefix="media-"))
            media_path = tmpdir / "media.bin"
            storage().download_to(key, media_path)
    except Exception as exc:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        log.exception("no se pudo preparar media %s", media_id)
        raise RuntimeError(f"storage error: {exc}") from exc

    # 1. ASR (por RUTA: no carga el archivo completo en memoria) con progreso en vivo.
    #    Progreso CONTINUO entre etapas: ASR = 0–85 %, diarización/visión = 85–99 %,
    #    100 % solo cuando la etapa 2 commitea. Así el monitor sube y no marca 100 antes.
    provider = get_asr_provider()
    base_cb = _progress_writer(org_id, user_id, media_id)

    def progress_cb(fraction: float, detail: str = "") -> None:
        if do_diarization:
            base_cb(fraction, detail)
        else:
            base_cb(min(0.60, float(fraction) * 0.60), detail)

    progress_cb(0.01, "Preparando audio…")
    asr_segments = provider.transcribe(media_path, media["mime_type"], progress_cb=progress_cb)
    log.info("ASR produjo %d segmentos para %s", len(asr_segments), media["filename"])

    # 2-4. Diarización + identificación visual + nombres. Si `do_diarization` es
    # False (etapa ASR), los segmentos quedan con hablante "UNKNOWN" y la etapa 2
    # (`apply_diarization`) los reasigna en su propio proceso.
    diarization: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    label_names: dict[str, str] = {}
    if do_diarization:
        try:
            diarization = diarize(media_path, media["mime_type"])
            log.info("Diarización produjo %d segmentos para %s", len(diarization), media["filename"])
        except Exception as exc:
            log.warning("Diarización falló para %s: %s", media["filename"], exc)

        # Identificación visual del HABLANTE ACTIVO (Teams: nombre resaltado)
        if media["media_type"] == "video":
            try:
                timeline = extract_active_speaker_timeline(media_path, media["mime_type"])
                log.info("Identificación visual produjo %d muestras de hablante activo para %s",
                         len(timeline), media["filename"])
            except Exception as exc:
                log.warning("Identificación visual falló para %s: %s", media["filename"], exc)

        label_names = _resolve_label_names(diarization, timeline)

    joined = _assign_speakers_to_segments(asr_segments, diarization, label_names)

    # 4b. Auto-presentaciones ("quien les habla, X") tienen prioridad: cubren a los
    # hablantes SIN video (p. ej. el juez con la cámara apagada), que no aparecen
    # resaltados en Teams y por tanto la identificación visual no puede resolver.
    if do_diarization:
        intro_names = _extract_self_intro_names(joined)
        if intro_names:
            label_names.update(intro_names)
            for seg in joined:
                seg["visual_name"] = label_names.get(seg["speaker_label"])
        log.info("Nombres resueltos: %s (auto-presentados: %s)", label_names, intro_names)

    # 5. Guardar speakers y segmentos
    needs_review_count = 0
    for seg in joined:
        speaker_id = _get_or_create_speaker(conn, org_id, case_id, seg["speaker_label"], seg.get("visual_name"))
        needs_review = seg["confidence"] < s.ASR_CONFIDENCE_THRESHOLD
        if needs_review:
            needs_review_count += 1
        one(conn, """
            INSERT INTO transcript_segments (
                organization_id, media_id, speaker_id, start_ms, end_ms, text, confidence, needs_review, language
            ) VALUES (
                :o, :m, :spk, :s, :e, :t, :c, :r, :lang
            )
            RETURNING id
        """,
            o=org_id, m=media_id, spk=speaker_id,
            s=seg["start_ms"], e=seg["end_ms"], t=seg["text"],
            c=seg["confidence"], r=needs_review, lang="es")

    # 6. Actualizar media
    status = "REVIEW_REQUIRED" if needs_review_count else "ASR_COMPLETE"
    one(conn, """
        UPDATE media
        SET processing_status = :s,
            duration_ms = :d
        WHERE id = :m
        RETURNING id
    """, s=status, m=media_id, d=max(seg["end_ms"] for seg in joined) if joined else 0)

    if do_diarization:
        _clear_progress(org_id, user_id, media_id)
    else:
        # Etapa 2 pendiente: dejar el progreso en 60 % (no limpiar) para que el monitor
        # NO muestre 100 % hasta que la diarización termine.
        base_cb(0.60, "Transcripción lista; diarizando hablantes…")
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return {
        "segments": len(joined),
        "speakers": len({seg["speaker_label"] for seg in joined}),
        "named_speakers": len(label_names),
        "label_names": label_names,
        "needs_review_count": needs_review_count,
        "duration_ms": max(seg["end_ms"] for seg in joined) if joined else 0,
        "status": status,
        "needs_diarization": not do_diarization,
    }


def apply_diarization(
    conn: Connection,
    media_id: str,
    org_id: str,
    case_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Etapa 2 del pipeline de medios: diarización + identificación visual.

    Toma los `transcript_segments` ya creados por el ASR y les asigna el hablante
    correcto (creando/actualizando `speakers`). Corre en su PROPIO job/proceso,
    sin Whisper cargado, para no agotar la RAM (OOM/SIGKILL).
    """
    s = get_settings()
    media = one(conn, """
        UPDATE media
        SET processing_status = 'ASR_RUNNING'
        WHERE id = :m AND case_id = :c AND processing_status = ANY(:allowed)
        RETURNING id, storage_uri, filename, mime_type, media_type, sha256
    """, m=media_id, c=case_id, allowed=["ASR_COMPLETE", "REVIEW_REQUIRED", "ASR_PENDING", "UPLOADED"])
    if media is None:
        existing = one(conn, "SELECT processing_status FROM media WHERE id = :m AND case_id = :c",
                       m=media_id, c=case_id)
        if existing is None:
            raise ValueError(f"media {media_id} not found")
        log.info("media %s no está listo para diarizar (status=%s); skipping", media_id, existing["processing_status"])
        _clear_progress(org_id, user_id, media_id)
        return {"segments": 0, "skipped": True, "reason": str(existing["processing_status"])}

    segs = rows(conn, """
        SELECT id, start_ms, end_ms, text, confidence
        FROM transcript_segments WHERE media_id = :m ORDER BY start_ms
    """, m=media_id)
    if not segs:
        log.info("media %s sin segmentos ASR; se omite la diarización", media_id)
        one(conn, "UPDATE media SET processing_status = 'ASR_COMPLETE' WHERE id = :m RETURNING id", m=media_id)
        _clear_progress(org_id, user_id, media_id)
        return {"segments": 0, "skipped": True, "reason": "no_segments"}

    local = incoming_dir() / str(media["sha256"])
    tmpdir: Path | None = None
    used_local = local.exists()
    try:
        if used_local:
            media_path = local
        else:
            key = key_from_uri(media["storage_uri"])
            tmpdir = Path(tempfile.mkdtemp(prefix="media-"))
            media_path = tmpdir / "media.bin"
            storage().download_to(key, media_path)
    except Exception as exc:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        log.exception("no se pudo preparar media %s para diarización", media_id)
        raise RuntimeError(f"storage error: {exc}") from exc

    progress = _progress_writer(org_id, user_id, media_id)
    try:
        progress(0.60, "Diarizando hablantes…")

        def _diar_progress(step_name: str, completed: Any, total: Any) -> None:
            if not total:
                return
            frac = max(0.0, min(1.0, float(completed or 0) / float(total)))
            # La diarización ocupa 60–95 % (es la etapa más lenta): así la barra se mueve.
            progress(0.60 + 0.35 * frac, f"Diarizando hablantes… ({step_name} {int(frac * 100)}%)")

        diarization: list[dict[str, Any]] = []
        try:
            diarization = diarize(media_path, media["mime_type"], progress_cb=_diar_progress)
            log.info("Diarización produjo %d segmentos para %s", len(diarization), media["filename"])
        except Exception as exc:
            log.warning("Diarización falló para %s: %s", media["filename"], exc)
        progress(0.95, "Diarización lista; identificando hablantes…")

        timeline: list[dict[str, Any]] = []
        if media["media_type"] == "video":
            progress(0.97, "Identificando hablantes (video)…")

            def _vis_progress(done: int, total: int) -> None:
                if total:
                    frac = min(1.0, max(0.0, done / total))
                    progress(0.97 + 0.02 * frac,
                             f"Identificando hablantes (video)… {int(frac * 100)}%")

            try:
                timeline = extract_active_speaker_timeline(
                    media_path, media["mime_type"],
                    step_s=float(getattr(s, "VISUAL_ID_STEP_SECONDS", 5.0) or 5.0),
                    progress_cb=_vis_progress)
            except Exception as exc:
                log.warning("Identificación visual falló para %s: %s", media["filename"], exc)

        label_names = _resolve_label_names(diarization, timeline)

        def _best_label(start_ms: int, end_ms: int) -> str:
            best = None
            best_overlap = 0
            for d in diarization:
                overlap = max(0, min(end_ms, d["end_ms"]) - max(start_ms, d["start_ms"]))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best = d
            return best["label"] if best else "UNKNOWN"

        # Auto-presentaciones ("quien les habla, X") sobre el texto ya transcrito.
        joined_pre = [{"start_ms": sg["start_ms"], "end_ms": sg["end_ms"], "text": sg["text"],
                       "speaker_label": _best_label(sg["start_ms"], sg["end_ms"])} for sg in segs]
        intro_names = _extract_self_intro_names(joined_pre)
        if intro_names:
            label_names.update(intro_names)
        log.info("Diarización etapa 2: %d segmentos, nombres=%s", len(segs), label_names)

        needs_review_count = 0
        labels_seen: set[str] = set()
        for sg, pre in zip(segs, joined_pre):
            label = pre["speaker_label"]
            labels_seen.add(label)
            speaker_id = _get_or_create_speaker(conn, org_id, case_id, label, label_names.get(label))
            one(conn, "UPDATE transcript_segments SET speaker_id = :spk WHERE id = :i RETURNING id",
                spk=speaker_id, i=str(sg["id"]))
            if (sg["confidence"] or 0.0) < s.ASR_CONFIDENCE_THRESHOLD:
                needs_review_count += 1

        status = "REVIEW_REQUIRED" if needs_review_count else "ASR_COMPLETE"
        one(conn, "UPDATE media SET processing_status = :s WHERE id = :m RETURNING id", s=status, m=media_id)
    finally:
        _clear_progress(org_id, user_id, media_id)
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        # La copia local NO se borra aquí: el handler sube el original a GCS tras
        # commitear los hablantes y entonces la libera (así la subida no retrasa nada).

    return {
        "segments": len(segs),
        "speakers": len(labels_seen),
        "named_speakers": len(label_names),
        "label_names": label_names,
        "status": status,
    }
