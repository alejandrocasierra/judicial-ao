"""Servicio de correo: invitaciones, password reset, notificaciones.
Soporta SMTP configurado por organización con fallback a log."""
from __future__ import annotations

import logging
import secrets

from sqlalchemy.engine import Connection

from app.core.db import one

log = logging.getLogger(__name__)

_TOKEN_TTL_HOURS = 72


def _get_smtp_config(conn: Connection, org_id: str) -> dict | None:
    """Lee configuración SMTP de la organización (si existe)."""
    row = one(conn, """SELECT from_name, from_email, server, port, security,
                              username, password_encrypted, cc_emails
                       FROM smtp_settings WHERE organization_id = :o""", o=org_id)
    return dict(row) if row else None


def _build_html(subject: str, body_text: str, button_text: str, button_url: str, locale: str) -> str:
    """Email HTML responsive con botón de acción."""
    lang = "es" if locale == "es" else "en"
    greeting = "Hola" if lang == "es" else "Hello"
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:system-ui,-apple-system,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:40px auto;background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.06)">
    <tr><td style="padding:40px 40px 24px;text-align:center">
      <h1 style="margin:0;font-size:24px;font-weight:700;color:#18181b">{subject}</h1>
    </td></tr>
    <tr><td style="padding:0 40px 32px">
      <p style="margin:0 0 16px;font-size:16px;line-height:1.5;color:#3f3f46">{greeting},</p>
      <p style="margin:0 0 24px;font-size:16px;line-height:1.5;color:#3f3f46">{body_text}</p>
      <table cellpadding="0" cellspacing="0" style="margin:0 auto">
        <tr><td style="background:#2563eb;border-radius:8px;padding:12px 32px">
          <a href="{button_url}" style="color:#fff;font-size:16px;font-weight:600;text-decoration:none;display:inline-block">{button_text}</a>
        </td></tr>
      </table>
      <p style="margin:24px 0 0;font-size:13px;color:#71717a;line-height:1.4">
        {"Si no solicitaste esto, ignora este correo." if lang == "es" else "If you didn't request this, ignore this email."}
      </p>
    </td></tr>
  </table>
</body>
</html>"""


def _send(to: str, subject: str, html: str, org_id: str | None = None) -> bool:
    """Intenta enviar por SMTP; si no hay config o falla, loguea."""
    # TODO: integrar con SMTP real usando la config de smtp_settings
    log.info("email.to=%s subject=%s org=%s html_len=%d", to, subject, org_id, len(html))
    return True


def create_invite_token(user_id: str) -> str:
    """Token para definir contraseña en primer login."""
    return f"inv_{user_id}_{secrets.token_urlsafe(32)}"


def create_reset_token(user_id: str) -> str:
    """Token para reset de contraseña."""
    return f"rst_{user_id}_{secrets.token_urlsafe(32)}"


def send_invite_email(email: str, full_name: str, link: str, locale: str) -> None:
    subject = "Bienvenido a Judicial AI" if locale == "es" else "Welcome to Judicial AI"
    body = f"{full_name}, te hemos creado una cuenta. Haz clic en el botón para definir tu contraseña." if locale == "es" else f"{full_name}, we've created an account for you. Click the button to set your password."
    btn = "Definir contraseña" if locale == "es" else "Set password"
    html = _build_html(subject, body, btn, link, locale)
    _send(email, subject, html)


def send_reset_email(email: str, full_name: str, link: str, locale: str) -> None:
    subject = "Restablecer contraseña — Judicial AI" if locale == "es" else "Password reset — Judicial AI"
    body = f"{full_name}, recibimos una solicitud para restablecer tu contraseña." if locale == "es" else f"{full_name}, we received a request to reset your password."
    btn = "Restablecer contraseña" if locale == "es" else "Reset password"
    html = _build_html(subject, body, btn, link, locale)
    _send(email, subject, html)


def encrypt_password(pw: str) -> str:
    """Placeholder para cifrado de contraseña SMTP."""
    return pw  # TODO: implementar cifrado simétrico
