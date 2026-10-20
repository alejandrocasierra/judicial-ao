"""Router de intención del chat (contrato §1.2).

Determinista y explicable: cada intención se decide con regex sobre la pregunta
normalizada (`text_terms.norm`). El LLM es OPCIONAL y solo se consulta cuando el
llamador lo pasa y la regla quedó por debajo del umbral de confianza; si el
modelo no responde con un JSON válido, se conserva la decisión por reglas.

Regla de oro del contrato: `navegar` NO es el valor por defecto. Solo se activa
con una señal explícita de ubicación («dónde aparece», «en qué minuto/página/
archivo», «localiza», «timestamp», «busca la palabra»). La palabra «aparece»
por sí sola no basta: «¿Qué evidencia hay sobre pagos?» es `dato`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.services import text_terms

log = logging.getLogger(__name__)

LLM_CONFIDENCE_THRESHOLD = 0.5


class Intent(StrEnum):
    NAVIGATE = "navegar"
    LOOKUP = "dato"
    ANALYSIS = "analisis"
    PROCEDURAL = "procedimental"
    AGGREGATION = "agregacion"
    READ = "lectura"
    CORRECTION = "correccion"


@dataclass(frozen=True)
class IntentDecision:
    intent: Intent
    confidence: float
    reason: str
    nav_term: str | None
    source: str


# --- señales -----------------------------------------------------------------
# Todas se escriben normalizadas (minúsculas, sin diacríticos) porque el texto
# de entrada pasa por `text_terms.norm` antes de compararse.

# Roles que convierten «cuántos/quiénes» en una pregunta de agregación.
_ROLE = (
    r"(juez|jueza|jueces|juezas|magistrad[oa]s?|apoderad[oa]s?|testig[oa]s?|"
    r"perit[oa]s?|secretari[oa]s?|partes?|demandant[ea]s?|demandad[oa]s?|"
    r"fiscal(es)?|abogad[oa]s?|defensor(es|a|as)?|representantes?|"
    r"intervinientes?|accionant[ea]s?|accionad[oa]s?|trabajador(es|a|as)?|"
    r"emplead[oa]s?|soci[oa]s?|accionistas?|hereder[oa]s?)"
)

_CORRECTION = re.compile(
    r"\b(corrige|corregir|corrigelo|corrigela|corrijas|correccion|corrigeme|"
    r"rectifica|rectificar|rectificacion)\b"
    r"|\bel hablante es\b"
    r"|\bcambia (el|la|de) hablante\b"
    r"|\bno es\b[^?]{0,40}\bsino\b"
    r"|\ben realidad (es|era|fue)\b"
    r"|\b(esta|estaba|estan) equivocad"
    r"|\bconfirmo la correccion\b"
)

_NAVIGATE = re.compile(
    # "dónde aparece / dónde se habla / dónde se menciona / dónde está"
    r"\bdonde\b[^?]{0,70}\b(aparece|aparecen|se habla|se menciona|se dice|se trata|"
    r"se registra|se encuentra|se discute|se debate|se analiza|se revisa|"
    r"esta|estan|consta|quedo|quedan)\b"
    # "en qué minuto / página / archivo / parte / folio"
    r"|\ben que (minuto|minutos|pagina|paginas|archivo|archivos|parte|partes|"
    r"folio|folios|seccion|momento|timestamp|video|audio|grabacion)\b"
    r"|\ba que (minuto|minutos|hora|pagina)\b"
    r"|\ben cual (pagina|minuto|archivo|parte|folio)\b"
    # verbos explícitos de localización
    r"|\b(localiza|localizar|localizame|localiceme|localice|ubica|ubicame|ubicar|"
    r"ubicacion)\b"
    r"|\btimestamp"
    # "busca la palabra / el término"
    r"|\bbusca la palabra\b|\bbuscar la palabra\b"
    r"|\bbusca el termino\b|\bbuscar el termino\b|\bbusca la frase\b"
    r"|\bdonde dice\b|\bdonde figuran?\b"
)

_AGGREGATION = re.compile(
    r"\b(cuantos|cuantas)\b[^?]{0,50}\b" + _ROLE +
    r"|\b" + _ROLE + r"\b[^?]{0,50}\b(cuantos|cuantas)\b"
    r"|\bquienes son\b"
    r"|\bquienes\b[^?]{0,50}\b(son|intervinieron|participaron|declararon|"
    r"comparecieron|actuaron|firmaron|suscribieron|aparecen)\b"
    # "roles" / "lista de partes"
    r"|\b(cuales|que) (son )?(los|las) (roles|partes|intervinientes|apoderados|jueces|testigos|peritos)\b"
)

_PROCEDURAL = re.compile(
    r"\bpor que\b[^?]{0,70}\b(se llego|llegaron|llego|se profirio|se dicto|"
    r"se tomo|se decidio|se presento|se interpuso|se origino|se causo|se tramito)\b"
    r"|\bque (causo|origino|motivo|condujo|llevo a|genero)\b"
    r"|\bque actuacion(es)?\b"
    r"|\bactuacion(es)? (que )?(origino|precedio|antecedio)\b"
    r"|\bantecedentes\b"
    r"|\bcadena procesal\b|\bhistoria procesal\b|\btrazabilidad procesal\b"
    r"|\bcomo se llego\b"
    r"|\bpor que se\b[^?]{0,40}\b(fallo|sentencio|resolvio|decidio)\b"
)

_ANALYSIS = re.compile(
    r"\bcontradicc"
    r"|\bcompar(a|ar|acion|ando|ativo|ativa)\b"
    r"|\brelacion(a|ar|ado|ada|adas|ados)?\b"
    r"|\bimplica\b|\bimplicacion(es)?\b"
    r"|\bconsecuencia(s)?\b"
    r"|\banaliza|\banalizar|\banalisis\b"
    r"|\bevalua|\bevaluar|\bevaluacion\b"
    r"|\bfortalezas?\b|\bdebilidades?\b"
    r"|\bcoherencia\b|\bconsistencia\b"
    r"|\bque tan (solido|solida|fuerte|creible|consistente)\b"
    r"|\bcomo (se relaciona|impacta|afecta)\b"
)

_READ = re.compile(
    # Imperativo (o petición cortés) + objeto documental: «lee este documento»,
    # «puedes resumir el contrato». Evita el verbo en tercera persona
    # («¿qué lee la demanda?»), que es una pregunta de dato.
    r"(?:^|[¿¡]\s*|por favor\s+|puedes\s+|podrias\s+|me\s+)"
    r"(lee|lees|leer|leeme|leeselo|resume|resumes|resumir|resumeme|sintetiza|sintetizar)\b"
    r"[^?]{0,50}\b(documento|documentos|archivo|archivos|pdf|adjunto|adjuntos|"
    r"contrato|demanda|escrito|transcripcion|grabacion|video|audio|caso|esto|eso)\b"
    # «lee/resume esto» sin objeto explícito.
    r"|(?:^|[¿¡]\s*)(lee|resume|resumeme|sintetiza)\b[^?]{0,40}"
    r"\b(este|esta|estos|estas|el|la|los|las|mi|mis)\b"
)

_ORDERED_RULES: tuple[tuple[Intent, re.Pattern[str], str], ...] = (
    (Intent.CORRECTION, _CORRECTION, "correction_phrase"),
    (Intent.NAVIGATE, _NAVIGATE, "explicit_location_signal"),
    (Intent.AGGREGATION, _AGGREGATION, "count_or_who_role"),
    (Intent.PROCEDURAL, _PROCEDURAL, "procedural_cause"),
    (Intent.ANALYSIS, _ANALYSIS, "analysis_verb"),
    (Intent.READ, _READ, "read_document_request"),
)


def _llm_text(llm: Any, system: str, user: str) -> str:
    result = llm.complete(system, user)
    if isinstance(result, str):
        return result
    return str(getattr(result, "text", "") or "")


def _parse_json_object(raw: str) -> dict | None:
    txt = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        data = json.loads(txt)
    except Exception:
        start, end = txt.find("{"), txt.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(txt[start:end + 1])
        except Exception:
            return None
    return data if isinstance(data, dict) else None


def _coerce_intent(value: object) -> Intent | None:
    if not isinstance(value, str):
        return None
    key = text_terms.norm(value).strip()
    for intent in Intent:
        if key == text_terms.norm(intent.value) or key == text_terms.norm(intent.name):
            return intent
    return None


def _intent_from_hints(hints: list[str]) -> Intent | None:
    """Un hint que sea EXACTAMENTE el nombre de una intención («lectura», «navegar»).

    Los hints del router son prosa larga (recordatorios al agente); buscar
    subcadenas ahí haría que palabras como «corrección» cambiasen el routing de
    una pregunta normal. Solo se acepta el token exacto.
    """
    for raw in hints:
        key = text_terms.norm(str(raw)).strip().strip(".")
        if not key:
            continue
        for intent in Intent:
            if key == text_terms.norm(intent.value) or key == text_terms.norm(intent.name):
                return intent
    return None


def _reason_is_safe(reason: str, question: str) -> bool:
    """El `reason` es explicable pero nunca repite el texto del usuario."""
    folded_reason = text_terms.norm(reason)
    folded_question = text_terms.norm(question).strip(" ¿?¡!")
    if not folded_question:
        return True
    head = folded_question[:24]
    return len(head) < 8 or head not in folded_reason


def _ask_llm(question: str, llm: Any) -> IntentDecision | None:
    system = (
        "Clasificas preguntas de un asistente jurídico. Devuelve SOLO JSON con las claves "
        '{"intent": "...", "confidence": 0.0, "reason": "..."}. '
        "Valores válidos de intent: navegar, dato, analisis, procedimental, agregacion, lectura, correccion. "
        "navegar SOLO si el usuario pide una ubicación explícita (minuto, página, archivo, timestamp). "
        "dato si pide un hecho concreto. No expliques nada fuera del JSON."
    )
    user = f"Pregunta: {question}"
    try:
        raw = _llm_text(llm, system, user)
    except Exception as exc:  # noqa: BLE001 — el LLM es opcional
        log.warning("intent llm failed: %s", exc)
        return None
    data = _parse_json_object(raw)
    if not data:
        return None
    intent = _coerce_intent(data.get("intent"))
    if intent is None:
        return None
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    confidence = min(1.0, max(0.0, confidence))
    reason = data.get("reason")
    reason = reason.strip()[:120] if isinstance(reason, str) and reason.strip() else "llm_router"
    if not _reason_is_safe(reason, question):
        reason = "llm_router"
    nav_term = text_terms.specific_term(question) if intent is Intent.NAVIGATE else None
    return IntentDecision(intent=intent, confidence=confidence, reason=reason,
                          nav_term=nav_term, source="llm")


def classify(question: str, *, has_attachments: bool = False,
             is_correction: bool = False, history: list[dict] | None = None,
             hints: list[str] | None = None, llm=None) -> IntentDecision:
    """Clasifica la pregunta con reglas deterministas (y LLM opcional de refuerzo)."""
    raw = (question or "").strip()
    folded = text_terms.norm(raw)

    if is_correction:
        return IntentDecision(Intent.CORRECTION, 1.0, "explicit_correction_flag",
                              None, "override")

    matches: list[tuple[Intent, str]] = []
    if folded:
        for intent, pattern, reason in _ORDERED_RULES:
            if pattern.search(folded):
                matches.append((intent, reason))

    if matches:
        intent, reason = matches[0]
        # Varias familias a la vez = decisión menos fiable (sin llegar a bloquear).
        confidence = 0.9 if len(matches) == 1 else 0.62
        nav_term = text_terms.specific_term(raw) if intent is Intent.NAVIGATE else None
        decision = IntentDecision(intent, confidence, reason, nav_term, "rules")
    elif has_attachments:
        decision = IntentDecision(Intent.READ, 0.85, "attachments_present", None, "rules")
    elif hints and (hinted := _intent_from_hints(hints)) is not None:
        decision = IntentDecision(hinted, 0.6, "caller_hint", None, "rules")
    else:
        # Default del contrato. Confianza deliberadamente baja: si el llamador
        # aporta un LLM barato, puede revisar este caso.
        decision = IntentDecision(Intent.LOOKUP, 0.45, "default_lookup", None, "rules")

    if llm is not None and decision.confidence < LLM_CONFIDENCE_THRESHOLD:
        revised = _ask_llm(raw, llm)
        if revised is not None:
            return revised
    return decision


def is_navigation(decision: IntentDecision) -> bool:
    """Azúcar para los routers: solo `navegar` habilita `locate`."""
    return decision.intent is Intent.NAVIGATE


def uses_graph(decision: IntentDecision) -> bool:
    return decision.intent in (Intent.LOOKUP, Intent.ANALYSIS)


__all__ = [
    "Intent",
    "IntentDecision",
    "classify",
    "is_navigation",
    "uses_graph",
]
