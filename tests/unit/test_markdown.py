"""UT-MD — conversión de texto OCR a Markdown (representación semántica)."""
from app.services import markdown as md

import pytest

pytestmark = pytest.mark.unit


def test_ut_md_01_headings_lists_and_paragraphs():
    text = ("HECHOS\nEl demandante manifiesta que\nel pago no se realizó.\n\n"
            "PRETENSIONES\n1. Que se condene al demandado.")
    out = md.text_to_markdown(text)
    assert "### HECHOS" in out
    assert "### PRETENSIONES" in out
    assert "El demandante manifiesta que el pago no se realizó." in out
    assert "1. Que se condene al demandado." in out


def test_ut_md_02_bullet_lines_become_list():
    out = md.text_to_markdown("• Primer anexo\n• Segundo anexo")
    assert "- Primer anexo" in out and "- Segundo anexo" in out


def test_ut_md_03_plain_text_is_single_paragraph():
    assert md.text_to_markdown("una linea\notra linea") == "una linea otra linea"


def test_ut_md_04_empty():
    assert md.text_to_markdown("") == ""
    assert md.text_to_markdown(None) == ""
