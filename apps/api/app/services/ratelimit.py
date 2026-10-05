from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.core.config import get_settings
from app.core.errors import AppError

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)

# Límites por usuario y por organización
_PER_USER = {"login": "RATE_LIMIT_LOGIN_PER_MINUTE", "query": "RATE_LIMIT_QUERIES_PER_MINUTE",
            "upload": "RATE_LIMIT_UPLOADS_PER_MINUTE"}
_PER_ORG = {"query": "RATE_LIMIT_ORG_QUERIES_PER_MINUTE", "upload": "RATE_LIMIT_ORG_UPLOADS_PER_MINUTE"}


def _limit(bucket: str) -> int:
    s = get_settings()
    return getattr(s, _PER_USER[bucket])


def _org_limit(bucket: str) -> int:
    s = get_settings()
    return getattr(s, _PER_ORG[bucket])


def _hit(bucket: str, key: str, limit: int) -> bool:
    """Registra un hit y retorna True si superó el límite."""
    s = get_settings()
    if s.RATE_LIMIT_BACKEND == "redis":
        import redis
        r = redis.Redis.from_url(s.REDIS_URL)
        k = f"rl:{bucket}:{key}:{int(time.time() // 60)}"
        n = r.incr(k)
        r.expire(k, 61)
        return n > limit
    now = time.monotonic()
    with _lock:
        q = _hits[f"{bucket}:{key}"]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False


def check(bucket: str, key: str) -> None:
    """Límite por usuario/IP."""
    if _hit(bucket, key, _limit(bucket)):
        from app.services import metrics
        metrics.track_rate_limit("user", bucket)
        raise AppError("RATE_LIMITED", 429)


def check_org(bucket: str, org_id: str) -> None:
    """Límite por organización (SSD §113): protege el costo compartido."""
    if bucket not in _PER_ORG:
        return
    if _hit(f"org:{bucket}", org_id, _org_limit(bucket)):
        from app.services import metrics
        metrics.track_rate_limit(org_id, f"org:{bucket}")
        raise AppError("RATE_LIMITED", 429)


def reset() -> None:
    with _lock:
        _hits.clear()
