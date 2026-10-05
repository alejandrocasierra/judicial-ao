"""SEC-AUTH: fuerza bruta, bloqueo, enumeración de usuarios y almacenamiento de contraseñas (amenaza T3)."""
from __future__ import annotations

import uuid

from helpers import assert_error, fetch


def _login(client, e, p, lang="es"):
    return client.post("/v1/auth/login", json={"email": e, "password": p}, headers={"Accept-Language": lang})


def test_sec_auth_01_no_user_enumeration(client, email, password, settings):
    """Usuario inexistente, contraseña errónea y usuario inactivo => misma respuesta."""
    wrong = password + "x"
    unknown = _login(client, email(f"nadie-{uuid.uuid4().hex[:8]}"), wrong)
    bad_pw = _login(client, email("lector.alfa"), wrong)
    inactive = _login(client, email("inactivo.alfa"), password)
    errs = [assert_error(r, 401, "AUTH_INVALID_CREDENTIALS") for r in (unknown, bad_pw, inactive)]
    assert len({e["message"] for e in errs}) == 1


def test_sec_auth_02_lockout_after_max_failed_attempts(client, email, password, settings, restore_user):
    target = email("analista.alfa")
    restore_user(target)
    for _ in range(settings.AUTH_MAX_FAILED_ATTEMPTS):
        assert _login(client, target, password + "!").status_code == 401
    # Con la cuenta bloqueada incluso la contraseña correcta es rechazada
    assert_error(_login(client, target, password), 423, "AUTH_ACCOUNT_LOCKED")


def test_sec_auth_03_successful_login_resets_counter(client, email, password, settings, owner_db, restore_user):
    target = email("revisor.alfa")
    restore_user(target)
    for _ in range(settings.AUTH_MAX_FAILED_ATTEMPTS - 1):
        _login(client, target, password + "!")
    assert _login(client, target, password).status_code == 200
    assert fetch(owner_db, "SELECT failed_login_attempts FROM users WHERE email = %s", (target,)) == [(0,)]


def test_sec_auth_04_login_rate_limit(client, settings, email):
    target = email(f"fuerza-bruta-{uuid.uuid4().hex[:8]}")
    codes = [_login(client, target, "x").status_code for _ in range(settings.RATE_LIMIT_LOGIN_PER_MINUTE + 1)]
    assert codes[:-1] == [401] * settings.RATE_LIMIT_LOGIN_PER_MINUTE and codes[-1] == 429


def test_sec_auth_05_passwords_are_argon2id_hashed_and_unique_salted(owner_db, password):
    hashes = [h for (h,) in fetch(owner_db, "SELECT password_hash FROM users")]
    assert hashes and all(h.startswith("$argon2id$") for h in hashes)
    assert len(set(hashes)) == len(hashes), "misma contraseña semilla => hashes distintos (sal única)"
    assert all(password not in h for h in hashes)


def test_sec_auth_06_me_never_exposes_secrets(client, auth):
    body = client.get("/v1/auth/me", headers=auth("admin.alfa")).text
    for k in ("password", "hash", "failed_login", "locked_until"):
        assert k not in body


def test_sec_auth_07_email_is_case_insensitive_but_strictly_validated(client, email, password):
    assert _login(client, email("lector.alfa").upper(), password).status_code == 200
    for bad in ("sin-arroba", "a@b", "a@@b.co", "<script>@x.co", "a" * 300 + "@x.co"):
        assert_error(_login(client, bad, password), 422, "VALIDATION_ERROR")


def test_sec_auth_08_failed_logins_are_audited_without_password(client, email, owner_db):
    secret_attempt = f"Intento-{uuid.uuid4().hex}"
    _login(client, email("lector.alfa"), secret_attempt)
    blob = fetch(owner_db, "SELECT string_agg(coalesce(before::text,'') || coalesce(after::text,''), ' ') FROM audit_logs")[0][0] or ""
    assert secret_attempt not in blob
    assert fetch(owner_db, "SELECT count(*) FROM audit_logs WHERE action = 'auth.login_failed'")[0][0] >= 1
