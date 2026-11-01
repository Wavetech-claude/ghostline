"""A throwaway Postgres (GHOSTLINE_TEST_DATABASE_URL, default a local one on port 5433) is reset before every test:
the public schema is dropped and every migration applied. The app under test uses the test signer, never Apple."""
from __future__ import annotations

import datetime as dt
import os
import time

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from ghostline import migrate
from ghostline.config import Settings
from ghostline.main import create_app

TEST_DB = os.environ.get("GHOSTLINE_TEST_DATABASE_URL", "postgresql://ghostline:ci@localhost:5433/ghostline_test")
SIGNER = "test-signer-key-not-a-secret-0123456789abcdef"
SESSION_SECRET = "session-secret-for-tests-0123456789abcdef"
OTHER_KEY = "another-key-0123456789abcdef0123456789"
OWNER_EMAIL = "qa+owner@example.com"
OWNER_SUB = "001234.qaowner.0001"
AUDIENCE = "com.wavetech.ghostline"
MIGRATIONS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")


def settings(**over) -> Settings:
    base = {"DATABASE_URL": TEST_DB, "GHOSTLINE_DB_POOL_MAX": "2",
            "GHOSTLINE_SESSION_SECRET": SESSION_SECRET,
            "GHOSTLINE_OWNER_APPLE_ID": OWNER_EMAIL, "GHOSTLINE_TEST_SIGNER_KEY": SIGNER, "GHOSTLINE_ENV": "test",
            "GHOSTLINE_WORKER_TOKEN": "worker-token-for-tests", "GHOSTLINE_MIGRATIONS": MIGRATIONS,
            "GHOSTLINE_APPLE_AUDIENCE": AUDIENCE}
    base.update(over)
    return Settings(**base)


@pytest.fixture
def fresh_db():
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    with psycopg.connect(TEST_DB) as conn:
        migrate.apply(conn, MIGRATIONS)
    return TEST_DB


@pytest.fixture
def client(fresh_db):
    app = create_app(settings())
    with TestClient(app) as c:
        yield c


def identity_token(sub: str = OWNER_SUB, email: str | None = OWNER_EMAIL, *, exp_in: int = 600, key: str = SIGNER,
                   audience: str = AUDIENCE) -> str:
    now = int(time.time())
    claims = {"iss": "ghostline-test", "aud": audience, "sub": sub, "iat": now, "exp": now + exp_in}
    if email:
        claims["email"] = email
    return jwt.encode(claims, key, algorithm="HS256")


def sign_in(client: TestClient, **kw) -> str:
    r = client.post("/auth/apple", json={"identity_token": identity_token(**kw), "platform": "ios"})
    assert r.status_code == 200, r.text
    return r.json()["session_token"]


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
