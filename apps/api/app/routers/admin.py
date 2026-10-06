"""Panel de administración: usuarios, roles, SMTP, agentes, skills, modelos, backups."""
from __future__ import annotations

import logging
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.db import one, rows, tx
from app.core.errors import AppError
from app.schemas import (AdminUserCreate, AdminUserPatch, AgentIn, AgentPatch, AiModelIn, AiModelPatch,
                         ModelCatalogIn, RoleIn, RolePatch, SkillIn, SkillPatch, SmtpSettingsPatch)
from app.security.deps import Principal, require_org
from app.security.passwords import hash_password, password_policy_ok
from app.services import alerts, audit, backup, builtin_agents, mailer, model_catalog, roles as roles_service

router = APIRouter(prefix="/admin", tags=["admin"])
log = logging.getLogger(__name__)


def _validate_password(pw: str) -> None:
    """Contraseña fijada por el administrador: debe cumplir la política
    (longitud mínima + mayúscula + minúscula + dígito + símbolo)."""
    if not password_policy_ok(pw):
        raise AppError("PASSWORD_WEAK", 422)

# Catálogo de modelos por proveedor. Se mantiene actualizado en código; el
# usuario puede escribir cualquier otro nombre (incluidos lanzamientos nuevos).
MODEL_CATALOG = {
    "anthropic": ["claude-opus-4-1", "claude-sonnet-4-5", "claude-haiku-4-5", "claude-3-5-sonnet-latest"],
    "openai": ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-luna",
               "gpt-5.6-terra", "gpt-5.5", "gpt-5.4", "gpt-5.2", "gpt-5.1", "gpt-5", "o4-mini", "o3"],
    "gemini": ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite",
               "gemini-3.1-flash-lite"],
    "kimi": ["kimi-k2", "moonshot-v1-128k", "moonshot-v1-32k"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner", "deepseek-v3"],
    "custom": [],
}


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
@router.get("/users")
def list_users(p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        items = rows(c, """SELECT id, email, full_name, org_role, locale, is_active,
                              last_login_at, created_at, version
                       FROM users ORDER BY created_at DESC""")
    return items


@router.post("/users")
def create_user(body: AdminUserCreate, request: Request, p: Principal = Depends(require_org("user.manage"))):
    # Si el administrador define la contraseña, se asigna de una vez (no se envía
    # correo de bienvenida). Si no, se genera una temporal y se envía el enlace.
    provided = bool(body.password)
    if provided:
        _validate_password(body.password or "")
    temp_pw = body.password or secrets.token_urlsafe(16)
    try:
        with tx(p.org_id, p.user_id) as c:
            u = one(c, """INSERT INTO users (organization_id, email, full_name, password_hash, org_role, locale)
                          VALUES (:o, :e, :n, :h, :r, :l) RETURNING id, email, full_name, org_role, locale, is_active""",
                    o=p.org_id, e=body.email, n=body.full_name, h=hash_password(temp_pw),
                    r=body.org_role, l=body.locale)
            audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.user_created",
                         entity_type="user", entity_id=str(u["id"]),
                         after={"email": u["email"], "org_role": u["org_role"], "password_set_by_admin": provided},
                         request=request)
    except IntegrityError:
        raise AppError("CASE_DUPLICATE", 409) from None
    if provided:
        return {**u, "invite_sent": False}
    # Enlace de bienvenida para definir contraseña
    s = get_settings()
    token = mailer.create_invite_token(str(u["id"]))
    link = f"{s.WEB_BASE_URL.rstrip('/')}/set-password?token={token}"
    mailer.send_invite_email(body.email, body.full_name, link, body.locale)
    return {**u, "invite_sent": True}


