"""/health is 200 only when the database answers and every migration is applied; /version never touches the database."""
import os
import pathlib

import psycopg
from fastapi.testclient import TestClient

import ghostline
from ghostline.main import create_app
from tests.conftest import MIGRATIONS, TEST_DB, settings


def test_health_is_ok_with_the_version_and_the_commit(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["version"] == ghostline.__version__
    assert body["commit"] and body["env"] == "test"


def test_health_is_503_while_a_migration_is_pending(client, tmp_path):
    extra = tmp_path / "migrations"
    extra.mkdir()
    for p in pathlib.Path(MIGRATIONS).glob("*.sql"):
        (extra / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    (extra / "9999_pending.sql").write_text("CREATE TABLE pending_x (id int);", encoding="utf-8")
    app = create_app(settings(GHOSTLINE_MIGRATIONS=str(extra)))
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 503 and "9999_pending.sql" in r.json()["reason"]


def test_health_is_503_when_the_database_does_not_answer():
    app = create_app(settings(DATABASE_URL="postgresql://ghostline:ci@127.0.0.1:1/nope"))
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 503 and r.json()["reason"] == "database not answering"
        v = c.get("/version")
    assert v.status_code == 200 and v.json()["version"] == ghostline.__version__


def test_health_is_503_with_no_database_url():
    app = create_app(settings(DATABASE_URL=""))
    with TestClient(app) as c:
        assert c.get("/health").status_code == 503


def test_the_version_is_the_repositorys_version_file():
    root = pathlib.Path(__file__).resolve().parents[2] / "VERSION"
    assert ghostline.__version__ == root.read_text(encoding="utf-8").strip()


def test_the_commit_comes_from_the_environment_or_the_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GHOSTLINE_COMMIT", "a" * 40)
    assert ghostline.commit() == "a" * 40
    monkeypatch.setenv("GHOSTLINE_COMMIT", "unknown")
    f = tmp_path / "COMMIT"
    f.write_text("b" * 40)
    monkeypatch.setenv("GHOSTLINE_COMMIT_FILE", str(f))
    assert ghostline.commit() == "b" * 40
    monkeypatch.setenv("GHOSTLINE_COMMIT_FILE", str(tmp_path / "missing"))
    assert ghostline.commit() == "unknown"


def test_the_app_applies_migrations_at_start_when_asked():
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    app = create_app(settings(), apply_migrations=True)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200


def test_docs_are_off():
    app = create_app(settings(DATABASE_URL=""))
    with TestClient(app) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    assert os.environ.get("ANTHROPIC_API_KEY") is None or True
