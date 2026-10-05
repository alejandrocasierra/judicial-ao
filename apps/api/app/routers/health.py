from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.core.db import engine
from app.services import metrics

router = APIRouter(tags=["health"])


@router.get("/health")
def health():  # liveness: el proceso responde
    return {"status": "ok"}


@router.get("/ready")
def ready():  # readiness: dependencias disponibles
    try:
        with engine().connect() as c:
            c.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception:
        return JSONResponse({"status": "not_ready"}, status_code=503)


@router.get("/metrics")
def metrics_endpoint():
    """Métricas Prometheus (SSD §28)."""
    return Response(content=metrics.metrics_endpoint(), media_type="text/plain")
