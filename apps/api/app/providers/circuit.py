"""Circuit breaker por proveedor/modelo (plan §3.3, punto 9).

Un proveedor que ya falló por timeout/5xx no debe consumir el presupuesto de
latencia de los siguientes turnos: tras N fallos se abre el circuito durante un
cooldown y se falla rápido. El llamador decide la degradación (el chat compone
con la evidencia disponible). No confunde errores de contrato (JSON inválido)
con caídas de red: solo cuentan timeouts, conexión y 5xx.
"""
from __future__ import annotations

import threading
import time

FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 30.0

_lock = threading.Lock()
_state: dict[str, dict[str, float]] = {}


class CircuitOpenError(RuntimeError):
    """El proveedor está en cooldown; no se intenta la llamada."""


def _should_trip(exc: Exception) -> bool:
    try:
        import httpx
    except Exception:  # pragma: no cover
        return False
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError,
                        httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


def _key(provider: str, model: str, base_url: str | None = None) -> str:
    return f"{provider}:{model}:{base_url or ''}"


def reset(key: str | None = None) -> None:
    with _lock:
        if key is None:
            _state.clear()
        else:
            _state.pop(key, None)


def is_open(key: str) -> bool:
    with _lock:
        entry = _state.get(key)
        return bool(entry and entry.get("opened_until", 0.0) > time.monotonic())


def call(key: str, fn, *, threshold: int = FAILURE_THRESHOLD,
         cooldown: float = COOLDOWN_SECONDS):
    """Ejecuta `fn`; abre el circuito tras `threshold` fallos de red/5xx."""
    with _lock:
        entry = _state.setdefault(key, {"failures": 0.0, "opened_until": 0.0})
        if entry["opened_until"] > time.monotonic():
            raise CircuitOpenError(f"circuit open for {key}")
    try:
        result = fn()
    except Exception as exc:
        if _should_trip(exc):
            with _lock:
                entry = _state.setdefault(key, {"failures": 0.0, "opened_until": 0.0})
                entry["failures"] += 1
                if entry["failures"] >= threshold:
                    entry["opened_until"] = time.monotonic() + cooldown
                    entry["failures"] = 0.0
        raise
    with _lock:
        entry = _state.setdefault(key, {"failures": 0.0, "opened_until": 0.0})
        entry["failures"] = 0.0
        entry["opened_until"] = 0.0
    return result


__all__ = ["CircuitOpenError", "call", "is_open", "reset"]
