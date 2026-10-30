"""The API. /health answers 200 only when the database answers and every migration is applied (503 otherwise), and
names the running version and commit; /version names them without touching the database. Every other route except
/auth/apple needs the owner's session."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import ghostline
from ghostline import migrate, storage
from ghostline.auth import Auth
from ghostline.config import Settings, load
from ghostline.db import Database
from ghostline.routes import ai as ai_routes
from ghostline.routes import alerts as alerts_routes
from ghostline.routes import attachments as attachments_routes
from ghostline.routes import auth as auth_routes
from ghostline.routes import claude as claude_routes
from ghostline.routes import devices as devices_routes
from ghostline.routes import ideas as ideas_routes
from ghostline.routes import invest as invest_routes
from ghostline.routes import ledger as ledger_routes
from ghostline.routes import market as market_routes
from ghostline.routes import practice as practice_routes
from ghostline.routes import risk as risk_routes

logger = logging.getLogger("ghostline")


def create_app(settings: Settings | None = None, *, apply_migrations: bool = False) -> FastAPI:
    settings = settings or load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.db = Database(settings)
        app.state.auth = Auth(settings)
        app.state.storage = storage.for_settings(settings)
        if settings.database_url:
            try:
                app.state.db.open()
                if apply_migrations:
                    with app.state.db.connection() as conn:
                        done = migrate.apply(conn, settings.migrations_dir)
                        if done:
                            logger.info("migrations applied: %s", ", ".join(done))
            except Exception as exc:  # noqa: BLE001 — the api starts and /health says what is wrong
                logger.warning("database not ready at start: %s", exc)
        try:
            yield
        finally:
            app.state.db.close()

    app = FastAPI(title="Ghostline", version=ghostline.__version__, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.include_router(auth_routes.router)
    app.include_router(devices_routes.router)
    app.include_router(ledger_routes.router)
    app.include_router(risk_routes.router)
    app.include_router(attachments_routes.router)
    app.include_router(market_routes.router)
    app.include_router(practice_routes.router)
    app.include_router(ideas_routes.router)
    app.include_router(claude_routes.router)
    app.include_router(invest_routes.router)
    app.include_router(ai_routes.router)
    app.include_router(alerts_routes.router)

    @app.get("/version")
    def version() -> dict:
        return {"version": ghostline.__version__, "commit": ghostline.commit(), "env": settings.ghostline_env}

    @app.get("/health")
    def health():
        body = {"status": "ok", "version": ghostline.__version__, "commit": ghostline.commit(),
                "env": settings.ghostline_env}
        db: Database = app.state.db
        if not settings.database_url or not db.ping():
            return JSONResponse({**body, "status": "unhealthy", "reason": "database not answering"}, status_code=503)
        try:
            with db.connection() as conn:
                left = migrate.pending(conn, settings.migrations_dir)
        except Exception as exc:  # noqa: BLE001
            reason = f"migrations unreadable: {type(exc).__name__}"
            return JSONResponse({**body, "status": "unhealthy", "reason": reason}, status_code=503)
        if left:
            return JSONResponse({**body, "status": "unhealthy", "reason": f"migrations pending: {', '.join(left)}"},
                                status_code=503)
        return body

    return app


app = create_app(apply_migrations=True)
