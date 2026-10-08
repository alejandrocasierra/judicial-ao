# -*- coding: utf-8 -*-
"""Reencola los jobs huérfanos (RUNNING/RETRYING) al ARRANCAR el worker, antes de celery.

Un job puede quedar huérfano si el worker muere/reinicia mientras lo ejecutaba (p. ej.
OOM). Con `JOB_STALE_MINUTES=120` el sweeper de beat tarda 2 h; esto lo recupera al
instante con `STARTUP_REAP_MINUTES` (1 min por defecto). Nunca debe impedir que el worker
arranque: cualquier error se registra y se continúa."""
from __future__ import annotations

import sys
from pathlib import Path

# El script vive en <repo>/scripts; el código de la app en <repo>/apps/api.
_api_dir = Path(__file__).resolve().parent.parent / "apps" / "api"
if _api_dir.exists() and str(_api_dir) not in sys.path:
    sys.path.insert(0, str(_api_dir))

try:
    from app.core.config import get_settings
    from app.workers.executor import reap_orphans

    n = reap_orphans(int(get_settings().STARTUP_REAP_MINUTES))
    print(f"[startup reap] jobs huérfanos reencolados: {n}", flush=True)
except Exception as exc:  # noqa: BLE001
    print(f"[startup reap] falló (se continúa con el arranque): {exc}", file=sys.stderr, flush=True)

sys.exit(0)
