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
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.services import text_terms

log = logging.getLogger(__name__)

# El LLM solo revisa lo que la regla NO decidió con seguridad. Las reglas fuertes
# (0.9) y los guardarraíles (adjuntos, corrección, hint) nunca se consultan.
LLM_REVIEW_MAX = 0.9
LLM_MIN_ACCEPT = 0.5

# Se sube a mano cuando cambian las reglas o el umbral. Viaja con cada turno
# (`chat_messages.router_version`) para poder comparar decisiones entre versiones
# del router sin adivinar cuál estaba desplegada.
ROUTER_VERSION = "rules-2026.11"


class Intent(StrEnum):
    NAVIGATE = "navegar"
    LOOKUP = "dato"
    ANALYSIS = "analisis"
    PROCEDURAL = "procedimental"
    AGGREGATION = "agregacion"
    READ = "lectura"
    CORRECTION = "correccion"


# Razones de regla que ni el LLM ni el perfil pueden reescribir.
GUARDED_REASONS = frozenset({"attachments_present", "explicit_correction_flag", "caller_hint"})


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
    # Solo primera/segunda persona: «el perito está equivocado» es una afirmación
    # sustantiva, no una corrección al sistema.
    r"|\b(estoy|estaba|estabas|estabamos) equivocad"
    r"|\bconfirmo la correccion\b"
    # Formas reales de corregir sin decir «corrige»: «actualiza la data», «no,
    # ella se llama…». Son correcciones de un dato ya dicho, no preguntas.
    r"|\bactualiza(r)? (la )?(data|informacion|nombre|ficha)\b"
    r"|\b(no|nop),? (el|ella) se llama\b"
)

_NAVIGATE = re.compile(
    # "dónde aparece / dónde se habla / dónde se menciona / dónde está"
    r"\bdonde\b[^?]{0,70}\b(aparece|aparecen|se habla|se menciona|se dice|se trata|"
    r"se registra|se encuentra|se discute|se debate|se analiza|se revisa|"
    r"esta|estan|consta|quedo|quedan)\b"
    # "en qué minuto / página / archivo / parte / folio"
    r"|\ben que (minuto|minutos|pagina|paginas|archivo|archivos|parte|partes|"
    r"folio|folios|seccion|momento|timestamp|video|audio|grabacion|documento|documentos)\b"
    r"|\ba que (minuto|minutos|hora|pagina|altura)\b"
    r"|\ben cual (pagina|minuto|archivo|parte|folio)\b"
    r"|\bmomento exacto\b"
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
    # "roles" / "lista de partes" / "dame los nombres"
    r"|\b(cuales|que) (son )?(los|las) (roles|partes|intervinientes|apoderados|jueces|testigos|peritos|nombres)\b"
    r"|\b(cuantos|cuantas)\b[^?]{0,50}\b(documentos|anexos|archivos)\b"
    r"|\b(cuantos|cuantas)\b[^?]{0,50}\b(medidas|actuaciones|audiencias|declaraciones|hechos)\b"
    r"|\b(lista|listado|relacion) de (los |las )?(peritos|testigos|partes|intervinientes|jueces|documentos)\b"
    r"|\b(dame|necesito|quiero|hazme)\b[^?]{0,30}\b(lista|listado|nombres|relacion)\b"
    r"|\bnombres de\b[^?]{0,30}\b(declararon|intervinieron|comparecieron|participaron)\b"
    r"|\bque (intervinientes|partes|testigos|peritos)\b[^?]{0,40}"
    r"\b(comparecieron|declararon|intervinieron|participaron|firmaron)\b"
    r"|\bcuantos son\b"
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
    r"|\bsecuencia (de )?(actuaciones|actos|hechos)\b"
    r"|\bque paso antes\b"
    r"|\brecorrido (del|de) proceso\b"
)

