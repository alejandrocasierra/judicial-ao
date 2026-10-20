"""Record/replay de llamadas LLM (cassettes JSONL).

Envuelve cualquier proveedor (FakeLLM, OpenAiLLM, AnthropicLLM) sin cambiar su
interfaz. La clave de reproducción es determinista:

    sha256(sha256(system) + "\\n" + sha256(user) + "\\n" + provider + "\\n" + model)

La cassette guarda la respuesta completa y los tokens. Nunca guarda la API key
ni la URL del proveedor. Escritura atómica línea a línea con lock de proceso.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from app.providers.base import LLMResult

DEFAULT_CASSETTE_DIR = Path(__file__).resolve().parents[4] / "evals" / "cassettes"


class RecordingMiss(LookupError):
    """No hay cassette para esta llamada en modo replay estricto."""


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def record_key(system: str, user: str, provider: str, model: str) -> str:
    """Clave estable para (prompt de sistema, prompt de usuario, proveedor, modelo)."""
    material = "\n".join((_sha256(system), _sha256(user), provider or "", model or ""))
    return _sha256(material)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value or "unknown").strip("-") or "unknown"


def _resolve_path(path: str | Path | None, provider: str, model: str) -> Path:
    if path is None:
        return DEFAULT_CASSETTE_DIR / f"{_safe_name(provider)}-{_safe_name(model)}.jsonl"
    p = Path(path)
    if p.suffix.lower() == ".jsonl":
        return p
    return p / f"{_safe_name(provider)}-{_safe_name(model)}.jsonl"


class RecordingLLM:
    """Proveedor que graba o reproduce respuestas de otro proveedor."""

    def __init__(self, inner: Any, *, mode: str = "record", path: str | Path | None = None,
                 on_miss: str = "fallback"):
        if mode not in {"record", "replay", "off"}:
            raise ValueError(f"modo inválido: {mode}")
        if on_miss not in {"fallback", "error"}:
            raise ValueError(f"on_miss inválido: {on_miss}")
        self._inner = inner
        self.mode = mode
        self.on_miss = on_miss
        self.name = getattr(inner, "name", "unknown")
        self.model = getattr(inner, "model", "")
        self.path = _resolve_path(path, self.name, self.model)
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self.misses = 0
        self.hits = 0
        self.recorded = 0
        if mode == "replay":
            self._records = self._load()

    @property
    def inner(self) -> Any:
        return self._inner

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        records: dict[str, dict] = {}
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = rec.get("key")
                if isinstance(key, str) and isinstance(rec.get("response"), str):
                    records[key] = rec
        return records

    def _lookup(self, key: str) -> dict | None:
        with self._lock:
            return self._records.get(key)

    def _append(self, rec: dict) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._records[rec["key"]] = rec
            self.recorded += 1

    def complete(self, system: str, user: str) -> LLMResult:
        key = record_key(system, user, self.name, self.model)
        if self.mode == "replay":
            rec = self._lookup(key)
            if rec is not None:
                with self._lock:
                    self.hits += 1
                return LLMResult(rec["response"], rec.get("provider", self.name),
                                 rec.get("model", self.model),
                                 int(rec.get("tokens_in", 0)), int(rec.get("tokens_out", 0)))
            with self._lock:
                self.misses += 1
            if self.on_miss == "error":
                raise RecordingMiss(f"no hay cassette para la clave {key} en {self.path}")
            return self._inner.complete(system, user)

        result = self._inner.complete(system, user)
        if self.mode == "record":
            self._append({
                "key": key,
                "system_sha256": _sha256(system),
                "user_sha256": _sha256(user),
                "provider": result.provider,
                "model": result.model,
                "response": result.text,
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
            })
        return result

    def stats(self) -> dict:
        return {"mode": self.mode, "path": str(self.path), "hits": self.hits,
                "misses": self.misses, "recorded": self.recorded,
                "cassettes": len(self._records)}


def wrap(llm: Any, *, mode: str = "record", path: str | Path | None = None,
         on_miss: str = "fallback") -> Any:
    """Devuelve el proveedor envuelto. `off` devuelve el original sin tocar."""
    if mode == "off":
        return llm
    return RecordingLLM(llm, mode=mode, path=path, on_miss=on_miss)
