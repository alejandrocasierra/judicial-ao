"""Pipeline de procesamiento de audio/video: ASR + diarización + identificación visual.

Salida: speakers (con display_name del hablante) y transcript_segments en BD.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
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
# SOLO patrones de PRIMERA PERSONA (la voz dice su propio nombre). Los patrones sueltos
# ("soy X", "les habla X") capturaban a TERCEROS nombrados por la jueza ("el señor ... que
# les habla Jorge Melo", "soy el que firmó") y atribuían el nombre al hablante equivocado.
_SELF_INTRO_PATTERNS = [
    re.compile(r"(?i:qui[eé]n(?:es)?\s+les\s+habla[,\s]+)" + _NAME_RX),
    re.compile(r"(?i:\bles\s+habla[,\s]+)" + _NAME_RX),
    re.compile(r"(?i:\bmi\s+nombre\s+es[,\s]+)" + _NAME_RX),
    re.compile(r"(?i:\bme\s+llamo[,\s]+)" + _NAME_RX),
    re.compile(r"(?i:\byo\s+soy[,\s]+)" + _NAME_RX),
]

# Otorgamiento de la palabra ("tiene la palabra el doctor X"): el nombre NO es el que
# habla ahora sino el del SIGUIENTE turno. El honorífico es opcional y en minúsculas.
_FLOOR_NAME_RX = r"(?:el\s+|la\s+)?(?:(?:doctor|doctora|señor|señora|magistrado|magistrada|apoderado|apoderada)\s+)?" + _NAME_RX
_FLOOR_PATTERNS = [
    re.compile(r"(?i:\btiene\s+la\s+palabra[,\s]+)" + _FLOOR_NAME_RX),
    re.compile(r"(?i:\b(?:concede|otorga|da|cede)\s+(?:el\s+uso\s+de\s+)?la\s+palabra[,\s]+(?:a[l]?\s+)?)" + _FLOOR_NAME_RX),
    re.compile(r"(?i:\buso\s+de\s+la\s+palabra[,\s]+(?:para\s+|a[l]?\s+)?)" + _FLOOR_NAME_RX),
]

# Palabras de relleno que NO forman parte de un nombre propio (evita "yo yo yo digo").
_NAME_FILLER = {"yo", "tu", "usted", "digo", "dice", "dijo", "dicen", "estoy", "soy", "es",
                "que", "pues", "bueno", "entonces", "esto", "esta", "aqui"}

# Palabras que no forman parte de un nombre (evita "soy el juez 21").
_NAME_STOPWORDS = {"el", "la", "los", "las", "juez", "doctor", "doctora", "señor", "señora",
                   "apoderado", "apoderada", "secretario", "secretaria", "magistrado"}

# Un nombre de HABLANTE debe parecer una persona. El OCR visual de Teams a veces lee
# textos de pantalla (correos, rótulos del despacho) y los toma como "nombre activo":
# "Correo: Juzgado 21 Civil Circuil", "soporte @ juzgado". Se descartan.
_ORG_WORDS = {"juzgado", "tribunal", "despacho", "civil", "circuito", "correo", "email",
              "sala", "fiscalia", "fiscalía", "secretaria", "secretaría", "circuil", "circular"}


def _looks_like_person_name(name: str | None) -> bool:
    """True si el texto parece el nombre de una PERSONA (y no un correo o un rótulo)."""
    if not name or not name.strip():
        return False
    n = name.strip()
    if any(ch in n for ch in ("@", ":", "/", "\\", "<", ">")):
        return False
    if any(ch.isdigit() for ch in n):
        return False
    words = n.split()
    if len(words) < 2 or len(words) > 4:
        return False
    low = {w.strip(",.;").lower() for w in words}
    if low & _ORG_WORDS:
        return False
    # Debe tener al menos 2 palabras capitalizadas (nombres propios).
    caps = sum(1 for w in words if w[:1].isupper())
    return caps >= 2


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


def _filter_static_overlay(timeline: list[dict[str, Any]], span_ms: int) -> list[dict[str, Any]]:
    """Quita del timeline los nombres que cubren >60 % de un video largo y los que NO
    parecen nombres de personas (correos/rótulos leídos por el OCR de Teams).

    En Teams el overlay puede ser FIJO (p. ej. "Organized by: X" = quien grabó) y no
    el hablante activo. Si un nombre aparece en más del 60 % de las muestras de un
    video de >10 min, es ese overlay: se descarta para no contaminar los nombres."""
    if not timeline:
        return timeline
    before = len(timeline)
    timeline = [e for e in timeline if _looks_like_person_name(e["name"])]
    if len(timeline) != before:
        log.info("Timeline visual: %d muestra(s) descartadas por no parecer nombres de personas",
                 before - len(timeline))
    if span_ms < 10 * 60_000 or not timeline:
        return timeline
    counts = Counter(e["name"] for e in timeline)
    total = sum(counts.values()) or 1
    static = {n for n, c in counts.items() if c / total > 0.6}
    if static:
        log.info("Overlay estático de Teams ignorado (cubre >60%% del video): %s", sorted(static))
    return [e for e in timeline if e["name"] not in static]


def _name_events(joined: list[dict[str, Any]]) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Eventos (tiempo_ms, nombre) de VOZ: auto-presentaciones y otorgamientos de palabra.

    Se usan con TIEMPO (no por cluster) porque un cluster de diarización puede mezclar
    a dos personas: así "Mi nombre es X" nombra a quien habla en ese momento y no a
    todo el cluster."""
    intros: list[tuple[int, str]] = []
    floors: list[tuple[int, str]] = []
    for i, seg in enumerate(joined):
        for rx in _SELF_INTRO_PATTERNS:
            m = rx.search(seg["text"] or "")
            if m:
                nm = _clean_intro_name(m.group(1))
                if nm:
                    intros.append((int(seg["start_ms"]), nm))
                break
        for rx in _FLOOR_PATTERNS:
            m = rx.search(seg["text"] or "")
            if m:
                nm = _clean_intro_name(m.group(1))
                if not nm:
                    continue
                # El nombre mencionado es el del SIGUIENTE hablante distinto.
                nxt = next((s for s in joined[i + 1:] if s["speaker_label"] != seg["speaker_label"]), None)
                if nxt:
                    floors.append((int(nxt["start_ms"]), nm))
                break
    return intros, floors


