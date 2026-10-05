"""Métricas de calidad de OCR: error de carácter (CER) y de palabra (WER).

CER/WER son las métricas estándar del SSD para validar OCR (§133). Se calculan con
distancia de Levenshtein (sin dependencias externas) sobre la referencia y la hipótesis.
"""
from __future__ import annotations

import unicodedata


def normalize_text(text: str) -> str:
    """Normaliza para comparar: minúsculas, sin acentos, espacios colapsados."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def _levenshtein(a: list[str], b: list[str]) -> int:
    """Distancia de edición entre dos secuencias (caracteres o palabras)."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def edit_distance(reference: str, hypothesis: str) -> int:
    return _levenshtein(list(reference or ""), list(hypothesis or ""))


def cer(reference: str, hypothesis: str, *, normalize: bool = True) -> float:
    """Character Error Rate: ediciones / longitud de la referencia (0.0 = perfecto)."""
    ref = normalize_text(reference) if normalize else (reference or "")
    hyp = normalize_text(hypothesis) if normalize else (hypothesis or "")
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(list(ref), list(hyp)) / len(ref)


def wer(reference: str, hypothesis: str, *, normalize: bool = True) -> float:
    """Word Error Rate: ediciones de palabra / nº de palabras de la referencia."""
    ref = (normalize_text(reference) if normalize else (reference or "")).split()
    hyp = (normalize_text(hypothesis) if normalize else (hypothesis or "")).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)
