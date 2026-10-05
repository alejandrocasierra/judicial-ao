"""UT-FILE — validación de archivos."""
import io
import zipfile

import pytest

from app.services import files

pytestmark = pytest.mark.unit


def test_ut_file_01_sniff_real_types(pdf_bytes):
    assert files.sniff(pdf_bytes()) == "pdf"
    assert files.sniff(b"\x89PNG\r\n\x1a\n" + b"0" * 20) == "png"
    assert files.sniff(b"\xff\xd8\xff\xe0" + b"0" * 20) == "jpeg"
    assert files.sniff(b"MZ\x90\x00 executable") is None


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd", "passwd"), ("..\\..\\windows\\system32.pdf", "system32.pdf"),
    ("a\x00b\x1f.pdf", "ab.pdf"), (".hidden.pdf", "hidden.pdf"), ("", "unnamed"), ('in<va>l|d?.pdf', "in_va_l_d_.pdf")])
def test_ut_file_02_sanitize_filename(raw, expected):
    assert files.sanitize_filename(raw) == expected


def test_ut_file_03_eicar_detected():
    with pytest.raises(files.MalwareFound):
        files.scan(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")


def test_ut_file_04_plain_zip_is_not_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("payload.sh", "rm -rf /")
    assert files.sniff(buf.getvalue()) is None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
    assert files.sniff(buf.getvalue()) == "docx"
