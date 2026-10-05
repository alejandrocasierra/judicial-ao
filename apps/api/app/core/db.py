"""Acceso a datos. Cada transacción fija app.current_org => RLS obligatorio."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from app.core.config import get_settings


@lru_cache
def engine() -> Engine:
    s = get_settings()
    return create_engine(s.database_url, pool_size=s.DB_POOL_SIZE, pool_pre_ping=True, future=True)


@contextmanager
def tx(org_id: UUID | str | None, actor_id: UUID | str | None = None) -> Iterator[Connection]:
    with engine().begin() as conn:
        conn.execute(text("SELECT set_config('app.current_org', :o, true)"), {"o": str(org_id) if org_id else ""})
        conn.execute(text("SELECT set_config('app.current_actor', :a, true)"), {"a": str(actor_id) if actor_id else ""})
        yield conn


def rows(conn: Connection, sql: str, **params) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def one(conn: Connection, sql: str, **params) -> dict | None:
    r = conn.execute(text(sql), params).first()
    return dict(r._mapping) if r else None
