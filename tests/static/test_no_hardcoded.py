"""STATIC: nada quemado en el código (requisito del proyecto) y coherencia de configuración.
No requiere base de datos; se ejecuta con SKIP_DB_RESET=1 en el pipeline rápido."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "apps" / "api" / "app"
CODE = [p for p in APP.rglob("*.py") if "__pycache__" not in p.parts]

SECRET_PATTERNS = {
    "clave_api": re.compile(r"(sk-ant-|sk-[A-Za-z0-9]{20}|AKIA[0-9A-Z]{16})"),
    "asignacion_secreta": re.compile(r"(?i)(password|secret|api_key|token)\s*=\s*[\"'][^\"'{}\s]{6,}[\"']"),
    "cadena_conexion": re.compile(r"(postgres(ql)?|redis|mysql)://[^\s\"'{}]+:[^\s\"'{}]+@"),
    "correo": re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}"),
    "url_http": re.compile(r"https?://(?!www\.w3\.org)[\w.-]+"),
    "ip_privada": re.compile(r"\b(?:10|127|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?:\.\d{1,3})?\b"),
}


@pytest.mark.parametrize("path", CODE, ids=lambda p: str(p.relative_to(ROOT)))
def test_static_01_no_secrets_urls_or_emails_in_app_code(path):
    text = path.read_text(encoding="utf-8")
    hits = [(k, m.group(0)) for k, rx in SECRET_PATTERNS.items() for m in rx.finditer(text)]
    assert hits == [], f"valores quemados en {path.name}: {hits}"


def _settings_fields() -> set[str]:
    tree = ast.parse((APP / "core" / "config.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings")
    return {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}


def _env_keys(path: Path) -> set[str]:
    return {ln.split("=", 1)[0].strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#") and "=" in ln}


def test_static_02_every_setting_is_documented_in_env_example():
    missing = _settings_fields() - _env_keys(ROOT / ".env.example")
    assert missing == set(), f"variables de Settings ausentes en .env.example: {sorted(missing)}"


def test_static_03_settings_have_no_defaults():
    """Toda configuración viene del entorno: los campos de Settings no tienen valor por defecto."""
    tree = ast.parse((APP / "core" / "config.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings")
    with_default = []
    for n in cls.body:
        if isinstance(n, ast.AnnAssign) and n.value is not None:
            v = n.value
            is_field_without_default = isinstance(v, ast.Call) and getattr(v.func, "id", "") == "Field" and not v.args \
                and not any(k.arg in ("default", "default_factory") for k in v.keywords)
            if not is_field_without_default:
                with_default.append(n.target.id)
    assert with_default == [], f"Settings con valor por defecto quemado: {with_default}"


def test_static_04_env_example_has_no_real_secrets():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if re.match(r"^\w*(SECRET|PASSWORD|API_KEY)=", line):
            value = line.split("=", 1)[1].split("#")[0].strip()
            assert value in ("", "__GENERATE__", "__SET_ME__") or value.startswith("${"), line


def test_static_05_every_error_code_used_exists_in_both_catalogs():
    used = set()
    for p in CODE:
        used |= set(re.findall(r"AppError\(\s*\"([A-Z_]+)\"", p.read_text(encoding="utf-8")))
        used |= set(re.findall(r"\"([A-Z][A-Z_]+)\"", p.read_text(encoding="utf-8"))) & {"NOT_FOUND", "METHOD_NOT_ALLOWED"}
    for lang in ("es", "en"):
        cat = json.loads((ROOT / "packages" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        assert used <= set(cat["errors"]), f"{lang}: faltan {sorted(used - set(cat['errors']))}"


def test_static_06_every_message_key_used_exists():
    used = set()
    for p in CODE:
        used |= set(re.findall(r"t\(\s*\"(messages\.[a-z_]+)\"", p.read_text(encoding="utf-8")))
    for lang in ("es", "en"):
        cat = json.loads((ROOT / "packages" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        assert {k.split(".", 1)[1] for k in used} <= set(cat["messages"]), lang


def test_static_07_sql_is_never_built_from_user_values():
    """Sólo se permiten f-strings SQL con identificadores de listas blancas internas (tablas/columnas)."""
    allowed = {"table", "sets", "CASE_COLS", "x"}  # identificadores internos, nunca entrada del usuario
    offenders = []
    for p in CODE:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.JoinedStr):
                src = ast.unparse(node)
                if re.search(r"(?i)\b(SELECT|INSERT|UPDATE|DELETE)\b", src):
                    names = {n.id for v in node.values if isinstance(v, ast.FormattedValue) for n in ast.walk(v.value) if isinstance(n, ast.Name)}
                    if names - allowed:
                        offenders.append((p.name, sorted(names - allowed)))
    assert offenders == [], offenders


def test_static_08_seed_data_contains_no_real_domains():
    text = (ROOT / "apps" / "api" / "seeds" / "data" / "seed_data.yaml").read_text(encoding="utf-8")
    assert not re.search(r"@(gmail|hotmail|outlook|yahoo)\.", text)
