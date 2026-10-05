"""Proveedores de embeddings (SSD §39): fake, OpenAI y sentence-transformers local.

El dominio consume la interfaz `EmbeddingProvider` de `base.py`; la factory
`get_embedding_provider()` resuelve la implementación desde variables de entorno.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.core.config import get_settings, Settings
from app.providers.base import EmbeddingProvider

log = logging.getLogger(__name__)


def _truncate(text: str, max_chars: int) -> str:
    return text[:max_chars] if len(text) > max_chars else text


class FakeEmbedding:
    """Embedding determinista para tests/desarrollo. No requiere red ni GPU.

    Genera vectores unitarios de la dimensión configurada a partir del hash
    SHA-256 del texto. Respeta el contrato `EmbeddingProvider`.
    """

    name = "fake"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.dimensions = self.settings.EMBEDDING_DIMENSIONS
        self.max_chars = self.settings.EMBEDDING_MAX_CHARS

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            t = _truncate(t, self.max_chars)
            h = hashlib.sha256(t.encode("utf-8")).digest()
            # Repetimos bytes hasta alcanzar la dimensión deseada y normalizamos.
            vals = [((h[i % len(h)] / 255.0) - 0.5) * 2 for i in range(self.dimensions)]
            norm = sum(v * v for v in vals) ** 0.5 or 1.0
            out.append([v / norm for v in vals])
        return out


class OpenAIEmbedding:
    """Cliente OpenAI (o compatible) para embeddings."""

    name = "openai"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.dimensions = self.settings.EMBEDDING_DIMENSIONS
        self.max_chars = self.settings.EMBEDDING_MAX_CHARS
        self.model = self.settings.EMBEDDING_MODEL
        self._client: Any | None = None

    def _client_instance(self) -> Any:
        if self._client is None:
            import openai
            kwargs: dict[str, Any] = {"api_key": self.settings.EMBEDDING_API_KEY}
            base = self.settings.EMBEDDING_API_BASE_URL
            if base:
                kwargs["base_url"] = base
            self._client = openai.OpenAI(**kwargs)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        texts = [_truncate(t, self.max_chars) for t in texts]
        client = self._client_instance()
        resp = client.embeddings.create(input=texts, model=self.model, dimensions=self.dimensions)
        return [d.embedding for d in resp.data]


class SentenceTransformersEmbedding:
    """Embeddings locales vía sentence-transformers. Carga el modelo una sola vez."""

    name = "sentence-transformers"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.dimensions = self.settings.EMBEDDING_DIMENSIONS
        self.max_chars = self.settings.EMBEDDING_MAX_CHARS
        self.model_name = self.settings.EMBEDDING_MODEL
        self._model: Any | None = None

    def _model_instance(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            log.info("loading sentence-transformers model=%s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        texts = [_truncate(t, self.max_chars) for t in texts]
        model = self._model_instance()
        vectors = model.encode(texts, batch_size=self.settings.EMBEDDING_BATCH_SIZE, show_progress_bar=False)
        # Asegurar lista de listas de float y normalizar.
        out: list[list[float]] = []
        for v in vectors:
            vals = [float(x) for x in v]
            norm = sum(x * x for x in vals) ** 0.5 or 1.0
            out.append([x / norm for x in vals])
        return out


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    s = settings or get_settings()
    if s.EMBEDDING_PROVIDER == "fake":
        return FakeEmbedding(s)
    if s.EMBEDDING_PROVIDER == "openai":
        return OpenAIEmbedding(s)
    if s.EMBEDDING_PROVIDER == "sentence-transformers":
        return SentenceTransformersEmbedding(s)
    raise ValueError(f"unknown EMBEDDING_PROVIDER: {s.EMBEDDING_PROVIDER}")
