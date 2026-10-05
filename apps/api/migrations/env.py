import os
import sys
from pathlib import Path
from urllib.parse import quote

from alembic import context
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import envload  # noqa: E402

# Fuente de verdad: el archivo ENV_FILE si existe (host/tests). Dentro de un contenedor
# las variables ya vienen del entorno (docker-compose), así que no se exige el archivo.
_env_file = Path(os.environ["ENV_FILE"]) if os.environ.get("ENV_FILE") else ROOT / ".env"
if not _env_file.is_absolute():
    _env_file = ROOT / _env_file
if _env_file.exists():
    envload.load(str(_env_file), override=True)
host, port, db, user, pw = envload.require("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "DB_OWNER_USER", "DB_OWNER_PASSWORD")
url = f"postgresql+psycopg://{quote(user)}:{quote(pw)}@{host}:{port}/{db}"

engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
