"""Validación de archivos: tipo real por firma (magic bytes), nombre saneado, malware."""
from __future__ import annotations

import hashlib
import re
import socket
import struct
import unicodedata
import zipfile
from io import BytesIO
from pathlib import Path

from app.core.config import get_settings

# Firmas binarias de formatos (constantes técnicas de los estándares, no datos de negocio)
MIME = {"pdf": "application/pdf", "png": "image/png", "jpeg": "image/jpeg", "tiff": "image/tiff",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "wav": "audio/wav",
        "mp3": "audio/mpeg"}
EXT_ALIASES = {"jpg": "jpeg", "tif": "tiff"}
EICAR_MARK = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"


def sniff(data: bytes) -> str | None:
    h = data[:64]
    if h.startswith(b"%PDF-"):
        return "pdf"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if h.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if h[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if h.startswith(b"PK\x03\x04"):
        try:
            names = zipfile.ZipFile(BytesIO(data)).namelist()
            if "xl/workbook.xml" in names:
                return "xlsx"
            if "word/document.xml" in names:
                return "docx"
            return None
        except zipfile.BadZipFile:
            return None
    if h[4:8] == b"ftyp":
        # MP4/MOV/3GP comparten la firma ftyp; usamos extensión si está disponible,
        # de lo contrario devolvemos el tipo genérico mp4.
        return "mov" if (len(data) >= 12 and data[8:12] == b"qt  ") else "mp4"
    if h.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if h.startswith(b"RIFF") and h[8:12] == b"WAVE":
        return "wav"
    if h.startswith(b"ID3") or h[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    return None


def sniff_path(path: Path, head_bytes: int = 1024) -> str | None:
    """Versión path-aware de sniff; para ZIP lee el directorio real del archivo."""
    with open(path, "rb") as f:
        head = f.read(head_bytes)
    h = head[:64]
    if h.startswith(b"%PDF-"):
        return "pdf"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if h.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if h[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if h.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
            if "xl/workbook.xml" in names:
                return "xlsx"
            if "word/document.xml" in names:
                return "docx"
            return None
        except zipfile.BadZipFile:
            return None
    if h[4:8] == b"ftyp":
        return "mov" if (len(head) >= 12 and head[8:12] == b"qt  ") else "mp4"
    if h.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if h.startswith(b"RIFF") and h[8:12] == b"WAVE":
        return "wav"
    if h.startswith(b"ID3") or h[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    return None


def extension(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXT_ALIASES.get(ext, ext)


def sanitize_filename(name: str, max_len: int = 200) -> str:
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"%(?:[01][0-9A-Fa-f]|7[Ff])", "", name)  # controles codificados (%00, %0a, %7f...)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().lstrip(".")
    name = re.sub(r"[<>:\"|?*]", "_", name)
    return name[:max_len] or "unnamed"


class MalwareFound(Exception):
    pass


def scan(data: bytes) -> None:
    s = get_settings()
    if s.MALWARE_SCANNER == "basic":
        if EICAR_MARK in data:
            raise MalwareFound("eicar")
        return
    # clamd INSTREAM
    with socket.create_connection((s.CLAMAV_HOST, s.CLAMAV_PORT), timeout=30) as sock:
        sock.sendall(b"zINSTREAM\0")
        for i in range(0, len(data), 65536):
            chunk = data[i:i + 65536]
            sock.sendall(struct.pack("!L", len(chunk)) + chunk)
        sock.sendall(struct.pack("!L", 0))
        reply = sock.recv(4096)
    if b"FOUND" in reply:
        raise MalwareFound(reply.decode(errors="ignore"))
    if b"OK" not in reply:
        raise RuntimeError("clamav_error")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash SHA-256 de un archivo sin cargarlo completo en memoria."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def scan_file(path: Path, chunk_size: int = 1024 * 1024) -> None:
    """Escanea un archivo grande con el scanner configurado.

    En modo 'basic' lee la firma EICAR; en modo 'clamav' usa FILDES/INSTREAM
    por chunks para no cargar archivos de varios gigabytes en RAM.
    """
    s = get_settings()
    if s.MALWARE_SCANNER == "basic":
        # La firma EICAR cabe en el primer chunk; no hace falta leer todo.
        with open(path, "rb") as f:
            head = f.read(max(chunk_size, len(EICAR_MARK) + 256))
        if EICAR_MARK in head:
            raise MalwareFound("eicar")
        return
    # clamd INSTREAM por chunks
    with socket.create_connection((s.CLAMAV_HOST, s.CLAMAV_PORT), timeout=30) as sock:
        sock.sendall(b"zINSTREAM\0")
        with open(path, "rb") as f:
            while chunk := f.read(chunk_size):
                sock.sendall(struct.pack("!L", len(chunk)) + chunk)
        sock.sendall(struct.pack("!L", 0))
        reply = sock.recv(4096)
    if b"FOUND" in reply:
        raise MalwareFound(reply.decode(errors="ignore"))
    if b"OK" not in reply:
        raise RuntimeError("clamav_error")