@router.patch("/users/{user_id}")
def patch_user(user_id: UUID, body: AdminUserPatch, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        cur = one(c, "SELECT id, version FROM users WHERE id = :u", u=str(user_id))
        if not cur:
            raise AppError("USER_NOT_FOUND", 404)
        if cur["version"] != body.expected_version:
            raise AppError("CONFLICT", 409)
        fields, params = [], {"u": str(user_id)}
        if body.full_name is not None:
            fields.append("full_name = :n")
            params["n"] = body.full_name
        if body.org_role is not None:
            fields.append("org_role = :r")
            params["r"] = body.org_role
        if body.locale is not None:
            fields.append("locale = :l")
            params["l"] = body.locale
        if body.is_active is not None:
            fields.append("is_active = :a")
            params["a"] = body.is_active
        if body.password is not None:
            _validate_password(body.password)
            fields.append("password_hash = :h")
            params["h"] = hash_password(body.password)
        if not fields:
            raise AppError("NOTHING_TO_UPDATE", 422)
        params["v"] = body.expected_version
        set_clause = ", ".join(fields)
        base = """UPDATE users SET /*SET_CLAUSE*/, version = version + 1
                  WHERE id = :u AND version = :v
                  RETURNING id, email, full_name, org_role, locale, is_active, version"""
        updated = one(c, base.replace("/*SET_CLAUSE*/", set_clause), **params)
        if not updated:
            raise AppError("CONFLICT", 409)
        if body.password is not None:
            # Al cambiar la contraseña se revocan las sesiones activas del usuario.
            c.execute(text("UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"),
                      {"u": str(user_id)})
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.user_updated",
                     entity_type="user", entity_id=str(user_id), after=body.model_dump(exclude_none=True),
                     request=request)
    return updated


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: UUID, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        u = one(c, "SELECT id, email, full_name, locale FROM users WHERE id = :u", u=str(user_id))
        if not u:
            raise AppError("USER_NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.password_reset_sent",
                     entity_type="user", entity_id=str(user_id), request=request)
    s = get_settings()
    token = mailer.create_reset_token(str(user_id))
    link = f"{s.WEB_BASE_URL.rstrip('/')}/reset-password?token={token}"
    mailer.send_reset_email(u["email"], u["full_name"], link, u["locale"])
    return {"reset_sent": True}


