"""UT-OCRD — alineación de líneas para el corpus de caligrafía."""
from __future__ import annotations

import pytest

from app.services.handwriting_dataset import (
    align_lines,
    build_pairs,
    normalize_label,
    reference_lines,
    similarity,
)

pytestmark = pytest.mark.unit


def test_ut_ocrd_01_reference_lines_limpia_vacias():
    assert reference_lines("uno\n\n  \ndos  \n") == ["uno", "dos"]
    assert normalize_label("  hola   mundo ") == "hola mundo"


def test_ut_ocrd_02_align_monotono_con_ruido():
    detected = ["COARENTA Y SEIS CIVIL", "SANTA FE DE BOGOTA", "morant"]
    reference = ["COARENTA Y SEIS CIVIL", "SANTA FE DE BOGOTA D.C:", "morant", "linea de mas"]
    pairs = align_lines(detected, reference)
    assert pairs == [(0, 0), (1, 1), (2, 2)]


def test_ut_ocrd_03_descarta_parejas_poco_similares():
    # Una línea detectada irrelevante no debe emparejarse con una referencia distinta.
    assert align_lines(["xyz"], ["texto completamente distinto"]) == []


def test_ut_ocrd_04_build_pairs_devuelve_etiquetas_corregidas():
    detected = ["primer linea", "segunda lnea"]
    corrected = "primer linea\nsegunda linea"
    assert build_pairs(detected, corrected) == [(0, "primer linea"), (1, "segunda linea")]


def test_ut_ocrd_05_similarity_basica():
    assert similarity("", "algo") == 0.0
    assert similarity("hola", "hola") == 100.0
    assert similarity("hola", "adios") < 50