_ANALYSIS = re.compile(
    # `contradic` cubre contradice/contradicción/contradictorio (antes `contradicc`
    # no matcheaba «se contradice», el caso más frecuente).
    r"\bcontradic"
    r"|\bcompar(a|ar|alo|ala|alos|alas|acion|ando|ativo|ativa|ado|ada|ados|adas)\b"
    r"|\brelacion(a|ar|ado|ada|adas|ados|an|ando)?\b"
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
    # «hazme un resumen del escrito»: la forma real de pedirlo en el chat.
    r"|\b(hazme|hacer|dame|necesito|quiero)\b[^?]{0,25}\bun (resumen|sintesis)\b"
    # «necesito entender el archivo / explícamelo»: leer un documento concreto.
    r"|\b(necesito|quiero) entender\b[^?]{0,40}"
    r"\b(archivo|documento|pdf|escrito|contrato|demanda|grabacion|video)\b"
)

# Seguimiento conversacional: «¿y en la página 12?», «y eso qué implica», «los
# mismos». Solo se hereda la intención del turno anterior cuando el texto NO se
# sostiene solo: pregunta corta + marcador deíctico/conectivo.
_FOLLOWUP = re.compile(
    r"^(y|e|pero|entonces|ademas|tambien|ahora)\b"
    r"|\b(eso|esto|esos|esas|aquel|aquella|aquellos|aquellas|el mismo|la misma|"
    r"los mismos|las mismas|lo mismo|ahi|alli|de esos|de esas|del mismo|de la misma)\b"
    r"|\b(y|ahora) (en|el|la|los|las|con|para|sobre|segun)\b"
)
FOLLOWUP_MAX_TOKENS = 10

