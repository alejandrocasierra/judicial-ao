"""Construcción del corpus de caligrafía a partir de las correcciones humanas.

Cada vez que una persona corrige el OCR de una página (`document_pages.human_corrected`),
queda un par implícito **(imagen de página, texto correcto)**. Para entrenar un OCR de
manuscrito hace falta al nivel de **línea**: (recorte de línea, texto). Este módulo alinea
las líneas que detecta el OCR con las líneas del texto corregido para producir esos pares.

La alineación es monótona (respeta el orden de lectura) y tolerante a errores, porque el
texto detectado suele ser una lectura defectuosa del texto correcto.
"""
from __future__ import annotations

from rapidfuzz import fuzz

MIN_SIMILARITY = 60.0


def normalize_label(text: str) -> str:
    return " ".join((text or "").split())


def reference_lines(text: str) -> list[str]:
    """Líneas útiles del texto corregido (sin vacías)."""
    return [ln for ln in (normalize_label(line) for line in (text or "").splitlines()) if ln]


def similarity(a: str, b: str) -> float:
    """Similitud 0-100 entre dos líneas normalizadas (0 si alguna está vacía)."""
    a, b = normalize_label(a), normalize_label(b)
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(a, b)


def align_lines(detected: list[str], reference: list[str],
                min_similarity: float = MIN_SIMILARITY) -> list[tuple[int, int]]:
    """Alinea líneas detectadas con líneas de referencia de forma monótona.

    Devuelve los pares `(índice_detectado, índice_referencia)` con similitud suficiente.
    Usa programación dinámica maximizando la suma de similitudes (sin penalizar huecos,
    porque el OCR puede omitir o inventar líneas).
    """
    n, m = len(detected), len(reference)
    if not n or not m:
        return []
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    take = [[False] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = score[i - 1][j - 1] + similarity(detected[i - 1], reference[j - 1])
            up, left = score[i - 1][j], score[i][j - 1]
            best = max(diag, up, left)
            score[i][j] = best
            take[i][j] = best == diag
    pairs: list[tuple[int, int]] = []
    i, j = n, m
    while i > 0 and j > 0:
        if take[i][j]:
            if similarity(detected[i - 1], reference[j - 1]) >= min_similarity:
                pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif score[i - 1][j] >= score[i][j - 1]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def build_pairs(detected: list[str], corrected_text: str,
                min_similarity: float = MIN_SIMILARITY) -> list[tuple[int, str]]:
    """Pares listos para el dataset: `(índice_de_línea_detectada, texto_correcto)`."""
    reference = reference_lines(corrected_text)
    return [(di, reference[ri]) for di, ri in align_lines(detected, reference, min_similarity)]
