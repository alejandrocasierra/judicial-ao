"""Aprendizaje automático del perfil de intención (job nocturno).

Construye, por usuario, una memoria de ejemplos a partir de conducta REAL:

1. `intent_feedback` (verdad): «útil» → la etiqueta que se estaba juzgando;
   «no era esto + cuál era» → la intención corregida; «no era esto» a secas →
   se olvida el ejemplo de esa pregunta (no se inventa etiqueta).
2. Contrafactual (inferencia, peso 0.4): un turno que falló (insuficiente,
   navegacional o sin citas) seguido en la misma sesión por una reformulación que
   sí respondió (`direct`/`partial` con citas, preguntada < 10 min después) → la
   pregunta reformulada, con la intención del camino que funcionó.

Después mide en sombra (leave-one-out sobre los turnos juzgados): ¿el perfil
habría acertado más que las reglas? Solo entonces lo activa; si empeora, lo
degrada. Nada de esto toca el routing por sí solo: manda `intent_profile.status`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.services import audit, metrics, text_terms
from app.services.intent import Intent, classify
from app.services.intent_profile import (
    MAX_NEIGHBORS,
    apply_gates,
    embedding_capability,
    intent_embedding_provider,
    neighbors,
    status_for,
    vote,
)

log = logging.getLogger(__name__)

INTENTS = frozenset(i.value for i in Intent)
MAX_EXEMPLARS_PER_USER = 200
FEEDBACK_WEIGHT = 1.0
COUNTERFACTUAL_WEIGHT = 0.4
MIN_JUDGED_FOR_ACTIVATION = 10
ACTIVATION_GAIN = 0.10


@dataclass(frozen=True)
class ExemplarSpec:
    user_id: str
    question: str
    label: str
    source: str
    weight: float
    created_at: datetime | None

    @property
    def question_hash(self) -> str:
        return question_hash(self.question)


def question_hash(question: str) -> str:
    """Hash estable de la pregunta normalizada (sin signos ni espacios de más).

    Así «¿Dónde está el PAGO?» y «donde esta el pago» son el mismo ejemplo: el
    job es idempotente y «olvidar este ejemplo» borra también sus variantes.
    """
    folded = " ".join(text_terms.norm(question).split())
    cleaned = re.sub(r"[^0-9a-z ]+", " ", folded)
    return hashlib.sha256(" ".join(cleaned.split()).encode("utf-8")).hexdigest()


def label_from_feedback(row: dict[str, Any]) -> str | None:
    """Etiqueta verdadera que aporta un veredicto, si la aporta.

    Un «útil» sobre una decisión del perfil o del LLM no es verdad independiente:
    sería entrenar al perfil con su propia predicción. Solo se acepta cuando la
    decisión la tomó una regla (o el flujo determinista).
    """
    verdict = str(row.get("verdict") or "")
    if verdict == "accepted":
        source = str(row.get("router_source") or "")
        if source in ("profile", "llm"):
            return None
        router = str(row.get("router_intent") or "")
        return router if router in INTENTS else None
    if verdict == "wrong_intent":
        corrected = str(row.get("corrected_intent") or "")
        return corrected if corrected in INTENTS else None
    return None


def derive_exemplars(feedback_rows: list[dict[str, Any]],
                     pairs: list[dict[str, Any]]) -> tuple[list[ExemplarSpec], dict[str, list[str]]]:
    """Convierte conducta en ejemplos etiquetados. Puro: sin DB, testeable.

    Devuelve `(ejemplos, hashes_a_olvidar_por_usuario)`. Manda el veredicto MÁS
    RECIENTE de cada pregunta: si después de aceptarla el usuario dice «no era
    esto», el ejemplo se olvida; si vuelve a aceptarla, se recupera. Una
    reformulación exitosa solo cuenta si no hay veredicto para esa pregunta.
    """
    best: dict[tuple[str, str], ExemplarSpec] = {}
    forgotten: dict[str, set[str]] = {}

    ordered = sorted(feedback_rows, key=lambda row: (
        row.get("created_at").timestamp() if row.get("created_at") else 0.0))

    for row in ordered:
        user_id = str(row.get("user_id") or "")
        question = str(row.get("question") or "").strip()
        if not user_id or not question:
            continue
        key = (user_id, question_hash(question))
        label = label_from_feedback(row)
        if label:
            best[key] = ExemplarSpec(user_id, question, label, "feedback",
                                     FEEDBACK_WEIGHT, row.get("created_at"))
            forgotten.get(user_id, set()).discard(key[1])
        elif str(row.get("verdict") or "") == "rejected":
            best.pop(key, None)
            forgotten.setdefault(user_id, set()).add(key[1])

    for row in pairs:
        user_id = str(row.get("user_id") or "")
        question = str(row.get("question") or "").strip()
        intent = str(row.get("intent") or "")
        if not user_id or not question or intent not in INTENTS:
            continue
        digest = question_hash(question)
        if (user_id, digest) in best or digest in forgotten.get(user_id, set()):
            continue
        best[(user_id, digest)] = ExemplarSpec(user_id, question, intent, "counterfactual",
                                               COUNTERFACTUAL_WEIGHT, row.get("created_at"))

    return list(best.values()), {uid: sorted(values) for uid, values in forgotten.items()}


# --------------------------------------------------------------------------- DB

def _provider():
    provider = intent_embedding_provider(get_settings())
    name = str(getattr(provider, "name", "") or "")
    model = str(getattr(provider, "model_name", None) or getattr(provider, "model", None) or name)
    return provider, name, model


def _feedback_rows(org_id: str, user_id: str | None) -> list[dict[str, Any]]:
    sql = """
        SELECT f.user_id::text AS user_id, f.verdict, f.corrected_intent,
               m.intent AS router_intent, m.intent_source AS router_source,
               q.content AS question,
               COALESCE(f.updated_at, f.created_at) AS created_at
          FROM intent_feedback f
          JOIN chat_messages m ON m.id = f.message_id
          JOIN LATERAL (
                 SELECT content FROM chat_messages u
                  WHERE u.session_id = m.session_id AND u.role = 'user'
                    AND u.created_at <= m.created_at
                  ORDER BY u.created_at DESC LIMIT 1
               ) q ON true
         WHERE f.organization_id = :o
    """
    params: dict[str, Any] = {"o": org_id}
    if user_id:
        sql += " AND f.user_id = :u"
        params["u"] = user_id
    sql += " ORDER BY COALESCE(f.updated_at, f.created_at) DESC LIMIT 5000"
    with tx(org_id) as conn:
        return rows(conn, sql, **params)


def _counterfactual_pairs(org_id: str, user_id: str | None, days: int) -> list[dict[str, Any]]:
    """Reformulación exitosa tras un turno fallido (misma sesión, < 10 min)."""
    sql = """
        WITH pairs AS (
          SELECT m.id AS assistant_id, m.session_id, m.intent, m.answer_kind,
                 COALESCE(jsonb_array_length(m.citations), 0) AS cites,
                 m.created_at, COALESCE(q.user_id, s.created_by)::text AS user_id,
                 q.content AS question, q.created_at AS asked_at
            FROM chat_messages m
            JOIN chat_sessions s ON s.id = m.session_id
            JOIN LATERAL (
                   SELECT content, created_at, user_id FROM chat_messages u
                    WHERE u.session_id = m.session_id AND u.role = 'user'
                      AND u.created_at <= m.created_at
                    ORDER BY u.created_at DESC LIMIT 1
                 ) q ON true
           WHERE m.role = 'assistant' AND m.organization_id = :o
             AND COALESCE(q.user_id, s.created_by) IS NOT NULL
             AND m.created_at > now() - make_interval(days => :days)
        ), seq AS (
          SELECT *, LAG(assistant_id) OVER w AS prev_id,
                 LAG(answer_kind) OVER w AS prev_kind,
                 LAG(cites) OVER w AS prev_cites,
                 LAG(asked_at) OVER w AS prev_asked
            FROM pairs WINDOW w AS (PARTITION BY session_id ORDER BY created_at, assistant_id)
        )
        SELECT question, intent, created_at, user_id
          FROM seq
         WHERE prev_id IS NOT NULL
           AND asked_at - prev_asked <= interval '10 minutes'
           AND (prev_kind IN ('insufficient', 'navigational') OR prev_cites = 0)
           AND answer_kind IN ('direct', 'partial') AND cites > 0
    """
    params: dict[str, Any] = {"o": org_id, "days": days}
    if user_id:
        sql += " AND user_id = :u"
        params["u"] = user_id
    sql += " ORDER BY created_at DESC LIMIT 5000"
    with tx(org_id) as conn:
        return rows(conn, sql, **params)


def _judged_turns(org_id: str, user_id: str) -> list[dict[str, Any]]:
    sql = """
        SELECT f.verdict, f.corrected_intent, m.intent AS router_intent,
               m.intent_source AS router_source, q.content AS question,
               COALESCE(f.updated_at, f.created_at) AS created_at
          FROM intent_feedback f
          JOIN chat_messages m ON m.id = f.message_id
          JOIN LATERAL (
                 SELECT content FROM chat_messages u
                  WHERE u.session_id = m.session_id AND u.role = 'user'
                    AND u.created_at <= m.created_at
                  ORDER BY u.created_at DESC LIMIT 1
               ) q ON true
         WHERE f.organization_id = :o AND f.user_id = :u
         ORDER BY COALESCE(f.updated_at, f.created_at) DESC LIMIT 500
    """
    with tx(org_id) as conn:
        return rows(conn, sql, o=org_id, u=user_id)


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in vector) + "]"


def _upsert_exemplars(org_id: str, user_id: str, specs: list[ExemplarSpec],
                      vectors: dict[str, list[float]], embedding_model: str) -> int:
    written = 0
    with tx(org_id) as conn:
        for spec in specs:
            vector = vectors.get(spec.question_hash)
            if not vector:
                continue
            conn.execute(text("""
                INSERT INTO intent_exemplar
                    (organization_id, user_id, label, question, question_hash, embedding,
                     embedding_model, source, weight, created_at)
                VALUES (:o, :u, :l, :q, :h, CAST(:e AS vector), :m, :s, :w, :c)
                ON CONFLICT (user_id, question_hash) DO UPDATE
                   SET label = EXCLUDED.label,
                       question = EXCLUDED.question,
                       embedding = EXCLUDED.embedding,
                       embedding_model = EXCLUDED.embedding_model,
                       source = EXCLUDED.source,
                       weight = EXCLUDED.weight,
                       created_at = EXCLUDED.created_at
            """), {"o": org_id, "u": user_id, "l": spec.label, "q": spec.question,
                   "h": spec.question_hash, "e": _vector_literal(vector), "m": embedding_model,
                   "s": spec.source, "w": spec.weight,
                   "c": spec.created_at or datetime.now(timezone.utc)})
            written += 1
        # Tope por usuario: los más viejos se van.
        conn.execute(text("""
            DELETE FROM intent_exemplar WHERE id IN (
                SELECT id FROM intent_exemplar WHERE user_id = :u
                 ORDER BY created_at DESC OFFSET :cap)
        """), {"u": user_id, "cap": MAX_EXEMPLARS_PER_USER})
    return written


def _forget(org_id: str, user_id: str, hashes: list[str]) -> int:
    if not hashes:
        return 0
    with tx(org_id) as conn:
        for value in hashes:
            conn.execute(text("DELETE FROM intent_exemplar WHERE user_id = :u AND question_hash = :h"),
                         {"u": user_id, "h": value})
    return len(hashes)


def _shadow_metrics(org_id: str, user_id: str, vectors: dict[str, list[float]],
                    model_label: str) -> dict[str, Any]:
    """¿Habría acertado más el perfil que las reglas? Leave-one-out, sin fugas."""
    judged = [row for row in _judged_turns(org_id, user_id) if label_from_feedback(row)]
    rules_ok = profile_ok = fp_rules = fp_profile = counted = 0
    with tx(org_id) as conn:
        for row in judged:
            truth = label_from_feedback(row)
            question = str(row.get("question") or "")
            vector = vectors.get(question_hash(question))
            if not truth or not vector:
                continue
            base = classify(question)
            found = neighbors(conn, user_id, vector, limit=MAX_NEIGHBORS,
                              exclude_hash=question_hash(question), model=model_label)
            pred = apply_gates(base, vote(found), question).intent.value
            counted += 1
            rules_ok += int(base.intent.value == truth)
            profile_ok += int(pred == truth)
            navegar = Intent.NAVIGATE.value
            fp_rules += int(base.intent.value == navegar and truth != navegar)
            fp_profile += int(pred == navegar and truth != navegar)
    if not counted:
        return {"judged": 0, "rules_accuracy": None, "profile_accuracy": None,
                "navegar_fp_rules": 0, "navegar_fp_profile": 0}
    return {
        "judged": counted,
        "rules_accuracy": round(rules_ok / counted, 4),
        "profile_accuracy": round(profile_ok / counted, 4),
        "navegar_fp_rules": fp_rules,
        "navegar_fp_profile": fp_profile,
    }


def _save_profile(org_id: str, user_id: str, *, status: str, reason: str,
                  exemplars: int, metrics_data: dict[str, Any]) -> dict[str, Any]:
    """Escribe el perfil y devuelve si hay degradación o subida de FP."""
    with tx(org_id) as conn:
        previous = one(conn, """SELECT status, navegar_fp_profile, navegar_fp_rules
                                  FROM intent_profile WHERE user_id = :u""", u=user_id)
        old_status = str((previous or {}).get("status") or "") or None
        prev_fp = int((previous or {}).get("navegar_fp_profile") or 0)
        conn.execute(text("""
            INSERT INTO intent_profile
                (organization_id, user_id, status, exemplars, judged, version,
                 rules_accuracy, profile_accuracy, navegar_fp_rules, navegar_fp_profile,
                 reason, metrics, built_at)
            VALUES (:o, :u, :st, :ex, :j, 1, :ra, :pa, :fr, :fp, :r, CAST(:m AS jsonb), now())
            ON CONFLICT (user_id) DO UPDATE
               SET status = EXCLUDED.status,
                   exemplars = EXCLUDED.exemplars,
                   judged = EXCLUDED.judged,
                   version = intent_profile.version + 1,
                   rules_accuracy = EXCLUDED.rules_accuracy,
                   profile_accuracy = EXCLUDED.profile_accuracy,
                   navegar_fp_rules = EXCLUDED.navegar_fp_rules,
                   navegar_fp_profile = EXCLUDED.navegar_fp_profile,
                   reason = EXCLUDED.reason,
                   metrics = EXCLUDED.metrics,
                   built_at = now()
        """), {"o": org_id, "u": user_id, "st": status, "ex": exemplars,
               "j": int(metrics_data.get("judged") or 0),
               "ra": metrics_data.get("rules_accuracy"), "pa": metrics_data.get("profile_accuracy"),
               "fr": int(metrics_data.get("navegar_fp_rules") or 0),
               "fp": int(metrics_data.get("navegar_fp_profile") or 0),
               "r": reason, "m": json.dumps(metrics_data, ensure_ascii=False, default=str)})
        fp_now = int(metrics_data.get("navegar_fp_profile") or 0)
        fp_rules = int(metrics_data.get("navegar_fp_rules") or 0)
        degraded = old_status == "active" and status != "active"
        # Sube el FP de navegar respecto de la última medición, o el perfil deja
        # de decidir con más FP que las reglas. No se repite cada noche con el
        # mismo valor: para el estado persistente está `health()`/el healthcheck.
        fp_alert = (fp_now > prev_fp and fp_now > 0) or (degraded and fp_now > fp_rules)
        if degraded or fp_alert:
            audit.record(conn, org_id=org_id, actor_id=None,
                         action="intent_profile.degraded", entity_type="intent_profile",
                         entity_id=user_id, before={"status": old_status, "navegar_fp": prev_fp},
                         after={"status": status, "reason": reason,
                                "fp_alert": fp_alert, **metrics_data})
    metrics.track_intent_profile(org_id, status=status, reason=reason, exemplars=exemplars,
                                 fp_profile=fp_now, fp_rules=fp_rules)
    return {"degraded": degraded, "fp_alert": fp_alert, "from": old_status, "to": status}


def notify_degradation(org_id: str, user_id: str, *, old: str | None, new: str,
                       reason: str, metrics_data: dict[str, Any]) -> None:
    """Aviso activo cuando un perfil que decidía deja de hacerlo.

    Sin webhook configurado queda el log de error + la métrica + el `audit_logs`;
    con `INTENT_ALERT_WEBHOOK` se POSTea el evento (3 s, nunca rompe el job).
    """
    payload = {"event": "intent_profile_degraded", "org_id": org_id, "user_id": user_id,
               "from": old, "to": new, "reason": reason,
               "metrics": {k: metrics_data.get(k) for k in
                           ("judged", "rules_accuracy", "profile_accuracy",
                            "navegar_fp_rules", "navegar_fp_profile")}}
    log.error("perfil de intención degradado org=%s user=%s %s→%s (%s) %s",
              org_id, user_id, old, new, reason, payload["metrics"])
    metrics.track_intent_profile_degraded(org_id, reason)
    url = (get_settings().INTENT_ALERT_WEBHOOK or "").strip()
    if not url.startswith(("https://", "http://")):
        return
    import urllib.request

    try:
        request = urllib.request.Request(  # noqa: S310 — esquema validado arriba
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(request, timeout=3).close()  # noqa: S310 — esquema validado arriba
    except Exception as exc:  # noqa: BLE001 — la alerta no puede tumbar el job
        log.warning("no se pudo enviar la alerta de perfil: %s", exc)


def _is_fresh(spec: ExemplarSpec, cutoff: datetime) -> bool:
    created = spec.created_at
    if created is None:
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created >= cutoff


def purge_expired(org_id: str, days: int | None = None) -> dict[str, int]:
    """Retención: los ejemplos caducan y los perfiles vacíos se van con ellos."""
    retention = int(days if days is not None else get_settings().INTENT_PROFILE_RETENTION_DAYS)
    if retention <= 0:
        return {"exemplars": 0, "profiles": 0}
    with tx(org_id) as conn:
        expired = conn.execute(text("""
            DELETE FROM intent_exemplar
             WHERE organization_id = :o
               AND created_at < now() - make_interval(days => :d)
        """), {"o": org_id, "d": retention}).rowcount or 0
        profiles = conn.execute(text("""
            DELETE FROM intent_profile p
             WHERE p.organization_id = :o
               AND p.built_at IS NOT NULL
               AND p.built_at < now() - make_interval(days => :d)
               AND NOT EXISTS (SELECT 1 FROM intent_exemplar e WHERE e.user_id = p.user_id)
        """), {"o": org_id, "d": retention}).rowcount or 0
    return {"exemplars": int(expired), "profiles": int(profiles)}


def rebuild(org_id: str, *, user_id: str | None = None, days: int = 90,
            dry_run: bool = False) -> list[dict[str, Any]]:
    """Reconstruye el perfil de cada usuario de la organización. Idempotente."""
    provider, provider_name, model_label = _provider()
    probe = embedding_capability()
    semantic = bool(probe.get("semantic"))
    if not dry_run:
        purge_expired(org_id)
    feedback = _feedback_rows(org_id, user_id)
    pairs = _counterfactual_pairs(org_id, user_id, days)
    specs, forget = derive_exemplars(feedback, pairs)

    by_user: dict[str, list[ExemplarSpec]] = {}
    for spec in specs:
        by_user.setdefault(spec.user_id, []).append(spec)
    # Un usuario con solo «no era esto» también entra: hay que olvidar su ejemplo.
    for row in feedback:
        uid = str(row.get("user_id") or "")
        if uid:
            by_user.setdefault(uid, [])

    report: list[dict[str, Any]] = []
    retention = int(get_settings().INTENT_PROFILE_RETENTION_DAYS)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention) if retention > 0 else None
    for uid, items in by_user.items():
        # Lo que ya caducó no se reinserta: si no, la purga de mañana lo borra y
        # el job de pasado lo vuelve a crear, todas las noches.
        if cutoff is not None:
            items = [spec for spec in items if _is_fresh(spec, cutoff)]
        questions = sorted({s.question for s in items})
        try:
            vectors = dict(zip((question_hash(q) for q in questions),
                               provider.embed(questions), strict=False)) if questions else {}
        except Exception as exc:  # noqa: BLE001 — sin embeddings no hay perfil
            log.warning("intent profile: embeddings fallaron para %s: %s", uid, exc)
            report.append({"user_id": uid, "status": "off", "reason": "embedding_failed"})
            continue
        exemplars = len({s.question_hash for s in items if s.question_hash in vectors})
        if dry_run:
            report.append({"user_id": uid, "exemplars": exemplars, "status": "dry_run",
                           "semantic": semantic, "probe": probe})
            continue
        if items:
            _upsert_exemplars(org_id, uid, items, vectors, model_label)
        _forget(org_id, uid, forget.get(uid, []))
        metrics_data = _shadow_metrics(org_id, uid, vectors, model_label)
        status, reason = status_for(
            judged=int(metrics_data.get("judged") or 0), exemplars=exemplars,
            rules_accuracy=metrics_data.get("rules_accuracy"),
            profile_accuracy=metrics_data.get("profile_accuracy"),
            fp_rules=int(metrics_data.get("navegar_fp_rules") or 0),
            fp_profile=int(metrics_data.get("navegar_fp_profile") or 0),
            semantic_embeddings=semantic,
            min_judged=MIN_JUDGED_FOR_ACTIVATION, gain=ACTIVATION_GAIN)
        change = _save_profile(org_id, uid, status=status, reason=reason, exemplars=exemplars,
                               metrics_data=metrics_data)
        if change["degraded"] or change["fp_alert"]:
            alert_reason = "navegar_fp_above_rules" if change["fp_alert"] and not change["degraded"] else reason
            notify_degradation(org_id, uid, old=change["from"], new=change["to"],
                               reason=alert_reason, metrics_data=metrics_data)
        report.append({"user_id": uid, "exemplars": exemplars, "status": status,
                       "reason": reason, "semantic": semantic, **metrics_data})
    return report


def health(org_id: str | None = None) -> list[dict[str, Any]]:
    """Perfiles que empeoraron o que añaden falsos positivos de `navegar`.

    Es la consulta que usa `scripts/intent_health.py` para salir con error en CI
    o en un cron externo: el job degrada el perfil, esto lo hace visible.
    """
    sql = """
        SELECT organization_id::text AS org_id, user_id::text AS user_id, status, reason,
               judged, rules_accuracy, profile_accuracy,
               navegar_fp_rules, navegar_fp_profile, exemplars, built_at
          FROM intent_profile
         WHERE reason = 'profile_worse_than_rules'
            OR navegar_fp_profile > navegar_fp_rules
         ORDER BY built_at DESC NULLS LAST
    """
    out: list[dict[str, Any]] = []
    for oid in ([org_id] if org_id else organizations()):
        with tx(oid) as conn:
            out.extend(rows(conn, sql))
    return out


def organizations() -> list[str]:
    with tx(None) as conn:
        return [str(r["id"]) for r in rows(conn, "SELECT id FROM ops_list_organizations()")]


def rebuild_all(*, days: int = 90) -> dict[str, Any]:
    total: list[dict[str, Any]] = []
    for org_id in organizations():
        try:
            total.extend({"org_id": org_id, **row} for row in rebuild(org_id, days=days))
        except Exception:  # noqa: BLE001 — una org no debe tumbar el job
            log.exception("intent profile: falló la organización %s", org_id)
    return {"users": len(total), "report": total}
