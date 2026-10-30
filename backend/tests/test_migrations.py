"""Migrations apply in order, once, and a wrongly named file is refused; the worker refuses ANTHROPIC_* keys; the pool
budget fits the role's connection limit."""
import psycopg
import pytest

from ghostline import db as dbmod
from ghostline import migrate
from ghostline.worker.__main__ import refuse_anthropic_keys
from tests.conftest import MIGRATIONS, TEST_DB


def test_migrations_apply_once_and_in_order(fresh_db):
    with psycopg.connect(TEST_DB) as conn:
        assert migrate.pending(conn, MIGRATIONS) == []
        assert migrate.apply(conn, MIGRATIONS) == []
        names = [p.name for p in migrate.files(MIGRATIONS)]
        assert names == sorted(names) and names[0] == "0001_init.sql"
        applied = conn.execute("SELECT name FROM schema_migrations ORDER BY applied_at, name").fetchall()
        assert [r[0] for r in applied] == names


def test_a_badly_named_migration_is_refused(tmp_path):
    (tmp_path / "init.sql").write_text("select 1;", encoding="utf-8")
    with pytest.raises(ValueError, match="NNNN_name.sql"):
        migrate.files(tmp_path)


def test_a_failing_migration_leaves_nothing_half_applied(fresh_db, tmp_path):
    (tmp_path / "0007_ok.sql").write_text("CREATE TABLE t_ok (id int);", encoding="utf-8")
    (tmp_path / "0008_bad.sql").write_text("CREATE TABLE t_bad (id int); SELECT nope_function();", encoding="utf-8")
    with psycopg.connect(TEST_DB) as conn:
        with pytest.raises(psycopg.Error):
            migrate.apply(conn, tmp_path)
    with psycopg.connect(TEST_DB) as conn:
        assert migrate.pending(conn, tmp_path) == ["0008_bad.sql"]
        tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()}
        assert "t_ok" in tables and "t_bad" not in tables


def test_the_worker_refuses_anthropic_keys():
    assert refuse_anthropic_keys({"ANTHROPIC_API_KEY": "x", "CLAUDE_CODE_OAUTH_TOKEN": "y"}) == ["ANTHROPIC_API_KEY"]
    assert refuse_anthropic_keys({"anthropic_base_url": "x"}) == ["anthropic_base_url"]
    assert refuse_anthropic_keys({"CLAUDE_CODE_OAUTH_TOKEN": "y", "PATH": "/bin"}) == []


def test_the_pool_budget_fits_the_roles_connection_limit():
    per_env = sum(dbmod.POOL_BUDGET.values())
    assert per_env * len(dbmod.ENVIRONMENTS) <= dbmod.ROLE_CONNECTION_LIMIT
    assert dbmod.POOL_BUDGET == {"api": 3, "worker": 2}
