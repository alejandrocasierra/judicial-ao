"""Infraestructura de pruebas. Usa ENV_FILE (por defecto .env.test). Con APP_ENV=test
recrea la base de datos, migra y siembra antes de la sesión (desactivar: SKIP_DB_RESET=1)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("ENV_FILE", str(ROOT / ".env.test"))
os.environ["ENV_FILE"] = str((ROOT / os.environ["ENV_FILE"]).resolve()) if not Path(os.environ["ENV_FILE"]).is_absolute() else os.environ["ENV_FILE"]
sys.path.insert(0, str(ROOT / "scripts"))
import envload  # noqa: E402

envload.load(os.environ["ENV_FILE"], override=True)
if os.environ.get("APP_ENV") != "test":
    raise SystemExit("Tests require APP_ENV=test in ENV_FILE (protege datos reales)")


def _run(cmd: list[str], cwd: Path = ROOT) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=os.environ.copy())
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{r.stdout}\n{r.stderr}")


def pytest_sessionstart(session):
    if os.environ.get("SKIP_DB_RESET") == "1":
        return
    _run([sys.executable, "scripts/db_create.py", "--drop"])
    _run([sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"])
    _run([sys.executable, "-m", "seeds.seed"], cwd=ROOT / "apps" / "api")


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app
    return fastapi_app


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from app.providers.llm import FakeLLM
    from app.services import ratelimit
    ratelimit.reset()
    FakeLLM.script = None
    yield
    FakeLLM.script = None


@pytest.fixture(scope="session")
def email():
    domain = os.environ["SEED_EMAIL_DOMAIN"]
    return lambda local: f"{local}@{domain}"


@pytest.fixture(scope="session")
def password():
    return os.environ["SEED_DEFAULT_PASSWORD"]


_TOKENS: dict[str, str] = {}


@pytest.fixture(scope="session")
def auth(client, email, password):
    """auth('abogada.alfa') -> headers con Bearer token (cacheado por sesión)."""
    def _h(local: str, lang: str | None = None) -> dict:
        if local not in _TOKENS:
            from app.services import ratelimit
            ratelimit.reset()
            r = client.post("/v1/auth/login", json={"email": email(local), "password": password})
            assert r.status_code == 200, r.text
            _TOKENS[local] = r.json()["access_token"]
        h = {"Authorization": f"Bearer {_TOKENS[local]}"}
        if lang:
            h["Accept-Language"] = lang
        return h
    return _h


@pytest.fixture(scope="session")
def ids(client, auth):
    """IDs de datos semilla resueltos vía API (nada quemado)."""
    alfa = {c["case_number"]: c for c in client.get("/v1/cases", headers=auth("admin.alfa")).json()}
    beta = {c["case_number"]: c for c in client.get("/v1/cases", headers=auth("admin.beta")).json()}
    pago = next(c for c in alfa.values() if "80.000.000" in c["title"])
    vacio = next(c for c in alfa.values() if c["id"] != pago["id"])
    lease = next(iter(beta.values()))
    h = auth("admin.alfa")
    claims = client.get(f"/v1/cases/{pago['id']}/claims", headers=h).json()
    facts = client.get(f"/v1/cases/{pago['id']}/facts", headers=h).json()
    docs = client.get(f"/v1/cases/{pago['id']}/documents", headers=h).json()
    speakers = client.get(f"/v1/cases/{pago['id']}/speakers", headers=h).json()
    contradictions = client.get(f"/v1/cases/{pago['id']}/contradictions", headers=h).json()
    return {"pago": pago["id"], "vacio": vacio["id"], "lease": lease["id"], "claims": claims, "facts": facts,
            "docs": {d["filename"]: d for d in docs}, "speakers": {s["label"]: s for s in speakers},
            "contradictions": contradictions}


def _pg(user_env: str, pw_env: str):
    import psycopg
    return psycopg.connect(host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"], dbname=os.environ["POSTGRES_DB"],
                           user=os.environ[user_env], password=os.environ[pw_env], autocommit=False)


@pytest.fixture
def app_db():
    """Conexión directa como rol de la APP (sin BYPASSRLS) — para probar RLS sin pasar por la API."""
    c = _pg("DB_APP_USER", "DB_APP_PASSWORD")
    yield c
    c.rollback()
    c.close()


@pytest.fixture
def owner_db():
    c = _pg("DB_OWNER_USER", "DB_OWNER_PASSWORD")
    yield c
    c.rollback()
    c.close()


@pytest.fixture(scope="session")
def org_ids(client, auth):
    return {k: client.get("/v1/auth/me", headers=auth(u)).json()["organization_id"]
            for k, u in (("alfa", "admin.alfa"), ("beta", "admin.beta"))}


@pytest.fixture
def pdf_bytes():
    from seeds.pdfgen import make_pdf
    import uuid
    return lambda text=None: make_pdf([text or f"Documento de prueba {uuid.uuid4()}"])


@pytest.fixture(scope="session")
def seed_data():
    import yaml
    return yaml.safe_load((ROOT / os.environ["SEED_DATA_FILE"]).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def markers(seed_data):
    return seed_data["test_markers"]


@pytest.fixture(scope="session")
def settings():
    from app.core.config import get_settings
    return get_settings()


@pytest.fixture
def raw_client(app):
    """Cliente que NO relanza excepciones del servidor: permite verificar la respuesta 500 real."""
    from fastapi.testclient import TestClient
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def restore_user(owner_db):
    """Restaura contador de fallos/bloqueo de usuarios modificados por una prueba."""
    touched: list[str] = []
    yield touched.append
    with owner_db.cursor() as cur:
        for email in touched:
            cur.execute("UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE email = %s", (email,))
    owner_db.commit()


def pytest_collection_modifyitems(config, items):
    """Aplica el marker según la carpeta (unit/integration/security/behavior/static) si falta."""
    for item in items:
        folder = Path(str(item.fspath)).parent.name
        if folder in {"unit", "integration", "security", "behavior", "static"} and not item.get_closest_marker(folder):
            item.add_marker(getattr(pytest.mark, folder))