_INTRO_WINDOW_MS = 150_000   # una presentación nombra al hablante de los ~2.5 min siguientes
_FLOOR_WINDOW_MS = 300_000   # la palabra otorgada vale para los ~5 min siguientes


def _name_in_mixed_blocks(intro_blocks: list[tuple[int, str]], start_ms: int,
                          end_ms: int) -> str | None:
    """Cluster MIXTO: cada auto-presentación inicia el bloque de ESE hablante hasta la
    siguiente presentación. Regla estricta: la intro vale desde el momento en que
    empieza (sin extensión hacia atrás — extenderla le asignaba a la persona que se
    presenta las preguntas que le hizo la jueza segundos antes)."""
    name: str | None = None
    for t, nm in intro_blocks:
        if t <= start_ms + 3_000:
            name = nm              # bloque vigente: esta intro ya empezó
        else:
            break
    return name


def _name_from_events(events: list[tuple[int, str]], start_ms: int, end_ms: int,
                      window_ms: int) -> str | None:
    """Nombre del evento más reciente que cubre este segmento (o None)."""
    best: tuple[int, str] | None = None
    for t, nm in events:
        if t <= end_ms + 20_000 and t >= start_ms - window_ms:
            if best is None or t > best[0]:
                best = (t, nm)
    return best[1] if best else None


def _split_text_at(text: str, frac: float) -> tuple[str, str]:
    """Parte un texto cerca de la fracción dada, en el espacio más cercano.

    Fallback cuando el segmento NO tiene timestamps por palabra (medios procesados
    antes de la migración 0040)."""
    if not text:
        return "", ""
    cut = int(len(text) * max(0.15, min(0.85, frac)))
    # Busca el espacio más cercano al corte (ventana de ±20 % del texto).
    span = max(3, int(len(text) * 0.2))
    cand = [p for p in (text.rfind(" ", max(0, cut - span), cut),
                        text.find(" ", cut, min(len(text), cut + span))) if p != -1]
    if cand:
        cut = min(cand, key=lambda p: abs(p - cut))
    return text[:cut].strip(), text[cut:].strip()


