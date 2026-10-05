"""Interfaces de proveedores (SSD §137-138). Implementaciones concretas se registran por
variable de entorno; el dominio nunca depende de un proveedor específico."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int


class LLMProvider(Protocol):
    name: str
    model: str
    def complete(self, system: str, user: str) -> LLMResult: ...


class OCRProvider(Protocol):
    def process(self, document_bytes: bytes, mime_type: str, progress_cb=None) -> list[dict]: ...  # páginas con layout y confianza


class ASRProvider(Protocol):
    def transcribe(self, media_bytes: bytes, mime_type: str) -> list[dict]: ...  # segmentos con timestamps


class EmbeddingProvider(Protocol):
    dimensions: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...
