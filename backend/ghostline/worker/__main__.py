"""The worker: the same image as the api, its own start command. It applies the migrations first, runs the schedules
(none yet in the skeleton), and answers /health on port 8000 for Coolify and the watch.

It refuses to start with any ANTHROPIC_* variable in its environment: Claude runs here as headless `claude -p` on the
subscription token (CLAUDE_CODE_OAUTH_TOKEN) only, as the auto-fixer does."""
from __future__ import annotations

import logging
import os
import sys

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import JSONResponse

import ghostline
from ghostline import migrate
from ghostline.config import Settings, load
from ghostline.db import Database
from ghostline.worker import schedule

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ghostline.worker")


def refuse_anthropic_keys(environ: dict[str, str]) -> list[str]:
    """The names of ANTHROPIC_* variables present: any one is a refusal to start."""
    return sorted(k for k in environ if k.upper().startswith("ANTHROPIC_"))


def health_app(settings: Settings, db: Database, scheduler: BackgroundScheduler) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        body = {"status": "ok", "role": "worker", "version": ghostline.__version__, "commit": ghostline.commit(),
                "env": settings.ghostline_env, "jobs": len(scheduler.get_jobs())}
        if not db.ping():
            return JSONResponse({**body, "status": "unhealthy", "reason": "database not answering"}, status_code=503)
        with db.connection() as conn:
            left = migrate.pending(conn, settings.migrations_dir)
        if left:
            return JSONResponse({**body, "status": "unhealthy", "reason": f"migrations pending: {', '.join(left)}"},
                                status_code=503)
        if not scheduler.running:
            return JSONResponse({**body, "status": "unhealthy", "reason": "scheduler stopped"}, status_code=503)
        return body

    @app.get("/version")
    def version():
        return {"version": ghostline.__version__, "commit": ghostline.commit(), "role": "worker"}

    return app


def main() -> int:
    bad = refuse_anthropic_keys(dict(os.environ))
    if bad:
        logger.error("refusing to start: %s present; the worker runs Claude on the subscription token only",
                     ", ".join(bad))
        return 2
    settings = load()
    if not settings.database_url:
        logger.error("DATABASE_URL is not set")
        return 2
    db = Database(settings, max_size=min(settings.db_pool_max, 2))
    db.open()
    with db.connection() as conn:
        done = migrate.apply(conn, settings.migrations_dir)
    logger.info("migrations applied: %s", ", ".join(done) or "none pending")
    scheduler = BackgroundScheduler(timezone="America/Los_Angeles")
    registered = schedule.register(scheduler, db)
    scheduler.start()
    logger.info("jobs registered: %s", ", ".join(registered))
    logger.info("worker up: env=%s version=%s commit=%s jobs=%d", settings.ghostline_env, ghostline.__version__,
                ghostline.commit(), len(scheduler.get_jobs()))
    try:
        uvicorn.run(health_app(settings, db, scheduler), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                    log_level="warning")
    finally:
        scheduler.shutdown(wait=False)
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