def _split_segment_by_words(seg: dict[str, Any], boundary_ms: int
                            ) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Parte un segmento EXACTO en el borde de un turno, usando los timestamps por
    palabra de Whisper (corte al milisegundo). Devuelve (parte1, parte2) o None si el
    segmento no tiene palabras utilizables."""
    words = seg.get("words") or []
    if not isinstance(words, list) or len(words) < 2:
        return None
    idx = next((i for i, w in enumerate(words)
                if int(w.get("start_ms") or 0) >= boundary_ms), None)
    if idx is None or idx == 0 or idx >= len(words):
        return None
    left, right = words[:idx], words[idx:]
    if not left or not right:
        return None
    t1 = " ".join(str(w.get("word", "")).strip() for w in left).strip()
    t2 = " ".join(str(w.get("word", "")).strip() for w in right).strip()
    if not t1 or not t2:
        return None
    return (
        {"start_ms": int(left[0].get("start_ms") or seg["start_ms"]),
         "end_ms": int(left[-1].get("end_ms") or boundary_ms),
         "text": t1, "words": left},
        {"start_ms": int(right[0].get("start_ms") or boundary_ms),
         "end_ms": int(right[-1].get("end_ms") or seg["end_ms"]),
         "text": t2, "words": right},
    )


def _extract_floor_grants(joined: list[dict[str, Any]]) -> dict[str, str]:
    """Mapea label -> nombre al que se le OTORGÓ la palabra ("tiene la palabra el doctor X").

    El nombre mencionado no es quien habla en ese segmento sino quien habla a
    continuación: se asigna al label del PRÓXIMO segmento con hablante distinto."""
    out: dict[str, str] = {}
    for i, seg in enumerate(joined):
        for rx in _FLOOR_PATTERNS:
            m = rx.search(seg["text"])
            if not m:
                continue
            name = _clean_intro_name(m.group(1))
            if name:
                for nxt in joined[i + 1:]:
                    if nxt["speaker_label"] != seg["speaker_label"]:
                        out.setdefault(nxt["speaker_label"], name)
                        break
            break
    return out


def _norm_name(name: str) -> str:
    """Minúsculas, sin tildes, espacios colapsados (para comparar nombres)."""
    base = unicodedata.normalize("NFKD", name.lower())
    return " ".join("".join(c for c in base if not unicodedata.combining(c)).split())


def _canonical_name(name: str | None, known: list[str], threshold: float = 0.6) -> str | None:
    """Corrige un nombre mal transcrito usando los nombres YA conocidos del caso.

    "Albalúzico Álvarez" (ASR) -> "Alba Lucy Cock Alvarez" (conocida del caso) si la
    similitud supera el umbral; si no hay parecido con nadie, se conserva el original."""
    if not name or not known:
        return name
    norm = _norm_name(name)
    if any(norm == _norm_name(k) for k in known):
        for k in known:
            if _norm_name(k) == norm:
                return k
        return name
    best, best_score = name, 0.0
    for k in known:
        score = SequenceMatcher(None, norm, _norm_name(k)).ratio()
        if score > best_score:
            best, best_score = k, score
    return best if best_score >= threshold else name


def _case_context(conn: Connection, case_id: str, org_id: str | None = None) -> tuple[str, list[str]]:
    """Prompt de contexto para Whisper + lista de nombres conocidos del caso.

    El prompt base se lee del AGENTE editable "Transcripción ASR · Prompt de contexto"
    (/dashboard/agents); si no existe, de ASR_INITIAL_PROMPT (.env) o el default.
    Whisper copia el vocabulario del `initial_prompt`: pasarle el radicado y los
    participantes ya conocidos (hablantes de otros medios + partes) corrige nombres
    propios y números de radicado que el modelo suele destrozar."""
    s = get_settings()
    from app.services.builtin_agents import ASR_CONTEXT_AGENT_NAME, ASR_CONTEXT_DEFAULT_PROMPT
    base_prompt = s.ASR_INITIAL_PROMPT or ASR_CONTEXT_DEFAULT_PROMPT
    if org_id:
        try:
            row = one(conn, "SELECT system_prompt FROM agents WHERE organization_id = :o AND name = :n",
                      o=org_id, n=ASR_CONTEXT_AGENT_NAME)
            if row and row.get("system_prompt"):
                base_prompt = str(row["system_prompt"]).strip()
        except Exception:  # noqa: BLE001 — si falla la lectura, se usa el default
            log.debug("no se pudo leer el prompt ASR del agente; usando default", exc_info=True)
    case = one(conn, "SELECT case_number, court FROM cases WHERE id = :c", c=case_id)
    known_rows = rows(conn, """
        SELECT DISTINCT display_name AS name FROM speakers
        WHERE case_id = :c AND display_name IS NOT NULL AND btrim(display_name) <> ''
        UNION
        SELECT DISTINCT name FROM parties
        WHERE case_id = :c AND name IS NOT NULL AND btrim(name) <> ''
    """, c=case_id)
    seen: set[str] = set()
    known: list[str] = []
    for r in known_rows:
        n = _norm_name(r["name"])
        if n not in seen:
            seen.add(n)
            known.append(r["name"])
    parts = [base_prompt]
    if case:
        parts.append(f"Radicado: {case['case_number']}.")
        if case.get("court"):
            parts.append(f"Despacho: {case['court']}.")
    if known:
        parts.append("Participantes: " + ", ".join(known) + ".")
    return " ".join(parts), known


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
        # El nombre debe parecer de una PERSONA: descarta correos/rótulos leídos por OCR.
        if count / total >= _MIN_NAME_AGREEMENT and _looks_like_person_name(name):
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
    media_id: str,
    label: str,
    display_name: str | None,
) -> str:
    """Crea el speaker (POR MEDIA) o ACTUALIZA su display_name cuando la resolución de
    máquina cambia.

    Los hablantes son de alcance por-medio: pyannote numera los clusters de forma
    independiente en cada grabación, así que `SPEAKER_04` es una persona distinta en
    cada video. Al ser por-media, la diarización de un video NO pisa los nombres de
    otro (antes sí: filas compartidas por label → nombres cruzados).

    Al re-diarizar, se actualiza el nombre resuelto por MÁQUINA (visual/auto-presentación)
    cuando difiere, pero NUNCA se pisa un nombre confirmado por una persona."""
    existing = one(conn, "SELECT id, display_name, resolution_status, resolution_source "
                         "FROM speakers WHERE case_id = :c AND media_id = :m AND label = :l",
                   c=case_id, m=media_id, l=label)
    if existing:
        human = existing["resolution_status"] == "CONFIRMED" or existing["resolution_source"] == "human"
        if display_name and not human and display_name != existing["display_name"]:
            one(conn, """UPDATE speakers SET display_name = :n, resolution_status = 'PROBABLE',
                             resolution_source = 'teams_visual_id', confidence = 0.7
                         WHERE id = :i RETURNING id""",
                n=display_name, i=str(existing["id"]))
        return str(existing["id"])
    result = one(conn, """
        INSERT INTO speakers (organization_id, case_id, media_id, label, display_name, resolution_status, resolution_source, confidence)
        VALUES (:o, :c, :m, :l, :n, :s, :src, :conf)
        RETURNING id
    """,
        o=org_id, c=case_id, m=media_id, l=label, n=display_name,
        s="PROBABLE" if display_name else "UNRESOLVED",
        src="teams_visual_id" if display_name else "diarization",
        conf=0.7 if display_name else 0.0,
    )
    return str(result["id"])


def _get_or_create_speaker_named(
    conn: Connection,
    org_id: str,
    case_id: str,
    media_id: str,
    name: str | None,
    fallback_label: str,
) -> str:
    """Speaker por-medio identificado por NOMBRE (identificación visual de Teams).

    En Teams el nombre RESALTADO es el del que habla en ese momento; así que se
    asigna el segmento al hablante con ESE nombre (aunque el cluster de audio lo
    hubiera agrupado con otro). Si no hay nombre, cae al hablante por etiqueta de
    audio (fallback para videos sin resaltado)."""
    if name and name.strip() and _looks_like_person_name(name):
        clean = name.strip()
        row = one(conn, """SELECT id FROM speakers
                           WHERE case_id = :c AND media_id = :m
                             AND lower(btrim(display_name)) = lower(btrim(:n))""",
                  c=case_id, m=media_id, n=clean)
        if row:
            return str(row["id"])
        nxt = one(conn, """SELECT coalesce(max((regexp_match(label, '^SPEAKER_([0-9]+)$'))[1]::int), -1) + 1 AS n
                           FROM speakers WHERE case_id = :c AND media_id = :m AND label ~ '^SPEAKER_[0-9]+$'""",
                  c=case_id, m=media_id)["n"]
        new = one(conn, """INSERT INTO speakers (organization_id, case_id, media_id, label, display_name,
                                resolution_status, resolution_source, confidence)
            VALUES (:o, :c, :m, :l, :n, 'PROBABLE', 'teams_visual_id', 0.7) RETURNING id""",
                  o=org_id, c=case_id, m=media_id, l=f"SPEAKER_{int(nxt):02d}", n=clean)
        return str(new["id"])
    return _get_or_create_speaker(conn, org_id, case_id, media_id, fallback_label, None)


def _merge_asr_segments(segments: list[dict[str, Any]], max_gap_ms: int = 500,
                        max_dur_ms: int = 30000,
                        speaker_key: str | None = None) -> list[dict[str, Any]]:
    """Fusiona segmentos consecutivos separados por una micropausa (< max_gap_ms).

    Whisper+VAD corta en cualquier pausa mínima, incluso en medio de una frase
    ("incidente 2018-" / "13-61,"). Fusionar huecos < 0.5 s recompone la frase.

    CON `speaker_key` solo se fusionan segmentos del MISMO hablante: así una réplica
    rápida de otra persona (interrupción < 0.5 s) jamás queda pegada al turno ajeno.
    Una pausa larga (> max_gap_ms) siempre abre un registro nuevo, aunque hable la
    misma persona. La confianza del fusionado es el promedio ponderado por duración."""
    if not segments:
        return segments
    merged: list[dict[str, Any]] = [
        dict(segments[0], words=list(segments[0].get("words", [])))
    ]
    for seg in segments[1:]:
        prev = merged[-1]
        gap = seg["start_ms"] - prev["end_ms"]
        same_speaker = not speaker_key or prev.get(speaker_key) == seg.get(speaker_key)
        if same_speaker and 0 <= gap <= max_gap_ms and (seg["end_ms"] - prev["start_ms"]) <= max_dur_ms:
            d_prev = max(1, prev["end_ms"] - prev["start_ms"])
            d_seg = max(1, seg["end_ms"] - seg["start_ms"])
            prev["confidence"] = (prev.get("confidence", 0.0) * d_prev
                                  + seg.get("confidence", 0.0) * d_seg) / (d_prev + d_seg)
            prev["end_ms"] = seg["end_ms"]
            sep = "" if prev["text"].rstrip().endswith("-") else " "
            prev["text"] = (prev["text"].rstrip() + sep + seg["text"].lstrip()).strip()
            prev["words"] = prev.get("words", []) + seg.get("words", [])
        else:
            merged.append(dict(seg, words=list(seg.get("words", []))))
    return merged


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
    ctx_prompt, _known_names = _case_context(conn, case_id, org_id)
    log.info("ASR con contexto del caso: %s", ctx_prompt)
    asr_segments = provider.transcribe(media_path, media["mime_type"],
                                       progress_cb=progress_cb, initial_prompt=ctx_prompt)
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

    # 4b. Auto-presentaciones ("quien les habla, X") y otorgamientos de palabra
    # ("tiene la palabra el doctor X") tienen prioridad: cubren a los hablantes SIN
    # video (p. ej. el juez con la cámara apagada) y corrigen overlays fijos de Teams
    # (p. ej. "Organized by", que NO es el hablante activo). Se canonicalizan contra
    # los nombres ya conocidos del caso.
    if do_diarization:
        intro_names = _extract_self_intro_names(joined)
        floor_names = _extract_floor_grants(joined)
        voice_names = {**floor_names, **intro_names}  # intro > floor
        if voice_names:
            label_names.update(voice_names)
        if label_names:
            label_names = {l: _canonical_name(n, _known_names) or n for l, n in label_names.items()}
            for seg in joined:
                seg["visual_name"] = label_names.get(seg["speaker_label"])
        log.info("Nombres resueltos: %s (voz: %s)", label_names, voice_names)

    # 5. Guardar speakers y segmentos. Primero se resuelve el speaker de cada segmento
    # y LUEGO se fusionan micropausas SOLO entre segmentos del mismo hablante: una
    # réplica rápida de otra persona nunca queda pegada al turno ajeno; una pausa
    # larga (> 0.5 s) siempre abre un registro nuevo aunque hable la misma persona.
    for seg in joined:
        seg["speaker_id"] = _get_or_create_speaker(
            conn, org_id, case_id, media_id, seg["speaker_label"], seg.get("visual_name"))
    n_pre_merge = len(joined)
    joined = _merge_asr_segments(joined, speaker_key="speaker_id")
    if len(joined) != n_pre_merge:
        log.info("Segmentos fusionados por hablante (micropausas): %d -> %d", n_pre_merge, len(joined))
    needs_review_count = 0
    for seg in joined:
        speaker_id = seg["speaker_id"]
        needs_review = seg["confidence"] < s.ASR_CONFIDENCE_THRESHOLD
        if needs_review:
            needs_review_count += 1
        one(conn, """
            INSERT INTO transcript_segments (
                organization_id, media_id, speaker_id, start_ms, end_ms, text, confidence, needs_review, language, words
            ) VALUES (
                :o, :m, :spk, :s, :e, :t, :c, :r, :lang, :w
            )
            RETURNING id
        """,
            o=org_id, m=media_id, spk=speaker_id,
            s=seg["start_ms"], e=seg["end_ms"], t=seg["text"],
            c=seg["confidence"], r=needs_review, lang="es",
            w=json.dumps(seg.get("words") or [], ensure_ascii=False))

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
        SELECT id, start_ms, end_ms, text, confidence, words
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
        timeline: list[dict[str, Any]] = []

        # 1) Identificación VISUAL de Teams PRIMERO (independiente de pyannote): sus
        #    nombres inequívocos sirven de SEMILLAS para agrupar la voz a lo largo del
        #    video (une fragmentos de la misma persona entre tramos).
        if media["media_type"] == "video":
            progress(0.62, "Identificando hablantes (video)…")

            def _vis_progress(done: int, total: int) -> None:
                if total:
                    frac = min(1.0, max(0.0, done / total))
                    progress(0.62 + 0.10 * frac,
                             f"Identificando hablantes (video)… {int(frac * 100)}%")

            try:
                timeline = extract_active_speaker_timeline(
                    media_path, media["mime_type"],
                    step_s=float(getattr(s, "VISUAL_ID_STEP_SECONDS", 5.0) or 5.0),
                    progress_cb=_vis_progress)
            except Exception as exc:
                log.warning("Identificación visual falló para %s: %s", media["filename"], exc)

        # 2) Diarización (con las semillas visuales disponibles ya). Devuelve también los
        #    centroides de voz por hablante (para detectar habla superpuesta después).
        centroids: dict[str, list[float]] = {}
        try:
            diarization, centroids, _seed_names = diarize(
                media_path, media["mime_type"], progress_cb=_diar_progress,
                visual_names=timeline, return_centroids=True)
            log.info("Diarización produjo %d segmentos para %s (%d voz/voces de referencia)",
                     len(diarization), media["filename"], len(centroids))
        except Exception as exc:
            log.warning("Diarización falló para %s: %s", media["filename"], exc)
        progress(0.95, "Diarización lista; identificando hablantes…")

        # Nombre por TURNO de diarización, tomado de la identificación VISUAL de Teams:
        # el nombre RESALTADO es el de quien habla en ese momento. Corrige los clusters
        # de audio que mezclaron a dos personas (p. ej. el juez con un apoderado).
        # Antes se descarta el overlay ESTÁTICO (p. ej. "Organized by: X" = quien grabó):
        # si aparece en >60 % de un video largo NO es el hablante activo.
        span_ms = max((d["end_ms"] for d in diarization), default=0)
        timeline = _filter_static_overlay(timeline, span_ms)

        from collections import Counter as _Counter
        for d in diarization:
            # El nombre-semilla (de un tramo con UN resaltado claro) manda; si no hay,
            # la mayoría visual del turno, exigiendo 70 % de acuerdo (con dos nombres
            # resaltados a la vez en Teams el visual es ambiguo y no se usa).
            vote_names = [e["name"] for e in timeline
                          if d["start_ms"] <= e["timestamp_ms"] <= d["end_ms"]]
            visual_turn: str | None = None
            if vote_names:
                nm, cnt = _Counter(vote_names).most_common(1)[0]
                runner = _Counter(vote_names).most_common(2)[1][1] if len(set(vote_names)) > 1 else 0
                if cnt / len(vote_names) >= 0.7 and runner / len(vote_names) < 0.25:
                    visual_turn = nm
            d["name"] = d.get("seed_name") or visual_turn

        label_names = _resolve_label_names(diarization, timeline)

        def _best_turn(start_ms: int, end_ms: int) -> dict[str, Any] | None:
            best = None
            best_overlap = 0
            for d in diarization:
                overlap = max(0, min(end_ms, d["end_ms"]) - max(start_ms, d["start_ms"]))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best = d
            return best

        # Evidencia de VOZ con TIEMPO (auto-presentaciones y otorgamiento de palabra).
        joined_pre = []
        for sg in segs:
            turn = _best_turn(sg["start_ms"], sg["end_ms"])
            joined_pre.append({
                "start_ms": sg["start_ms"], "end_ms": sg["end_ms"], "text": sg["text"],
                "speaker_label": turn["label"] if turn else "UNKNOWN",
                "turn_name": (turn.get("name") if turn else None),
            })
        intro_events, floor_events = _name_events(joined_pre)

        # Cluster MIXTO: la diarización fusionó a 2+ personas en un mismo label (p. ej.
        # la jueza y un testigo). Se detecta con ≥2 auto-presentaciones distintas y se
        # PARTE por bloques: cada presentación inicia el bloque de ese hablante.
        intro_blocks_by_label: dict[str, list[tuple[int, str]]] = {}
        for t_ms, nm in intro_events:
            lbl = next((s["speaker_label"] for s in joined_pre
                        if s["start_ms"] <= t_ms <= s["end_ms"]), None)
            if lbl:
                intro_blocks_by_label.setdefault(lbl, []).append((t_ms, nm))
        mixed_labels = {l for l, evs in intro_blocks_by_label.items()
                        if len({_norm_name(nm) for _, nm in evs}) > 1}
        if mixed_labels:
            log.warning("Clusters MIXTOS (2+ personas juntas en un label): %s — se parten "
                        "por bloques de presentación y lo sin evidencia queda para revisión",
                        sorted(mixed_labels))

        _ctx_prompt, known_names = _case_context(conn, case_id, org_id)
        label_names = {l: _canonical_name(n, known_names) or n for l, n in label_names.items()}

        # Nombre final por TURNO (un turno es un tramo contiguo de UNA voz; aunque el
        # cluster mezcle personas, el turno no). Sirve para partir segmentos que cruzan
        # un cambio de hablante.
        for d in diarization:
            if d["label"] in mixed_labels:
                d["final_name"] = _name_in_mixed_blocks(intro_blocks_by_label.get(d["label"], []),
                                                        d["start_ms"], d["end_ms"])
            else:
                d["final_name"] = d.get("name") or label_names.get(d["label"])

        needs_review_count = 0
        labels_seen: set[str] = set()
        log_live = bool(getattr(s, "ASR_LOG_LIVE", False))

        def _turns_overlapping(sg_start: int, sg_end: int) -> list[tuple[int, dict]]:
            ovs = []
            for d in diarization:
                ov = max(0, min(sg_end, d["end_ms"]) - max(sg_start, d["start_ms"]))
                if ov > 0:
                    ovs.append((ov, d))
            ovs.sort(key=lambda x: -x[0])
            return ovs

        # Asignación de hablante por segmento (en memoria, sin tocar la BD aún).
        assigned: list[dict[str, Any]] = []
        for sg, pre in zip(segs, joined_pre):
            label = pre["speaker_label"]
            labels_seen.add(label)
            ovs = _turns_overlapping(sg["start_ms"], sg["end_ms"])

            # 0) ¿El segmento cruza un cambio de hablante? (2 turnos con nombres distintos
            #    y ambos con solape >= 30 % del mayor) → se PARTE en el borde del turno.
            #    Nunca se deja un registro hablando por dos personas.
            if len(ovs) > 1:
                (o1, t1), (o2, t2) = ovs[0], ovs[1]
                boundary = t2["start_ms"] if t2["start_ms"] >= t1["start_ms"] else t1["start_ms"]
                boundary = min(max(boundary, sg["start_ms"] + 300), sg["end_ms"] - 300)
                if label in mixed_labels:
                    # Cluster mixto: cada mitad se nombra con los BLOQUES DE PRESENTACIÓN
                    # (los turnos vecinos pueden pertenecer a un cluster-fragmento mal
                    # agrupado y dar un nombre equivocado).
                    blocks = intro_blocks_by_label.get(label, [])
                    n1 = _canonical_name(_name_in_mixed_blocks(blocks, sg["start_ms"], boundary)
                                         or t1.get("final_name"), known_names)
                    n2 = _canonical_name(_name_in_mixed_blocks(blocks, boundary, sg["end_ms"])
                                         or t2.get("final_name"), known_names)
                else:
                    n1 = _canonical_name(t1.get("final_name"), known_names)
                    n2 = _canonical_name(t2.get("final_name"), known_names)
                if n1 and n2 and _norm_name(n1) != _norm_name(n2) and o2 >= 0.3 * o1:
                    log.info("Segmento %d–%d s cruza hablantes (%s | %s): se parte en %d s y se "
                             "marca para revisión", sg["start_ms"] // 1000, sg["end_ms"] // 1000,
                             n1, n2, boundary // 1000)
                    # Corte EXACTO con timestamps por palabra si están disponibles;
                    # si no (medios previos a la migración 0040), proporcional + Revisar.
                    exact = _split_segment_by_words(sg, boundary)
                    if exact:
                        (p1, p2) = exact
                        boundary = p1["end_ms"]  # borde real de la última palabra de la 1ª parte
                        parts = (
                            (n1, p1["start_ms"], p1["end_ms"], p1["text"], p1["words"], True, False),
                            (n2, p2["start_ms"], p2["end_ms"], p2["text"], p2["words"], False, False),
                        )
                    else:
                        frac = (boundary - sg["start_ms"]) / max(1, sg["end_ms"] - sg["start_ms"])
                        text1, text2 = _split_text_at(sg["text"] or "", frac)
                        parts = (
                            (n1, sg["start_ms"], boundary, text1, None, True, True),
                            (n2, boundary, sg["end_ms"], text2, None, False, True),
                        )
                    for part_name, p_start, p_end, p_text, p_words, keep_id, inexact in parts:
                        speaker_id = _get_or_create_speaker_named(conn, org_id, case_id, media_id,
                                                                  part_name, label)
                        assigned.append({
                            "id": str(sg["id"]) if keep_id else None,
                            "start_ms": p_start, "end_ms": p_end,
                            "text": p_text, "words": p_words,
                            "confidence": sg["confidence"],
                            "speaker_id": speaker_id, "speaker_name": part_name,
                            "needs_review": inexact,
                        })
                    continue

            visual = pre.get("turn_name")
            # 1) Evidencia de VOZ del momento (la más fiable: el hablante dice su nombre
            #    o se le otorga la palabra justo antes).
            voice = _name_from_events(intro_events, sg["start_ms"], sg["end_ms"], _INTRO_WINDOW_MS) \
                or _name_from_events(floor_events, sg["start_ms"], sg["end_ms"], _FLOOR_WINDOW_MS)
            if label in mixed_labels:
                # Cluster mixto: se parte por bloques de presentación; sin bloque, la voz
                # del momento; sin evidencia, queda para revisión (nunca un nombre inventado).
                name = _name_in_mixed_blocks(intro_blocks_by_label.get(label, []),
                                             sg["start_ms"], sg["end_ms"]) or voice
            else:
                # Prioridad: voz del momento > visual del turno > nombre del cluster.
                # El visual solo cuenta si PARECE un nombre de persona (no un correo).
                visual = visual if _looks_like_person_name(visual) else None
                name = voice or visual or label_names.get(label)
            if voice and visual and _norm_name(voice) != _norm_name(visual):
                log.warning("Conflicto de nombre (%s) en %d s: visual=%r vs voz=%r; se usa la VOZ",
                            label, sg["start_ms"] // 1000, visual, voice)
            name = _canonical_name(name, known_names)
            needs_review = (name is None) or (sg["confidence"] or 0.0) < s.ASR_CONFIDENCE_THRESHOLD
            if name is None:
                log.info("Segmento %d–%d s sin nombre fiable (cluster %s mixto o sin evidencia): "
                         "queda para revisión", sg["start_ms"] // 1000, sg["end_ms"] // 1000, label)
            speaker_id = _get_or_create_speaker_named(conn, org_id, case_id, media_id, name, label)
            assigned.append({
                "id": str(sg["id"]), "start_ms": sg["start_ms"], "end_ms": sg["end_ms"],
                "text": sg["text"], "words": sg.get("words"),
                "confidence": sg["confidence"],
                "speaker_id": speaker_id, "speaker_name": name or f"Por revisar ({label})",
                "needs_review": needs_review, "label": label,
            })

        # 2b) Análisis de VOZ: hay segmentos que contienen DOS personas hablando a la vez
        #     (habla superpuesta) y pyannote los ve como un solo turno. Se comparan los
        #     embeddings de locutor a cada lado del posible corte y se parte en dos
        #     registros, cada uno con el hablante cuya VOZ más se parece.
        if centroids:
            try:
                from app.services import voice_split
                by_id = {a.get("id"): a for a in assigned if a.get("id")}
                voice_out = voice_split.split_by_voice_change(
                    media_path,
                    [{"id": a.get("id"), "start_ms": a["start_ms"], "end_ms": a["end_ms"],
                      "text": a["text"], "words": a.get("words"), "confidence": a["confidence"],
                      "speaker_id": a["speaker_id"], "name": a["speaker_name"],
                      "label": a.get("label") or "UNKNOWN"} for a in assigned],
                    centroids, label_names)
                rebuilt: list[dict[str, Any]] = []
                for v in voice_out:
                    if v.get("split_reason") == "voice_change":
                        spk = _get_or_create_speaker_named(conn, org_id, case_id, media_id,
                                                          v.get("name"), v.get("label") or "UNKNOWN")
                        rebuilt.append({
                            "id": v.get("id"), "start_ms": v["start_ms"], "end_ms": v["end_ms"],
                            "text": v["text"], "words": v.get("words"),
                            "confidence": v.get("confidence"), "speaker_id": spk,
                            "speaker_name": v.get("name"), "needs_review": False,
                            "label": v.get("label") or "UNKNOWN",
                        })
                    else:
                        orig = by_id.get(v.get("id"))
                        rebuilt.append(orig if orig is not None else {
                            "id": v.get("id"), "start_ms": v["start_ms"], "end_ms": v["end_ms"],
                            "text": v["text"], "words": v.get("words"),
                            "confidence": v.get("confidence"), "speaker_id": v.get("speaker_id"),
                            "speaker_name": v.get("name"), "needs_review": False,
                            "label": v.get("label") or "UNKNOWN",
                        })
                assigned = rebuilt
            except Exception:  # noqa: BLE001 — el análisis de voz no debe tumbar la diarización
                log.exception("el análisis de voz intra-segmento falló (media %s)", media_id)

        # 2) Fusión de micropausas SOLO entre segmentos del MISMO hablante: una réplica
        #    rápida de otra persona nunca queda pegada al turno ajeno; una pausa larga
        #    (> 0.5 s) abre un registro nuevo aunque hable la misma persona.
        merged = _merge_asr_segments(assigned, speaker_key="speaker_id")
        if len(merged) != len(assigned):
            log.info("Segmentos fusionados por hablante (micropausas): %d -> %d",
                     len(assigned), len(merged))

        # 3) Persistir: segmentos conservados → UPDATE; piezas partidas nuevas → INSERT;
        #    los absorbidos por la fusión → DELETE.
        from app.providers.asr import _fmt_ms
        kept = {m["id"] for m in merged if m.get("id")}
        removed = [str(sg["id"]) for sg in segs if str(sg["id"]) not in kept]
        for m in merged:
            needs_review = bool(m.get("needs_review")) or (m["confidence"] or 0.0) < s.ASR_CONFIDENCE_THRESHOLD
            if needs_review:
                needs_review_count += 1
            if m.get("id"):
                one(conn, """
                    UPDATE transcript_segments
                    SET speaker_id = :spk, start_ms = :s, end_ms = :e, text = :t,
                        confidence = :c, needs_review = :r, words = :w
                    WHERE id = :i RETURNING id
                """, spk=m["speaker_id"], s=m["start_ms"], e=m["end_ms"], t=m["text"],
                    c=m["confidence"], r=needs_review, i=m["id"],
                    w=json.dumps(m["words"], ensure_ascii=False) if m.get("words") else None)
            else:
                one(conn, """
                    INSERT INTO transcript_segments (
                        organization_id, media_id, speaker_id, start_ms, end_ms, text,
                        confidence, needs_review, language, words
                    ) VALUES (:o, :m, :spk, :s, :e, :t, :c, :r, 'es', :w) RETURNING id
                """, o=org_id, m=media_id, spk=m["speaker_id"], s=m["start_ms"],
                    e=m["end_ms"], t=m["text"], c=m["confidence"], r=needs_review,
                    w=json.dumps(m["words"], ensure_ascii=False) if m.get("words") else None)
            if log_live:
                log.info("FINAL [%s–%s] %s: %s",
                         _fmt_ms(m["start_ms"]), _fmt_ms(m["end_ms"]),
                         m["speaker_name"], (m["text"] or "")[:120])
        if removed:
            conn.execute(
                text("DELETE FROM transcript_segments WHERE media_id = :m AND id = ANY(:ids)"),
                {"m": media_id, "ids": removed},
            )

        # Fusiona hablantes que quedaron con el MISMO nombre resuelto (pyannote suele
        # partir a una persona en dos clusters y la visión/intro les pone el mismo
        # nombre): evita duplicados en la lista y el selector de hablantes.
        try:
            from app.services import speakers as speakers_service
            dups = speakers_service.dedupe_by_name(conn, case_id)
            if dups:
                log.info("Hablantes fusionados por nombre (media %s): %s", media_id, dups)
        except Exception:  # noqa: BLE001 — la fusión no debe tumbar la diarización
            log.exception("no se pudieron fusionar hablantes duplicados (caso %s)", case_id)

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
