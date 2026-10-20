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


@dataclass(frozen=True)
class Capabilities:
    """Capacidades reales de un proveedor. El pipeline NUNCA las exige: las usa
    para degradar (p. ej. sin JSON mode se valida y repara el texto)."""
    streaming: bool = False
    json_mode: bool = False
    tool_calling: bool = False
    max_context: int | None = None


class LLMProvider(Protocol):
    name: str
    model: str
    def complete(self, system: str, user: str) -> LLMResult: ...
    def capabilities(self) -> Capabilities: ...


class OCRProvider(Protocol):
    def process(self, document_bytes: bytes, mime_type: str, progress_cb=None) -> list[dict]: ...  # páginas con layout y confianza


class ASRProvider(Protocol):
    def transcribe(self, media_bytes: bytes, mime_type: str, progress_cb=None,
                   initial_prompt: str | None = None) -> list[dict]: ...  # segmentos con timestamps


class EmbeddingProvider(Protocol):
    dimensions: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...
