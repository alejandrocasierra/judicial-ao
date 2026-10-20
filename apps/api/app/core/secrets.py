"""Cifrado simétrico de secretos en reposo (API keys, password SMTP).

Usa Fernet con una clave derivada de `SECRETS_KEY` o, si no está definida, de
`JWT_SECRET`. El prefijo `enc:` distingue ciphertext de valores legacy en claro:
la lectura acepta ambos para permitir una migración sin downtime; la escritura
siempre cifra. Rotar `SECRETS_KEY` exige re-cifrar (script de mantenimiento).
"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

log = logging.getLogger(__name__)
_PREFIX = "enc:"


def _fernet() -> Fernet:
    s = get_settings()
    material = (s.ENCRYPTION_MASTER_KEY or s.JWT_SECRET).encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
    return Fernet(key)


def is_encrypted(value: str | None) -> bool:
    return bool(value and value.startswith(_PREFIX))


def encrypt(value: str | None) -> str | None:
    """Cifra un secreto. Vacío/None se conserva; idempotente."""
    if not value:
        return value
    if is_encrypted(value):
        return value
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str | None) -> str | None:
    """Descifra un secreto; si es legacy en claro lo devuelve tal cual.

    Un ciphertext corrupto no tumba el turno: se registra y se devuelve None para
    que el llamador use la clave global o muestre un error explícito.
    """
    if not value:
        return value
    if not is_encrypted(value):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        log.error("no se pudo descifrar un secreto; ¿cambió SECRETS_KEY?")
        return None
