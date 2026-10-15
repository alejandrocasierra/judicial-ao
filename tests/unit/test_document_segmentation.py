"""UT-SEG — segmentación estructural de documentos judiciales."""
import pytest

from app.services import document_segmentation as ds

pytestmark = pytest.mark.unit


def test_seg_01_classifies_decision():
    """Una página con RESUELVE se clasifica como decisión."""
    txt = "En mérito de lo expuesto, el JUZGADO RESUELVE: PRIMERO: ADMITIR la demanda."
    assert ds.classify_text(txt) == ds.SEGMENT_DECISION


def test_seg_02_classifies_body():
    """Antecedentes/considerandos se clasifican como cuerpo."""
    txt = "II. CONSIDERANDOS. La parte demandante alega que el contrato no fue cumplido."
    assert ds.classify_text(txt) == ds.SEGMENT_BODY


def test_seg_03_classifies_header():
    """Encabezado con radicado y juzgado se clasifica como encabezado."""
    txt = "JUZGADO 15 CIVIL DEL CIRCUITO DE BOGOTÁ. Radicación No. 2020-00361. Proceso No. 11001."
    assert ds.classify_text(txt) == ds.SEGMENT_HEADER


def test_seg_04_classifies_annexes():
    """Los anexos se clasifican como anexos."""
    txt = "ANEXO No. 3 - Certificado bancario expedido por el Banco XYZ."
    assert ds.classify_text(txt) == ds.SEGMENT_ANNEXES


def test_seg_05_decision_beats_body():
    """Si convive decisión y cuerpo, gana la decisión."""
    txt = "ANTECEDENTES: ... Por lo anterior, RESUELVE: declarar probada la excepción."
    assert ds.classify_text(txt) == ds.SEGMENT_DECISION


def test_seg_06_defaults_to_body():
    """Texto genérico sin marcas cae en cuerpo."""
    assert ds.classify_text("Se allegan memoriales para su valoración probatoria.") == ds.SEGMENT_BODY


def test_seg_07_annotate_segments_marks_items():
    """annotate_segments agrega el atributo segment sin sobrescribir uno previo."""
    items = [
        {"handle": "E1", "text": "RESUELVE: admitir la demanda."},
        {"handle": "E2", "text": "CONSIDERANDOS: los hechos probados son."},
        {"handle": "E3", "text": "texto", "segment": "anexos"},
    ]
    n = ds.annotate_segments(items)
    assert n == 2
    assert items[0]["segment"] == ds.SEGMENT_DECISION
    assert items[1]["segment"] == ds.SEGMENT_BODY
    assert items[2]["segment"] == "anexos"


def test_seg_08_annotate_empty_and_none():
    """No falla con lista vacía ni con texto None."""
    assert ds.annotate_segments([]) == 0
    assert ds.classify_text(None) == ds.SEGMENT_BODY