# Guardarraíles de precisión sobre las dos clases caras. `navegar` responde con
# una lista de menciones y gasta 0 LLM; `correccion` abre el flujo determinista.
# Una pregunta sustantiva disfrazada de «dónde está» o un «corrige mi duda» no
# deben caer ahí.
_ABSTRACT_LOCATION = re.compile(
    r"\bdonde\b[^?]{0,50}\b(contradiccion|contradicciones|diferencia|diferencias|"
    r"problema|problemas|relacion|relaciones|error|errores|inconsistencia|"
    r"inconsistencias|sentido|fundamento|motivo)\b"
)
_FACT_LOCATION = re.compile(
    r"\b(localiza|localizar|localizame|ubica|ubicame)\s+(el|la|los|las)\s+"
    r"(fecha|monto|valor|nombre|nombres|cifra|numero|precio|plazo)\b"
)
_META_CORRECTION = re.compile(
    r"\bcorrige\b[^?]{0,20}\b(mi|mi propio)\s+"
    r"(entendimiento|duda|interpretacion|lectura|conclusion)\b"
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


def _call_with_timeout(llm: Any, system: str, user: str, timeout: float) -> str:
    """El LLM no puede comerse el deadline del turno: se corta a los N segundos.

    `LLM_TIMEOUT_SECONDS` (60 s en producción) es para redactar respuestas, no
    para clasificar. Si el proveedor se cuelga, decide la regla.
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(_llm_text, llm, system, user).result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)


# Cortacircuitos: tras 3 timeouts seguidos, el L2 queda apagado 5 minutos. Evita
# acumular hilos colgados y latencia durante una caída del proveedor.
_LLM_TRIP = {"failures": 0, "until": 0.0}


def _llm_available() -> bool:
    return time.monotonic() >= _LLM_TRIP["until"]


def _trip_llm() -> None:
    _LLM_TRIP["failures"] += 1
    if _LLM_TRIP["failures"] >= 3:
        _LLM_TRIP["until"] = time.monotonic() + 300.0
        _LLM_TRIP["failures"] = 0


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


def _previous_intent(history: list[dict] | None) -> Intent | None:
    """Última intención del asistente en el historial (para seguimientos)."""
    for turn in reversed(history or []):
        if not isinstance(turn, dict):
            continue
        if str(turn.get("role") or "") != "assistant":
            continue
        value = turn.get("intent") or turn.get("intent_value")
        intent = _coerce_intent(value)
        if intent is not None:
            return intent
    return None


def _followup_decision(question: str, folded: str, history: list[dict] | None,
                       current: IntentDecision) -> IntentDecision | None:
    """Hereda la intención anterior si la pregunta es un seguimiento.

    «¿Y en la página 12?» no tiene señal propia: sin el turno anterior el router
    cae al cajón de `dato` y responde otra cosa. Heredar es más honesto que
    adivinar. Límites: no se hereda `correccion` (flujo determinista), no se
    hereda `navegar` sin señal de ubicación y una regla fuerte nunca se pisa.
    """
    previous = _previous_intent(history)
    if previous is None or previous is Intent.CORRECTION:
        return None
    if current.confidence >= 0.9 or current.reason in ("attachments_present", "caller_hint"):
        return None
    if previous is Intent.NAVIGATE and not has_location_signal(question):
        return None
    tokens = [token for token in folded.split() if token]
    if len(tokens) > FOLLOWUP_MAX_TOKENS or not _FOLLOWUP.search(folded):
        return None
    nav_term = text_terms.specific_term(question) if previous is Intent.NAVIGATE else None
    return IntentDecision(previous, 0.72, "followup_inherit", nav_term, "rules")


def _ask_llm(question: str, llm: Any, history: list[dict] | None = None) -> IntentDecision | None:
    """Revisión barata de la banda ambigua (regla < 0.9).

    El modelo no puede saltarse los guardarraíles: si devuelve `navegar` sin señal
    de ubicación o `correccion` (que es determinista), se descarta. Si devuelve
    basura o confianza baja, decide la regla.
    """
    previous = _previous_intent(history)
    system = (
        "Clasificas preguntas de un asistente jurídico en UNA de estas 7 intenciones:\n"
        "- navegar: pide una UBICACIÓN explícita (minuto, página, archivo, timestamp).\n"
        "- dato: pide un hecho concreto que está en el expediente.\n"
        "- analisis: compara, contradicciones, implicaciones, fortalezas.\n"
        "- procedimental: por qué/cómo se llegó a una actuación; cadena procesal.\n"
        "- agregacion: cuántos/quiénes; listas de roles o intervinientes.\n"
        "- lectura: pide leer/resumir/sintetizar un documento o adjunto.\n"
        "- correccion: corrige un hablante o un dato ya dicho.\n"
        'Devuelve SOLO JSON: {"intent": "...", "confidence": 0.0, "reason": "..."}. '
        "Si dudas entre dato y otra, elige dato. No expliques nada fuera del JSON."
    )
    context = f"Intención del turno anterior: {previous.value}.\n" if previous else ""
    user = f"{context}Pregunta: {question}"
    try:
        from app.core.config import get_settings

        timeout = float(getattr(get_settings(), "INTENT_LLM_TIMEOUT_S", 2.5))
    except Exception:  # noqa: BLE001 — sin settings no hay L2
        timeout = 2.5
    try:
        raw = _call_with_timeout(llm, system, user, timeout)
    except FuturesTimeout:
        log.warning("intent llm timeout after %.1fs: decide la regla", timeout)
        _trip_llm()
        return None
    except Exception as exc:  # noqa: BLE001 — el LLM es opcional
        log.warning("intent llm failed: %s", exc)
        _trip_llm()
        return None
    _LLM_TRIP["failures"] = 0
    data = _parse_json_object(raw)
    if not data:
        return None
    intent = _coerce_intent(data.get("intent"))
    if intent is None:
        return None
    if intent is Intent.CORRECTION:
        return None  # la corrección la decide el flujo determinista
    if intent is Intent.NAVIGATE and not has_location_signal(question):
        return None
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    confidence = min(1.0, max(0.0, confidence))
    if confidence < LLM_MIN_ACCEPT:
        return None
    reason = data.get("reason")
    reason = reason.strip()[:120] if isinstance(reason, str) and reason.strip() else "llm_router"
    if not _reason_is_safe(reason, question):
        reason = "llm_router"
    nav_term = text_terms.specific_term(question) if intent is Intent.NAVIGATE else None
    return IntentDecision(intent=intent, confidence=confidence, reason=reason,
                          nav_term=nav_term, source="llm")


# Caché corta del L2: la misma pregunta repetida no paga dos veces el modelo.
_LLM_CACHE: dict[str, tuple[float, IntentDecision | None]] = {}
_LLM_CACHE_TTL = 3600.0
_LLM_CACHE_MAX = 512


def _llm_cached(question: str, folded: str, llm: Any,
                history: list[dict] | None) -> IntentDecision | None:
    import time

    key = (f"{getattr(llm, 'model', None) or getattr(llm, 'model_name', None) or id(llm)}|"
           f"{folded}|{(_previous_intent(history) or '').__str__()}")
    now = time.monotonic()
    hit = _LLM_CACHE.get(key)
    if hit and now - hit[0] < _LLM_CACHE_TTL:
        return hit[1]
    decision = _ask_llm(question, llm, history)
    if len(_LLM_CACHE) >= _LLM_CACHE_MAX:
        _LLM_CACHE.clear()
    _LLM_CACHE[key] = (now, decision)
    return decision


def intent_llm():
    """Modelo barato para la banda ambigua, si `LLM_ROUTING` lo define.

    `intent` manda; si no está, se reutiliza el de `summary` (mismo perfil de
    coste). Sin routing configurado devuelve None y el router queda 100 % reglas.
    """
    try:
        from app.core.config import get_settings

        settings = get_settings()
        if not getattr(settings, "INTENT_LLM_ENABLED", True):
            return None
        routing = settings.LLM_ROUTING or {}
        task = "intent" if "intent" in routing else ("summary" if "summary" in routing else None)
        if task is None:
            return None
        from app.providers.llm import get_llm_for_task

        return get_llm_for_task(task)
    except Exception as exc:  # noqa: BLE001 — el L2 es opcional
        log.debug("intent llm unavailable: %s", exc)
        return None


def _should_ask_llm(decision: IntentDecision) -> bool:
    if not _llm_available():
        return False
    if decision.confidence >= LLM_REVIEW_MAX:
        return False
    return decision.reason not in GUARDED_REASONS and decision.reason != "followup_inherit"


def classify(question: str, *, has_attachments: bool = False,
             is_correction: bool = False, history: list[dict] | None = None,
             hints: list[str] | None = None, llm=None) -> IntentDecision:
    """Clasifica la pregunta con reglas deterministas (y LLM opcional de refuerzo).

    Orden: guardarraíles → reglas → seguimiento (historial) → L2 (banda ambigua).
    """
    raw = (question or "").strip()
    folded = text_terms.norm(raw)

    if is_correction:
        return IntentDecision(Intent.CORRECTION, 1.0, "explicit_correction_flag",
                              None, "override")

    matches: list[tuple[Intent, str]] = []
    if folded:
        for intent, pattern, reason in _ORDERED_RULES:
            if not pattern.search(folded):
                continue
            if intent is Intent.NAVIGATE and (
                    _ABSTRACT_LOCATION.search(folded) or _FACT_LOCATION.search(folded)):
                continue
            if intent is Intent.CORRECTION and _META_CORRECTION.search(folded):
                continue
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

    if not has_attachments:
        inherited = _followup_decision(raw, folded, history, decision)
        if inherited is not None:
            return inherited

    if llm is not None and _should_ask_llm(decision):
        revised = _llm_cached(raw, folded, llm, history)
        if revised is not None:
            return revised
    return decision


def is_navigation(decision: IntentDecision) -> bool:
    """Azúcar para los routers: solo `navegar` habilita `locate`."""
    return decision.intent is Intent.NAVIGATE


def has_location_signal(question: str) -> bool:
    """¿La pregunta pide una ubicación explícita? (misma señal que `_NAVIGATE`).

    Incluye los guardarraíles de precisión: «¿Dónde está la contradicción?» y
    «localiza el nombre del perito» NO son peticiones de ubicación, aunque
    contengan «dónde» o «localiza». Lo usan el perfil, el L2 y `occurrences`:
    si dijera que sí, la pregunta sustantiva acabaría en una lista de menciones.
    """
    folded = text_terms.norm(question or "")
    if _ABSTRACT_LOCATION.search(folded) or _FACT_LOCATION.search(folded):
        return False
    return bool(_NAVIGATE.search(folded))


def uses_graph(decision: IntentDecision) -> bool:
    return decision.intent in (Intent.LOOKUP, Intent.ANALYSIS)


__all__ = [
    "Intent",
    "IntentDecision",
    "ROUTER_VERSION",
    "classify",
    "has_location_signal",
    "is_navigation",
    "uses_graph",
]
