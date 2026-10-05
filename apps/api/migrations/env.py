import sys
from pathlib import Path
from urllib.parse import quote

from alembic import context
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import envload  # noqa: E402

envload.load(override=True)  # el archivo ENV_FILE es la fuente de verdad
host, port, db, user, pw = envload.require("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "DB_OWNER_USER", "DB_OWNER_PASSWORD")
url = f"postgresql+psycopg://{quote(user)}:{quote(pw)}@{host}:{port}/{db}"

engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
