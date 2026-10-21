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


class LocalNgramEmbedding:
    """Embedding local determinista de 1024 dimensiones (sin red, sin GPU).

    No pretende sustituir a un modelo semántico: es un **fallback de transición**
    para que pgvector nunca quede con vectores aleatorios ni vacío cuando el
    proveedor externo no tiene crédito. Codifica palabras, bigramas y n-gramas de
    caracteres con hashing con signo; textos morfológicamente parecidos quedan
    cerca. El modelo se etiqueta como `local-ngram-1024`, de modo que el filtro
    por `embedding_model` impide mezclarlo con Gemini.
    """

    name = "local-hash"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.dimensions = self.settings.EMBEDDING_DIMENSIONS
        self.max_chars = self.settings.EMBEDDING_MAX_CHARS

    @staticmethod
    def _grams(text: str) -> list[tuple[str, float]]:
        from app.services import text_terms

        normalized = text_terms.norm(text)[:8000]
        words = [w for w in normalized.split() if w]
        grams: list[tuple[str, float]] = []
        for word in words:
            grams.append((f"w:{word}", 1.0))
            for n in (3, 4, 5):
                if len(word) >= n:
                    for i in range(len(word) - n + 1):
                        grams.append((f"c:{word[i:i + n]}", 0.35))
        for a, b in zip(words, words[1:], strict=False):
            grams.append((f"b:{a}_{b}", 0.6))
        return grams

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dimensions
            for gram, weight in self._grams(_truncate(text, self.max_chars)):
                digest = hashlib.sha256(gram.encode("utf-8")).digest()
                idx = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1.0 if digest[4] & 1 else -1.0
                vec[idx] += sign * weight
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
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
            kwargs: dict[str, Any] = {"api_key": self.settings.EMBEDDING_API_KEY,
                                      "timeout": getattr(self.settings, "EMBEDDING_TIMEOUT_S", 10.0)}
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
    if s.EMBEDDING_PROVIDER == "local-hash":
        return LocalNgramEmbedding(s)
    if s.EMBEDDING_PROVIDER == "openai":
        return OpenAIEmbedding(s)
    if s.EMBEDDING_PROVIDER == "sentence-transformers":
        return SentenceTransformersEmbedding(s)
    raise ValueError(f"unknown EMBEDDING_PROVIDER: {s.EMBEDDING_PROVIDER}")


def get_embedding_provider_for_model(model: str | None, settings: Settings | None = None) -> EmbeddingProvider:
    """Proveedor coherente con la etiqueta del índice.

    Si el índice fue construido con el fallback local, la consulta debe usar el
    mismo espacio vectorial aunque el entorno siga apuntando a Gemini. Así el
    cambio de proveedor no deja la rama vectorial vacía ni mezcla modelos.
    """
    s = settings or get_settings()
    if model and model.startswith("local-ngram"):
        return LocalNgramEmbedding(s)
    return get_embedding_provider(s)
