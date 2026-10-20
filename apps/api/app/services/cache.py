"""Caché de dos niveles para el pipeline del chat (contrato §1.9).

Redis cuando `RATE_LIMIT_BACKEND=redis` y `redis` es importable; en cualquier
otro caso (o si Redis falla) un LRU en proceso con TTL. La API nunca propaga
errores: la caché es una optimización, no una dependencia.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

log = logging.getLogger(__name__)

_MAX_ENTRIES = 512
_PREFIX = "judicial:cache:"
_LOCK = threading.RLock()
_MEMORY: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
_REDIS_CLIENT: Any = None
_REDIS_FAILED = False


def _redis_client():
    global _REDIS_CLIENT, _REDIS_FAILED
    if _REDIS_FAILED or _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    try:
        from app.core.config import get_settings
        s = get_settings()
        if s.RATE_LIMIT_BACKEND != "redis" or not s.REDIS_URL:
            return None
        import redis  # import diferido: el backend en memoria no lo necesita
        client = redis.Redis.from_url(s.REDIS_URL, decode_responses=True,
                                      socket_timeout=0.25, socket_connect_timeout=0.25)
        client.ping()
        _REDIS_CLIENT = client
    except Exception as exc:  # noqa: BLE001 — sin Redis se sigue en memoria
        log.debug("redis cache unavailable: %s", exc)
        _REDIS_FAILED = True
        _REDIS_CLIENT = None
    return _REDIS_CLIENT


def _redis_key(namespace: str, key: str) -> str:
    return f"{_PREFIX}{namespace}:{key}"


def _memory_get(full_key: str) -> Any:
    with _LOCK:
        entry = _MEMORY.get(full_key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.time():
            _MEMORY.pop(full_key, None)
            return None
        _MEMORY.move_to_end(full_key)
        return value


def _memory_set(full_key: str, value: Any, ttl_seconds: int) -> None:
    with _LOCK:
        _MEMORY[full_key] = (time.time() + max(1, int(ttl_seconds)), value)
        _MEMORY.move_to_end(full_key)
        while len(_MEMORY) > _MAX_ENTRIES:
            _MEMORY.popitem(last=False)


def get_json(namespace: str, key: str) -> Any | None:
    if not namespace or key is None:
        return None
    full_key = _redis_key(namespace, str(key))
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(full_key)
            if raw is not None:
                return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            log.debug("redis get failed: %s", exc)
    return _memory_get(full_key)


def set_json(namespace: str, key: str, value: Any, ttl_seconds: int) -> None:
    if not namespace or key is None:
        return
    ttl = max(1, int(ttl_seconds or 1))
    full_key = _redis_key(namespace, str(key))
    # Se escribe también en memoria: si Redis cae a mitad de un turno, el
    # llamador igualmente obtiene el dato de su propio proceso.
    try:
        _memory_set(full_key, value, ttl)
    except Exception:  # noqa: BLE001
        log.debug("memory cache set failed", exc_info=True)
    client = _redis_client()
    if client is not None:
        try:
            client.setex(full_key, ttl, json.dumps(value, ensure_ascii=False, default=str))
        except Exception as exc:  # noqa: BLE001
            log.debug("redis set failed: %s", exc)


def delete_prefix(namespace: str) -> None:
    if not namespace:
        return
    prefix = _redis_key(namespace, "")
    with _LOCK:
        for key in [k for k in _MEMORY if k.startswith(prefix)]:
            _MEMORY.pop(key, None)
    client = _redis_client()
    if client is None:
        return
    try:
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=f"{prefix}*", count=200)
            if keys:
                client.delete(*keys)
            if cursor == 0:
                break
    except Exception as exc:  # noqa: BLE001
        log.debug("redis delete_prefix failed: %s", exc)


def clear_memory() -> None:
    """Solo para pruebas: vacía el LRU del proceso."""
    with _LOCK:
        _MEMORY.clear()
