"""Pipeline de procesamiento de audio/video: ASR + diarización + identificación visual.

Salida: speakers (con display_name del hablante) y transcript_segments en BD.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.config import get_settings
from app.core.db import one
from app.providers.asr import get_asr_provider
from app.services.diarization import diarize
from app.services.storage import key_from_uri, storage
from app.services.teams_visual_id import extract_active_speaker_timeline

log = logging.getLogger(__name__)

# Fracción mínima de frames de un turno que deben coincidir con un nombre para
# asignarlo (evita nombres ruidosos por detecciones puntuales).
_MIN_NAME_AGREEMENT = 0.5

# Patrones de auto-presentación en audiencias (para hablantes SIN video: el juez
# suele tener la cámara apagada, así que no hay cuadro resaltado que leer).
_NAME_RX = r"([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+(?:\s+(?:de\s+|del\s+|la\s+|los\s+)?[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+){1,3})"
_SELF_INTRO_PATTERNS = [
    re.compile(r"qui[eé]nes?\s+les\s+habla[,\s]+" + _NAME_RX, re.IGNORECASE),
    re.compile(r"\bles\s+habla[,\s]+" + _NAME_RX, re.IGNORECASE),
    re.compile(r"\bmi\s+nombre\s+es[,\s]+" + _NAME_RX, re.IGNORECASE),
    re.compile(r"\bme\s+llamo[,\s]+" + _NAME_RX, re.IGNORECASE),
    re.compile(r"\bsoy\s+" + _NAME_RX, re.IGNORECASE),
]

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
    """Crea el speaker o actualiza su display_name si se identificó un nombre."""
    existing = one(conn, "SELECT id, display_name FROM speakers WHERE case_id = :c AND label = :l",
                   c=case_id, l=label)
    if existing:
        if display_name and not existing["display_name"]:
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


def process_media(
    conn: Connection,
    media_id: str,
    org_id: str,
    case_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Ejecuta ASR + diarización + identificación visual sobre un medio."""
    s = get_settings()
    allowed_statuses = {"UPLOADED", "ASR_PENDING", "ASR_COMPLETE", "REVIEW_REQUIRED"}
    media = one(conn, """
        UPDATE media
        SET processing_status = 'ASR_RUNNING'
        WHERE id = :m AND case_id = :c AND processing_status = ANY(:allowed)
        RETURNING id, storage_uri, filename, mime_type, media_type
    """, m=media_id, c=case_id, allowed=list(allowed_statuses))
    if media is None:
        existing = one(conn, "SELECT processing_status FROM media WHERE id = :m AND case_id = :c", m=media_id, c=case_id)
        if existing is None:
            raise ValueError(f"media {media_id} not found")
        log.info("media %s already processed (status=%s); skipping", media_id, existing["processing_status"])
        return {"segments": 0, "speakers": 0, "skipped": True}

    # Reprocesamiento idempotente: los segmentos son artefactos derivados.
    conn.execute(text("DELETE FROM transcript_segments WHERE media_id = :m"), {"m": media_id})

    try:
        key = key_from_uri(media["storage_uri"])
        media_bytes = storage().get(key)
    except Exception as exc:
        log.exception("no se pudo leer media %s desde storage", media_id)
        raise RuntimeError(f"storage error: {exc}") from exc

    # 1. ASR
    provider = get_asr_provider()
    asr_segments = provider.transcribe(media_bytes, media["mime_type"])
    log.info("ASR produjo %d segmentos para %s", len(asr_segments), media["filename"])

    # 2. Diarización
    diarization: list[dict[str, Any]] = []
    try:
        diarization = diarize(media_bytes, media["mime_type"])
        log.info("Diarización produjo %d segmentos para %s", len(diarization), media["filename"])
    except Exception as exc:
        log.warning("Diarización falló para %s: %s", media["filename"], exc)

    # 3. Identificación visual del HABLANTE ACTIVO (Teams: nombre resaltado)
    timeline: list[dict[str, Any]] = []
    if media["media_type"] == "video":
        try:
            timeline = extract_active_speaker_timeline(media_bytes, media["mime_type"])
            log.info("Identificación visual produjo %d muestras de hablante activo para %s",
                     len(timeline), media["filename"])
        except Exception as exc:
            log.warning("Identificación visual falló para %s: %s", media["filename"], exc)

    # 4. Nombre por label (votación visual) y unión con los segmentos
    label_names = _resolve_label_names(diarization, timeline)
    joined = _assign_speakers_to_segments(asr_segments, diarization, label_names)

    # 4b. Auto-presentaciones ("quien les habla, X") tienen prioridad: cubren a los
    # hablantes SIN video (p. ej. el juez con la cámara apagada), que no aparecen
    # resaltados en Teams y por tanto la identificación visual no puede resolver.
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

    return {
        "segments": len(joined),
        "speakers": len({seg["speaker_label"] for seg in joined}),
        "named_speakers": len(label_names),
        "label_names": label_names,
        "needs_review_count": needs_review_count,
        "duration_ms": max(seg["end_ms"] for seg in joined) if joined else 0,
        "status": status,
    }
