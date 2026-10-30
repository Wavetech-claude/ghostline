"""Plain-SQL migrations: backend/migrations/NNNN_name.sql, applied in file order, each in its own transaction, recorded
in schema_migrations. /health is 200 only when pending() is empty, so an image whose migrations did not run never
counts as healthy."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import psycopg

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name       text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def files(migrations_dir: str | Path) -> list[Path]:
    """The migration files, in order; a file whose name is not NNNN_name.sql is refused."""
    out = []
    for p in sorted(Path(migrations_dir).glob("*.sql")):
        if not _NAME.match(p.name):
            raise ValueError(f"migration name not NNNN_name.sql: {p.name}")
        out.append(p)
    return out


def applied(conn: psycopg.Connection[Any]) -> set[str]:
    conn.execute(_TABLE)
    conn.commit()
    rows = conn.execute("SELECT name FROM schema_migrations").fetchall()
    return {r["name"] if isinstance(r, dict) else r[0] for r in rows}


def pending(conn: psycopg.Connection[Any], migrations_dir: str | Path) -> list[str]:
    done = applied(conn)
    return [p.name for p in files(migrations_dir) if p.name not in done]


def apply(conn: psycopg.Connection[Any], migrations_dir: str | Path) -> list[str]:
    """Apply every pending migration, in order, one transaction each. Returns the names applied."""
    done = applied(conn)
    out: list[str] = []
    for p in files(migrations_dir):
        if p.name in done:
            continue
        with conn.transaction():
            conn.execute(p.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (p.name,))
        out.append(p.name)
    return out
