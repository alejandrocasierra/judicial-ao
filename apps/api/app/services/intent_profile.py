"""Perfil de intención por usuario: voto kNN ponderado sobre ejemplos propios.

No es fine-tuning ni un clasificador entrenado a ciegas: es una memoria pequeña
(≤ 200 ejemplos) de preguntas que ESTE usuario ya resolvió, con la etiqueta que
el propio usuario confirmó (o que se infirió de una reformulación exitosa).

Puertas de seguridad (lo que impide que la personalización reabra el bug de los
timestamps):

- Una regla fuerte (`confidence >= 0.9`) no se toca: el perfil solo registra el
  desacuerdo. Tampoco se toca un turno con adjuntos, corrección explícita o hint.
- El perfil solo puede decidir entre las 7 intenciones cuando la regla quedó en
  `default_lookup` (0.45) o en una coincidencia múltiple (0.62).
- `navegar` sigue exigiendo señal explícita de ubicación: el perfil no puede
  inventarla, solo puede elegirla cuando la pregunta ya la tiene.
- `correccion` es del flujo determinista: el perfil nunca la produce.
- Si el margen del voto es bajo, no decide. Ante cualquier error, decide la regla.

El coste en el turno es un SELECT por `user_id` + un embedding. Nunca corre en
las reglas fuertes, así que `navegar` conserva su presupuesto de latencia.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterable

from app.core.db import one, rows, tx
from app.services import text_terms
from app.services.intent import GUARDED_REASONS, Intent, IntentDecision, has_location_signal

log = logging.getLogger(__name__)

STRONG_RULE_CONFIDENCE = 0.9
PROFILE_MARGIN_MIN = 0.15
MIN_NEIGHBORS = 2
MIN_SIMILARITY = 0.55
MAX_NEIGHBORS = 12
HALF_LIFE_DAYS = 90.0
SOURCE_WEIGHT = {"feedback": 1.0, "corpus": 0.8, "counterfactual": 0.4}

# Sonda de capacidad: pares con la MISMA intención pero poco solapamiento léxico, y
# pares con intención distinta. Un proveedor léxico (hash de n-gramas) no puede
# separarlos; un modelo semántico sí. Es lo que autoriza a activar el perfil, en
# lugar de confiar en el nombre del proveedor.
PROBE_MIN_SAME = 0.55
PROBE_MIN_GAP = 0.08
_PROBE = (
    ("¿Cuándo se firmó el contrato?", "¿En qué fecha se suscribió el contrato?", True),
    ("¿Quién compareció a la audiencia?", "¿Qué personas asistieron a la audiencia?", True),
    ("¿Cuánto se pagó?", "¿Qué monto se pagó?", True),
    ("¿Dónde aparece la cláusula de pago?", "¿En qué página está la cláusula de pago?", True),
    ("¿Cuándo se firmó el contrato?", "¿Quién firmó el contrato?", False),
    ("¿Cuánto se pagó?", "¿Dónde se menciona el pago?", False),
    ("¿Qué dijo el testigo?", "¿Cuántos testigos declararon?", False),
)


@dataclass(frozen=True)
class Vote:
    label: str
    margin: float
    score: float
    neighbors: int


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    """Similitud coseno tolerante a vectores no normalizados (OpenAI no lo hace)."""
    va, vb = list(a), list(b)
    if not va or len(va) != len(vb):
        return 0.0
    dot = sum(x * y for x, y in zip(va, vb, strict=False))
    na = sum(x * x for x in va) ** 0.5
    nb = sum(y * y for y in vb) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def recency_weight(created_at: datetime | None, now: datetime | None = None) -> float:
    """0,5^(días/90): el estilo de hace un año no manda sobre el de ayer."""
    if created_at is None:
        return 1.0
    ref = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    days = max(0.0, (ref - created_at).total_seconds() / 86400.0)
    return 0.5 ** (days / HALF_LIFE_DAYS)


def probe_embeddings(provider) -> dict[str, Any]:
    """¿Este proveedor tiene espacio semántico? Se mide, no se supone.

    Devuelve la similitud media de pares paráfrasis, la de pares con intención
    distinta y el margen. `semantic=False` con `fake` o con un hash léxico: en ese
    caso el perfil se queda en sombra aunque haya veredictos.
    """
    unique = sorted({text for a, b, _ in _PROBE for text in (a, b)})
    vectors = dict(zip(unique, provider.embed(unique), strict=False))
    same: list[float] = []
    different: list[float] = []
    for left, right, same_intent in _PROBE:
        score = cosine(vectors[left], vectors[right])
        (same if same_intent else different).append(score)
    same_mean = sum(same) / len(same) if same else 0.0
    diff_mean = sum(different) / len(different) if different else 0.0
    gap = same_mean - diff_mean
    return {
        "provider": str(getattr(provider, "name", "") or "?"),
        "model": str(getattr(provider, "model_name", None) or getattr(provider, "model", None) or ""),
        "same": round(same_mean, 4),
        "different": round(diff_mean, 4),
        "gap": round(gap, 4),
        "semantic": same_mean >= PROBE_MIN_SAME and gap >= PROBE_MIN_GAP,
    }


def intent_embedding_provider(settings=None):
    """Proveedor de embeddings del PERFIL, separado del índice de documentos.

    El perfil puede usar un modelo semántico aunque el índice siga con el fallback
    léxico: comparar preguntas entre sí no requiere el mismo espacio que buscar
    chunks. Así activar la personalización no obliga a reindexar el expediente.

    La instancia se cachea: `SentenceTransformersEmbedding` carga el modelo en el
    primer `embed` y crear una instancia por turno volvería a cargar ~2 GB.
    """
    from app.core.config import get_settings

    s = settings or get_settings()
    name = s.INTENT_EMBEDDING_BACKEND or s.EMBEDDING_PROVIDER
    model = s.INTENT_EMBEDDING_MODEL or s.EMBEDDING_MODEL
    return _cached_provider(name, model, s.EMBEDDING_DIMENSIONS)


@lru_cache(maxsize=4)
def _cached_provider(name: str, model: str, dimensions: int):
    from app.core.config import get_settings
    from app.providers.embeddings import get_embedding_provider

    s = get_settings()
    return get_embedding_provider(s.model_copy(update={
        "EMBEDDING_PROVIDER": name,
        "EMBEDDING_MODEL": model,
        "EMBEDDING_DIMENSIONS": dimensions,
    }))


@lru_cache(maxsize=8)
def _cached_probe(provider_name: str, model: str) -> dict[str, Any]:
    return probe_embeddings(intent_embedding_provider())


def embedding_capability() -> dict[str, Any]:
    """Sonda cacheada del proveedor configurado para el perfil (una vez por proceso)."""
    provider = intent_embedding_provider()
    name = str(getattr(provider, "name", "") or "")
    model = str(getattr(provider, "model_name", None) or getattr(provider, "model", None) or "")
    return _cached_probe(name, model)


def vote(neighbors: list[dict[str, Any]], now: datetime | None = None) -> Vote | None:
    """Voto ponderado por etiqueta sobre los vecinos ya recuperados por pgvector.

    Cada vecino aporta `similitud · peso_origen · peso_recencia`. El margen
    relativo (mejor - segundo)/mejor decide si hay convicción suficiente.
    """
    scores: dict[str, float] = defaultdict(float)
    used = 0
    for item in neighbors:
        label = str(item.get("label") or "")
        if label not in {i.value for i in Intent}:
            continue
        similarity = float(item.get("similarity") or 0.0)
        if similarity < MIN_SIMILARITY:
            continue
        source = str(item.get("source") or "feedback")
        weight = float(item.get("weight") or SOURCE_WEIGHT.get(source, 0.4))
        scores[label] += similarity * weight * recency_weight(item.get("created_at"), now)
        used += 1
    if used < MIN_NEIGHBORS or not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    label, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (best - second) / best if best > 0 else 0.0
    return Vote(label=label, margin=round(margin, 4), score=round(best, 4), neighbors=used)


def is_guarded(decision: IntentDecision) -> bool:
    """¿La regla es lo bastante fuerte como para que el perfil no la reescriba?"""
    return decision.confidence >= STRONG_RULE_CONFIDENCE or decision.reason in GUARDED_REASONS


def apply_gates(decision: IntentDecision, profile_vote: Vote | None,
                question: str) -> IntentDecision:
    """Decide si el voto del perfil sustituye a la regla. Puro y testeable."""
    if profile_vote is None or is_guarded(decision):
        return decision
    if profile_vote.margin < PROFILE_MARGIN_MIN:
        return decision
    if profile_vote.label == Intent.CORRECTION.value:
        return decision  # la corrección la decide el flujo determinista
    if profile_vote.label == Intent.NAVIGATE.value and not has_location_signal(question):
        return decision  # jamás una lista de menciones sin señal de ubicación
    intent = Intent(profile_vote.label)
    return IntentDecision(
        intent=intent,
        confidence=round(min(0.85, 0.5 + 0.45 * profile_vote.margin), 3),
        reason="user_profile",
        nav_term=text_terms.specific_term(question) if intent is Intent.NAVIGATE else None,
        source="profile",
    )


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in vector) + "]"


def _embed(question: str) -> list[float] | None:
    try:
        return intent_embedding_provider().embed([question])[0]
    except Exception as exc:  # noqa: BLE001 — el perfil es opcional
        log.warning("intent profile embedding failed: %s", exc)
        return None


def intent_model_label(settings=None) -> str:
    """Etiqueta del espacio vectorial del perfil (nunca se mezclan modelos)."""
    provider = intent_embedding_provider(settings)
    return str(getattr(provider, "model_name", None) or getattr(provider, "model", None)
               or getattr(provider, "name", "") or "")


def neighbors(conn, user_id: str, vector: list[float], *, limit: int = MAX_NEIGHBORS,
              exclude_hash: str | None = None, model: str | None = None) -> list[dict[str, Any]]:
    """Vecinos más cercanos del usuario (pgvector, coseno)."""
    sql = """
        SELECT label, source, weight, created_at,
               1 - (embedding <=> CAST(:q AS vector)) AS similarity
          FROM intent_exemplar
         WHERE user_id = :u AND embedding IS NOT NULL
    """
    params: dict[str, Any] = {"u": str(user_id), "q": _vector_literal(vector), "limit": limit}
    if exclude_hash:
        sql += " AND question_hash <> :h"
        params["h"] = exclude_hash
    if model:
        # Un cambio de modelo deja vectores viejos incomparables: se ignoran hasta
        # que el job los re-embedde (misma disciplina que `chunks.embedding_model`).
        sql += " AND embedding_model = :m"
        params["m"] = model
    sql += " ORDER BY embedding <=> CAST(:q AS vector) LIMIT :limit"
    return rows(conn, sql, **params)


def refine(decision: IntentDecision, question: str, *, org_id: str | None,
           user_id: str | None) -> IntentDecision:
    """Aplica el perfil si está `active`. Nunca rompe el turno: ante error, regla.

    La conexión NO se retiene mientras se calcula el embedding (puede ser una
    llamada de red): se lee el estado, se cierra la transacción, se embebe y se
    vuelve a abrir solo para la consulta vectorial.
    """
    if not org_id or not user_id or is_guarded(decision):
        return decision
    try:
        with tx(org_id, user_id) as conn:
            profile = one(conn, "SELECT status FROM intent_profile WHERE user_id = :u",
                          u=str(user_id))
            if not profile or profile.get("status") != "active":
                return decision
        vector = _embed(question)
        if vector is None:
            return decision
        with tx(org_id, user_id) as conn:
            found = neighbors(conn, user_id, vector, model=intent_model_label())
    except Exception as exc:  # noqa: BLE001 — el router sigue siendo la red de seguridad
        log.warning("intent profile skipped: %s", exc)
        return decision
    refined = apply_gates(decision, vote(found), question)
    if refined is not decision:
        log.info("intent profile: %s -> %s (margin=%s)", decision.intent,
                 refined.intent, refined.reason)
    return refined


def status_for(*, judged: int, exemplars: int, rules_accuracy: float | None,
               profile_accuracy: float | None, fp_rules: int, fp_profile: int,
               semantic_embeddings: bool, min_judged: int = 10,
               gain: float = 0.10) -> tuple[str, str]:
    """Política de activación automática (y de reversión). Pura y testeable.

    Solo se activa con evidencia: suficientes turnos juzgados por el usuario, una
    mejora clara sobre las reglas y sin empeorar los falsos positivos de navegar.
    """
    if not exemplars and not judged:
        return "off", "no_data"
    if not semantic_embeddings:
        return "shadow", "embedding_provider_not_semantic"
    if judged < min_judged:
        return ("shadow" if exemplars else "off"), "not_enough_feedback"
    if profile_accuracy is None or rules_accuracy is None:
        return "shadow", "no_metrics"
    if fp_profile > fp_rules:
        return "shadow", "profile_would_add_navegar_fp"
    if profile_accuracy >= rules_accuracy + gain:
        return "active", "profile_beats_rules"
    if profile_accuracy + gain < rules_accuracy:
        return "off", "profile_worse_than_rules"
    return "shadow", "no_clear_gain"
