from __future__ import annotations

import logging
import threading
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError, app_error_handler, http_error_handler, unhandled_error_handler, validation_error_handler
from app.core.i18n import negotiate
from app.routers import admin, audit_router, auth, cases, chats, citations, documents, folders, graph, health, meta, models, query, review


def _warm_intent_embeddings() -> None:
    """Carga el modelo semántico del perfil en segundo plano, no en el primer turno.

    Solo para `sentence-transformers` (cargar 2 GB en el primer turno activo
    rompería el deadline). Si falla, el perfil se salta el turno: la regla decide.
    """
    settings = get_settings()
    if settings.INTENT_EMBEDDING_BACKEND != "sentence-transformers":
        return

    def run() -> None:
        try:
            from app.services.intent_profile import intent_embedding_provider

            intent_embedding_provider(settings).embed(["warmup"])
            logging.getLogger(__name__).info("intent embedding model warmed up")
        except Exception as exc:  # noqa: BLE001 — el perfil es opcional
            logging.getLogger(__name__).warning("intent embedding warmup failed: %s", exc)

    threading.Thread(target=run, daemon=True, name="intent-embedding-warmup").start()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _warm_intent_embeddings()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=s.LOG_LEVEL, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    app = FastAPI(title=s.APP_NAME, version=s.PIPELINE_VERSION, lifespan=_lifespan,
                  docs_url=None if s.APP_ENV == "production" else "/docs",
                  openapi_url=None if s.APP_ENV == "production" else "/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "Accept-Language", "X-Request-ID"])

    @app.middleware("http")
    async def context(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        request.state.request_id = rid if (rid and len(rid) <= 64 and rid.replace("-", "").isalnum()) else str(uuid.uuid4())
        request.state.locale = negotiate(request.headers.get("accept-language"))
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Content-Language"] = request.state.locale
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if not request.url.path.startswith(("/docs", "/openapi.json")):
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if s.APP_ENV in ("staging", "production"):
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(health.router)
    for r in (auth.router, meta.router, cases.router, documents.router, folders.router, query.router, models.router,
              models.agents_router, citations.router, review.router, audit_router.router, graph.router, admin.router,
              chats.router):
        app.include_router(r, prefix=s.API_PREFIX)
    return app


app = create_app()
