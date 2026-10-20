"""Presupuesto de latencia de un turno (contrato §1.8).

Se propaga por el pipeline (routing → retrieval → síntesis → compositor) y
permite degradar en vez de colgar: al agotarse se omite el juez semántico, se
recorta el trabajo opcional y se devuelve la mejor respuesta disponible con
`degraded=True`.

No lanza excepciones: un deadline nunca debe tumbar una petición.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Iterator

log = logging.getLogger(__name__)

_DEFAULT_BUDGET_MS = 12000


def _default_budget() -> int:
    try:
        from app.core.config import get_settings
        return int(get_settings().REQUEST_DEADLINE_MS)
    except Exception:  # noqa: BLE001 — el deadline debe existir aun sin settings
        return _DEFAULT_BUDGET_MS


class Deadline:
    def __init__(self, budget_ms: int | None = None):
        self.budget_ms = int(budget_ms if budget_ms is not None else _default_budget())
        self._started = time.monotonic()
        self._lock = threading.RLock()
        self.degraded = False
        self.expired_stage: str | None = None

    @property
    def expired(self) -> bool:
        return self.remaining_ms() <= 0

    def remaining_ms(self) -> int:
        elapsed = int((time.monotonic() - self._started) * 1000)
        return max(0, self.budget_ms - elapsed)

    def can(self, stage: str, ms: int = 0) -> bool:
        """¿Hay presupuesto para empezar `stage` con al menos `ms` ms?"""
        remaining = self.remaining_ms()
        if remaining <= 0 or remaining < max(0, int(ms)):
            with self._lock:
                self.degraded = True
                if self.expired_stage is None:
                    self.expired_stage = stage
            return False
        return True

    @contextmanager
    def stage(self, name: str) -> Iterator["Deadline"]:
        """Mide una etapa y marca degradación si termina fuera de presupuesto."""
        tracker = None
        try:
            from app.services import metrics
            tracker = metrics.track_stage
        except Exception:  # noqa: BLE001 — métricas opcionales
            tracker = None

        if tracker is None:
            started = time.monotonic()
            try:
                yield self
            finally:
                elapsed = int((time.monotonic() - started) * 1000)
                if self.remaining_ms() <= 0:
                    with self._lock:
                        self.degraded = True
                        if self.expired_stage is None:
                            self.expired_stage = name
                log.debug("stage %s took %sms", name, elapsed)
            return

        try:
            with tracker(f"chat_{name}"):
                yield self
        finally:
            if self.remaining_ms() <= 0:
                with self._lock:
                    self.degraded = True
                    if self.expired_stage is None:
                        self.expired_stage = name
