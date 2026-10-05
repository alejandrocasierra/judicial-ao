from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_ph = PasswordHasher()
# Hash señuelo para igualar tiempos cuando el usuario no existe (anti-enumeración)
_DUMMY = _ph.hash("timing-equalizer-not-a-credential")


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str | None) -> bool:
    try:
        return _ph.verify(hashed or _DUMMY, pw) and hashed is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_policy_ok(pw: str) -> bool:
    s = get_settings()
    return (len(pw) >= s.PASSWORD_MIN_LENGTH and re.search(r"[a-z]", pw) is not None
            and re.search(r"[A-Z]", pw) is not None and re.search(r"\d", pw) is not None
            and re.search(r"[^A-Za-z0-9]", pw) is not None)
