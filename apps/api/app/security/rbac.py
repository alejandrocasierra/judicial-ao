"""RBAC como código: la política vive en RBAC_POLICY_FILE (YAML), no en el código."""
from __future__ import annotations

from functools import lru_cache

import yaml

from app.core.config import get_settings


# Overrides por organización (roles personalizados), cargados desde la tabla `roles`.
_org_overrides: dict[tuple[str, str], set[str]] = {}


def set_org_role(org_id: str, code: str, permissions: set[str]) -> None:
    _org_overrides[(org_id, code)] = set(permissions)


def clear_org(org_id: str) -> None:
    for key in [k for k in _org_overrides if k[0] == org_id]:
        _org_overrides.pop(key, None)


@lru_cache
def policy() -> dict:
    s = get_settings()
    data = yaml.safe_load(s.path(s.RBAC_POLICY_FILE).read_text(encoding="utf-8"))
    known = set(data["permissions"])
    for group in ("org_roles", "case_roles"):
        for role, perms in data[group].items():
            unknown = set(perms) - known - {"*"}
            if unknown:
                raise ValueError(f"RBAC policy: unknown permissions {unknown} in {group}.{role}")
    return data


def org_permissions(org_role: str, org_id: str | None = None) -> set[str]:
    if org_id is not None and (org_id, org_role) in _org_overrides:
        return set(_org_overrides[(org_id, org_role)])
    perms = policy()["org_roles"].get(org_role, [])
    return set(policy()["permissions"]) if "*" in perms else set(perms)


def case_permissions(org_role: str, case_role: str | None, org_id: str | None = None) -> set[str]:
    """Permisos efectivos en un expediente = rol org ∩ rol en el expediente.
    ORG_ADMIN ('*') tiene todo sin membresía (siempre dentro de su organización por RLS)."""
    if "*" in policy()["org_roles"].get(org_role, []):
        return set(policy()["permissions"])
    perms = org_permissions(org_role, org_id)
    if case_role is None:
        return set()
    return perms & set(policy()["case_roles"].get(case_role, []))


def is_org_level(permission: str) -> bool:
    return permission in set(policy()["org_level_permissions"])