@router.get("/users/{user_id}")
def get_user(user_id: UUID, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        u = one(c, """SELECT id, email, full_name, org_role, locale, is_active,
                         last_login_at, created_at, version
                      FROM users WHERE id = :u""", u=str(user_id))
        if not u:
            raise AppError("USER_NOT_FOUND", 404)
    return u


# ---------------------------------------------------------------------------
# Configuración SMTP
# ---------------------------------------------------------------------------
@router.get("/smtp")
def get_smtp(p: Principal = Depends(require_org("user.manage"))):
    s = get_settings()
    with tx(p.org_id, p.user_id) as c:
        row = one(c, """SELECT from_name, from_email, server, port, security, username, cc_emails
                        FROM smtp_settings WHERE organization_id = :o""", o=p.org_id)
    if row:
        return {
            "from_name": row["from_name"],
            "from_email": row["from_email"],
            "server": row["server"],
            "port": row["port"],
            "security": row["security"],
            "username": row["username"],
            "cc_emails": [e.strip() for e in (row["cc_emails"] or "").split(",") if e.strip()],
        }
    # Fallback a variables de entorno si aún no se ha guardado configuración.
    return {
        "from_name": s.SMTP_FROM_NAME,
        "from_email": s.SMTP_FROM_EMAIL,
        "server": s.SMTP_SERVER,
        "port": s.SMTP_PORT,
        "security": s.SMTP_SECURITY,
        "username": s.SMTP_USERNAME,
        "cc_emails": [e.strip() for e in s.SMTP_CC_EMAILS.split(",") if e.strip()],
    }


@router.post("/smtp")
def set_smtp(body: SmtpSettingsPatch, request: Request, p: Principal = Depends(require_org("user.manage"))):
    # Guardamos en la tabla `smtp_settings`; el mailer la lee por organización.
    with tx(p.org_id, p.user_id) as c:
        c.execute(text("""INSERT INTO smtp_settings (organization_id, from_name, from_email, server, port,
                         security, username, password_encrypted, cc_emails, updated_by)
                  VALUES (:o, :fn, :fe, :s, :port, :sec, :u, :pw, :cc, :uid)
                  ON CONFLICT (organization_id) DO UPDATE SET
                    from_name = :fn, from_email = :fe, server = :s, port = :port,
                    security = :sec, username = :u, password_encrypted = :pw, cc_emails = :cc,
                    updated_by = :uid, updated_at = now()"""),
                  {"o": p.org_id, "fn": body.from_name, "fe": body.from_email, "s": body.server,
                   "port": body.port, "sec": body.security, "u": body.username,
                   "pw": mailer.encrypt_password(body.password) if body.password else None,
                   "cc": body.cc_emails or "", "uid": p.user_id})
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.smtp_updated", request=request)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Agentes
# ---------------------------------------------------------------------------
@router.get("/agents")
def list_agents(p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        builtin_agents.ensure_builtin_agents(c, p.org_id, p.user_id)
        builtin_agents.ensure_task_agents(c, p.org_id, p.user_id)
        builtin_agents.ensure_chat_agents(c, p.org_id, p.user_id)
        return rows(c, """SELECT id, name, system_prompt, skills, is_system, kind, created_at, updated_at
                          FROM agents ORDER BY is_system DESC, kind, name""")


@router.post("/agents")
def create_agent(body: AgentIn, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        a = one(c, """INSERT INTO agents (organization_id, name, system_prompt, skills, created_by, is_system, kind)
                      VALUES (:o, :n, :sp, :sk, :u, false, 'custom')
                      RETURNING id, name, system_prompt, skills, is_system, kind, created_at""",
                o=p.org_id, n=body.name, sp=body.system_prompt, sk=body.skills, u=p.user_id)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.agent_created",
                     entity_type="agent", entity_id=str(a["id"]), after={"name": body.name}, request=request)
    return a


@router.patch("/agents/{agent_id}")
def update_agent(agent_id: UUID, body: AgentPatch, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        if not one(c, "SELECT id FROM agents WHERE id = :i", i=str(agent_id)):
            raise AppError("NOT_FOUND", 404)
        sets = ["updated_at = now()"]
        params = {"i": str(agent_id)}
        if body.name is not None:
            sets.append("name = :n")
            params["n"] = body.name
        if body.system_prompt is not None:
            sets.append("system_prompt = :sp")
            params["sp"] = body.system_prompt
        if body.skills is not None:
            sets.append("skills = :sk")
            params["sk"] = body.skills
        a = one(c, f"UPDATE agents SET {', '.join(sets)} WHERE id = :i "
                   "RETURNING id, name, system_prompt, skills, created_at, updated_at", **params)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.agent_updated",
                     entity_type="agent", entity_id=str(agent_id), request=request)
    return a


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: UUID, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        if not one(c, "DELETE FROM agents WHERE id = :i RETURNING id", i=str(agent_id)):
            raise AppError("NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.agent_deleted",
                     entity_type="agent", entity_id=str(agent_id), request=request)
    return {"deleted": str(agent_id)}


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
@router.get("/skills")
def list_skills(p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        # Siembra automática de skills y agentes del chat (idempotente por nombre),
        # para que el panel muestre el catálogo completo con su uso real.
        builtin_agents.ensure_chat_builtins(c, p.org_id, p.user_id)
        return rows(c, """SELECT id, name, system_prompt, is_system, created_at, updated_at,
                          (SELECT count(*) FROM agents a WHERE s.id::text = ANY(a.skills)) AS used_by
                          FROM skills s ORDER BY is_system DESC, name""")


@router.post("/skills")
def create_skill(body: SkillIn, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        s = one(c, """INSERT INTO skills (organization_id, name, system_prompt, created_by)
                      VALUES (:o, :n, :sp, :u) RETURNING id, name, system_prompt, created_at""",
                o=p.org_id, n=body.name, sp=body.system_prompt, u=p.user_id)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.skill_created",
                     entity_type="skill", entity_id=str(s["id"]), after={"name": body.name}, request=request)
    return s


@router.patch("/skills/{skill_id}")
def update_skill(skill_id: UUID, body: SkillPatch, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        if not one(c, "SELECT id FROM skills WHERE id = :i", i=str(skill_id)):
            raise AppError("NOT_FOUND", 404)
        sets, params = ["updated_at = now()"], {"i": str(skill_id)}
        if body.name is not None:
            sets.append("name = :n")
            params["n"] = body.name
        if body.system_prompt is not None:
            sets.append("system_prompt = :sp")
            params["sp"] = body.system_prompt
        s = one(c, f"UPDATE skills SET {', '.join(sets)} WHERE id = :i RETURNING id, name, system_prompt, created_at, updated_at", **params)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.skill_updated",
                     entity_type="skill", entity_id=str(skill_id), request=request)
    return s


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: UUID, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        if not one(c, "DELETE FROM skills WHERE id = :i RETURNING id", i=str(skill_id)):
            raise AppError("NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.skill_deleted",
                     entity_type="skill", entity_id=str(skill_id), request=request)
    return {"deleted": str(skill_id)}


# ---------------------------------------------------------------------------
# Modelos IA
# ---------------------------------------------------------------------------
@router.get("/models/providers")
def model_providers(p: Principal = Depends(require_org("user.manage"))):
    """Proveedores soportados y catálogo de modelos estático (fallback)."""
    return {"providers": [{"id": k, "models": v} for k, v in MODEL_CATALOG.items()]}


@router.post("/models/available")
def available_models(body: ModelCatalogIn, p: Principal = Depends(require_org("user.manage"))):
    """Catálogo DINÁMICO: consulta la lista real de modelos del proveedor.

    Usa la API key del cuerpo o, si no viene, la guardada en algún modelo de la
    organización para ese proveedor; si no hay key o el proveedor no responde, cae al
    catálogo estático. Así los modelos nuevos aparecen solos en el selector."""
    provider = body.provider
    api_key = body.api_key
    api_base_url = body.api_base_url
    if not api_key:
        with tx(p.org_id, p.user_id) as c:
            row = one(c, """SELECT encrypted_api_key, api_base_url FROM ai_models
                WHERE provider = :p AND encrypted_api_key IS NOT NULL AND encrypted_api_key <> ''
                ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1""", p=provider)
        if row:
            api_key = row["encrypted_api_key"]
            api_base_url = api_base_url or row["api_base_url"]
    if api_key:
        try:
            models = model_catalog.list_provider_models(provider, api_key, api_base_url)
            if models:
                return {"provider": provider, "models": models, "source": "live"}
        except Exception:  # noqa: BLE001 — proveedor caído/mal key: no romper el alta
            log.warning("no se pudieron listar los modelos de %s en vivo", provider, exc_info=True)
    return {"provider": provider, "models": MODEL_CATALOG.get(provider, []), "source": "catalog"}


@router.get("/models")
def list_models(p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        return rows(c, """SELECT id, provider, model_name, api_base_url, is_default, ocr_enabled, asr_enabled,
                          created_at, updated_at,
                          (encrypted_api_key IS NOT NULL) AS has_api_key
                          FROM ai_models ORDER BY is_default DESC, provider, model_name""")


def _exclusive_flags(c, org_id: str, model_id: str | None, *, ocr: bool | None, asr: bool | None) -> None:
    """Garantiza un único modelo con OCR y otro con ASR activos por organización:
    al activar una capacidad en un modelo se desmarca la misma en los demás."""
    if ocr:
        c.execute(text("UPDATE ai_models SET ocr_enabled = false WHERE organization_id = :o AND id IS DISTINCT FROM :i"),
                  {"o": org_id, "i": model_id})
    if asr:
        c.execute(text("UPDATE ai_models SET asr_enabled = false WHERE organization_id = :o AND id IS DISTINCT FROM :i"),
                  {"o": org_id, "i": model_id})


@router.post("/models")
def create_model(body: AiModelIn, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        if body.is_default:
            c.execute(text("UPDATE ai_models SET is_default = false WHERE organization_id = :o"), {"o": p.org_id})
        _exclusive_flags(c, p.org_id, None, ocr=body.ocr_enabled, asr=body.asr_enabled)
        m = one(c, """INSERT INTO ai_models (organization_id, provider, model_name, api_key_ref, encrypted_api_key,
                      api_base_url, is_default, ocr_enabled, asr_enabled, created_by)
                      VALUES (:o, :pr, :mn, 'org', :key, :base, :d, :ocr, :asr, :u)
                      RETURNING id, provider, model_name, api_base_url, is_default, ocr_enabled, asr_enabled, created_at""",
                o=p.org_id, pr=body.provider, mn=body.model_name,
                key=body.api_key or None, base=(body.api_base_url or None),
                d=body.is_default, ocr=body.ocr_enabled, asr=body.asr_enabled, u=p.user_id)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.model_created",
                     entity_type="ai_model", entity_id=str(m["id"]),
                     after={"provider": body.provider, "model": body.model_name,
                            "api_base_url": body.api_base_url or None,
                            "ocr_enabled": body.ocr_enabled, "asr_enabled": body.asr_enabled}, request=request)
    return m


@router.patch("/models/{model_id}")
def update_model(model_id: UUID, body: AiModelPatch, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        if not one(c, "SELECT id FROM ai_models WHERE id = :i", i=str(model_id)):
            raise AppError("NOT_FOUND", 404)
        if body.is_default:
            c.execute(text("UPDATE ai_models SET is_default = false WHERE organization_id = :o"), {"o": p.org_id})
        _exclusive_flags(c, p.org_id, str(model_id), ocr=body.ocr_enabled, asr=body.asr_enabled)
        sets, params = ["updated_at = now()"], {"i": str(model_id)}
        if body.provider is not None:
            sets.append("provider = :pr")
            params["pr"] = body.provider
        if body.model_name is not None:
            sets.append("model_name = :mn")
            params["mn"] = body.model_name
        if body.api_key is not None:
            sets.append("encrypted_api_key = :key")
            params["key"] = body.api_key or None
        if body.api_base_url is not None:
            sets.append("api_base_url = :base")
            params["base"] = body.api_base_url or None
        if body.is_default is not None:
            sets.append("is_default = :d")
            params["d"] = body.is_default
        if body.ocr_enabled is not None:
            sets.append("ocr_enabled = :ocr")
            params["ocr"] = body.ocr_enabled
        if body.asr_enabled is not None:
            sets.append("asr_enabled = :asr")
            params["asr"] = body.asr_enabled
        m = one(c, f"UPDATE ai_models SET {', '.join(sets)} WHERE id = :i RETURNING id, provider, model_name, api_base_url, is_default, ocr_enabled, asr_enabled, updated_at", **params)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.model_updated",
                     entity_type="ai_model", entity_id=str(model_id), request=request)
    return m


@router.delete("/models/{model_id}")
def delete_model(model_id: UUID, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        if not one(c, "DELETE FROM ai_models WHERE id = :i RETURNING id", i=str(model_id)):
            raise AppError("NOT_FOUND", 404)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.model_deleted",
                     entity_type="ai_model", entity_id=str(model_id), request=request)
    return {"deleted": str(model_id)}


# ---------------------------------------------------------------------------
# Roles y permisos
# ---------------------------------------------------------------------------
@router.get("/roles")
def list_roles(p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        roles_service.ensure_builtin_roles(c, p.org_id, p.user_id)
        return rows(c, """SELECT id, code, name, description, permissions, is_system, created_at, updated_at
                          FROM roles ORDER BY is_system DESC, name""")


@router.get("/permissions")
def list_permissions(p: Principal = Depends(require_org("user.manage"))):
    return {"permissions": roles_service.all_permissions()}


@router.post("/roles")
def create_role(body: RoleIn, request: Request, p: Principal = Depends(require_org("user.manage"))):
    unknown = set(body.permissions) - set(roles_service.all_permissions())
    if unknown:
        raise AppError("VALIDATION_ERROR", 422, {"unknown_permissions": sorted(unknown)})
    try:
        with tx(p.org_id, p.user_id) as c:
            r = one(c, """INSERT INTO roles (organization_id, code, name, description, permissions, created_by)
                          VALUES (:o, :c, :n, :d, :pm, :u)
                          RETURNING id, code, name, description, permissions, is_system, created_at""",
                    o=p.org_id, c=body.code, n=body.name, d=body.description, pm=body.permissions, u=p.user_id)
            audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.role_created",
                         entity_type="role", entity_id=str(r["id"]), after={"code": body.code}, request=request)
    except IntegrityError:
        raise AppError("CASE_DUPLICATE", 409) from None
    return r


@router.patch("/roles/{role_id}")
def update_role(role_id: UUID, body: RolePatch, request: Request, p: Principal = Depends(require_org("user.manage"))):
    if body.permissions is not None:
        unknown = set(body.permissions) - set(roles_service.all_permissions())
        if unknown:
            raise AppError("VALIDATION_ERROR", 422, {"unknown_permissions": sorted(unknown)})
    with tx(p.org_id, p.user_id) as c:
        if not one(c, "SELECT id FROM roles WHERE id = :i", i=str(role_id)):
            raise AppError("NOT_FOUND", 404)
        sets, params = ["updated_at = now()"], {"i": str(role_id)}
        if body.name is not None:
            sets.append("name = :n")
            params["n"] = body.name
        if body.description is not None:
            sets.append("description = :d")
            params["d"] = body.description
        if body.permissions is not None:
            sets.append("permissions = :pm")
            params["pm"] = body.permissions
        r = one(c, f"UPDATE roles SET {', '.join(sets)} WHERE id = :i "
                   "RETURNING id, code, name, description, permissions, is_system, updated_at", **params)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.role_updated",
                     entity_type="role", entity_id=str(role_id), request=request)
    roles_service.invalidate_cache(p.org_id)
    return r


@router.delete("/roles/{role_id}")
def delete_role(role_id: UUID, request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        cur = one(c, "SELECT is_system, code FROM roles WHERE id = :i", i=str(role_id))
        if not cur:
            raise AppError("NOT_FOUND", 404)
        if cur["code"] in ("ORG_ADMIN", "SYSTEM"):
            raise AppError("FORBIDDEN", 403, {"detail": "Rol crítico del sistema; no se elimina"})
        used = one(c, "SELECT count(*) AS n FROM users WHERE org_role = :r", r=cur["code"])["n"]
        if used:
            raise AppError("INVALID_STATE_TRANSITION", 409, {"detail": "Rol asignado a usuarios", "count": used})
        c.execute(text("DELETE FROM roles WHERE id = :i"), {"i": str(role_id)})
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.role_deleted",
                     entity_type="role", entity_id=str(role_id), request=request)
    roles_service.invalidate_cache(p.org_id)
    return {"deleted": str(role_id)}


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------
@router.get("/backups")
def list_backups(p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        return rows(c, """SELECT id, backup_type, status, size_bytes, created_at, completed_at
                          FROM backups ORDER BY created_at DESC LIMIT 100""")


@router.post("/backups")
def trigger_backup(request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        b = one(c, """INSERT INTO backups (organization_id, backup_type, status, created_by)
                      VALUES (:o, 'manual', 'RUNNING', :uid) RETURNING id, status""",
                o=p.org_id, uid=p.user_id)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.backup_triggered",
                     entity_type="backup", entity_id=str(b["id"]), request=request)
        # Ejecutar backup inmediatamente (en producción sería un job Celery)
        try:
            result = backup.backup_postgres(c, p.org_id, str(b["id"]))
            b.update(result)
        except Exception as e:
            c.execute(text("UPDATE backups SET status = 'FAILED', completed_at = now() WHERE id = :id"), {"id": str(b["id"])})
            raise AppError("INTERNAL_ERROR", 500, {"detail": str(e)}) from e
    return b


@router.get("/backups/verify-rpo")
def verify_rpo(p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        return backup.verify_rpo(c, p.org_id)


@router.post("/backups/restore-test")
def restore_test(request: Request, p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        result = backup.restore_test(c, p.org_id)
        audit.record(c, org_id=p.org_id, actor_id=p.user_id, action="admin.restore_test",
                     entity_type="backup", after=result, request=request)
    return result


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------
@router.get("/alerts")
def list_alerts(p: Principal = Depends(require_org("user.manage"))):
    with tx(p.org_id, p.user_id) as c:
        result = alerts.check_all(c, p.org_id)
        alerts.log_alerts(result, p.org_id)
    return result
