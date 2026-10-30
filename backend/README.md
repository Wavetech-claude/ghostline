# Ghostline backend

FastAPI api (`ghostline.main:app`) and worker (`python -m ghostline.worker`) from one image, Postgres 16, plain-SQL
migrations in `migrations/`.

## Run the tests

A throwaway Postgres on port 5433:

```bash
docker run --rm -d --name ghostline-test-db -e POSTGRES_USER=ghostline -e POSTGRES_PASSWORD=ci \
  -e POSTGRES_DB=ghostline_test -p 5433:5432 postgres:16
cd backend
pip install -e ".[dev]"
ruff check .
pytest -q
```

`GHOSTLINE_TEST_DATABASE_URL` points the tests at another database. Every test resets the public schema and applies
every migration.

## Run the api locally

```bash
cp .env.example .env   # fill DATABASE_URL, GHOSTLINE_SESSION_SECRET, GHOSTLINE_OWNER_APPLE_ID
set -a; . ./.env; set +a
uvicorn ghostline.main:app --reload --port 8000
curl -s localhost:8000/health
```

## Settings

Every setting comes from the environment; `.env.example` lists them. An empty `GHOSTLINE_OWNER_APPLE_ID` admits
nobody. The worker refuses to start with any `ANTHROPIC_*` variable present.

## Migrations

`migrations/NNNN_name.sql`, applied in order, each in its own transaction, recorded in `schema_migrations`. The
worker applies them at start; `/health` answers 503 while one is pending.
