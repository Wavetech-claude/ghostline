"""The connection pool. Its size is per process: the role `ghostline` is capped at CONNECTION LIMIT 10 on the
homelab, shared by api (3) and worker (2) in staging and in production (3 + 2 + 3 + 2 = 10). Raising a pool means
lowering another; tests/test_pool_budget.py pins the arithmetic."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ghostline.config import Settings

# the budget the homelab role allows (CONNECTION LIMIT 10), split by process kind and environment
ROLE_CONNECTION_LIMIT = 10
POOL_BUDGET = {"api": 3, "worker": 2}
ENVIRONMENTS = ("staging", "prod")


class Database:
    def __init__(self, settings: Settings, *, max_size: int | None = None) -> None:
        self.settings = settings
        self.max_size = max_size or settings.db_pool_max
        self._pool: ConnectionPool | None = None

    def open(self) -> None:
        if self._pool is None:
            self._pool = ConnectionPool(self.settings.database_url, min_size=1, max_size=self.max_size, open=True,
                                        timeout=10, kwargs={"row_factory": dict_row, "connect_timeout": 5})

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        if self._pool is None:
            self.open()
        assert self._pool is not None
        with self._pool.connection() as conn:
            yield conn

    def fetch_one(self, sql: str, params: Any = None) -> dict | None:
        with self.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def fetch_all(self, sql: str, params: Any = None) -> list[dict]:
        with self.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: Any = None) -> int:
        with self.connection() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount

    def ping(self) -> bool:
        try:
            with self.connection() as conn:
                return conn.execute("SELECT 1").fetchone() is not None
        except Exception:  # noqa: BLE001 — a database that cannot answer is "not answering", whatever the reason
            return False
