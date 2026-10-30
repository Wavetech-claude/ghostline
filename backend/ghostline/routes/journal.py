"""The journal: settled paper results, the modules' weights and graduation, the lanes, the lessons, the weekly test set
and the weekly digest; the learning settings the owner sets."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ghostline.auth import OwnerSub
from ghostline.claude.jobs import enqueue
from ghostline.learning import Learning, settings

router = APIRouter()


def _row(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, (dt.datetime, dt.date)):
            out[k] = v.isoformat()
        elif k in ("results", "facts") and isinstance(v, str):
            out[k] = json.loads(v)
        else:
            out[k] = v
    return out


@router.get("/journal")
def journal(request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    counts = {
        r["status"]: int(r["n"])
        for r in db.fetch_all("SELECT status, count(*) AS n FROM paper_results GROUP BY status")
    }
    ev = db.fetch_one("SELECT id, questions, correct, score_pct, at FROM eval_runs ORDER BY id DESC LIMIT 1")
    digest = db.fetch_one(
        "SELECT id, week_of, content, pushed_at, created_at FROM weekly_digests ORDER BY week_of DESC LIMIT 1"
    )
    return {
        "counts": {
            "pending": counts.get("pending", 0),
            "settled": counts.get("settled", 0),
            "unpriced": counts.get("unpriced", 0),
        },
        "settings": {k: str(v) for k, v in settings(db).items()},
        "modules": [_row(r) for r in db.fetch_all("SELECT * FROM module_weights ORDER BY module")],
        "lanes": [_row(r) for r in db.fetch_all("SELECT * FROM lane_graduation ORDER BY lane")],
        "recent": [
            _row(r)
            for r in db.fetch_all(
                "SELECT * FROM paper_results WHERE status = 'settled' ORDER BY settled_at DESC LIMIT 20"
            )
        ],
        "last_eval": _row(ev) if ev else None,
        "digest": _row(digest) if digest else None,
    }


@router.get("/journal/results")
def results(
    request: Request,
    status: str | None = Query(default=None, pattern="^(pending|settled|unpriced)$"),
    module: str | None = None,
    lane: str | None = Query(default=None, pattern="^(long|swing|puts)$"),
    limit: int = Query(default=100, le=500),
    sub: str = OwnerSub,
) -> dict:
    where, params = [], []
    for clause, v in (("status = %s", status), ("module = %s", module), ("lane = %s", lane)):
        if v:
            where.append(clause)
            params.append(v)
    sql = (
        "SELECT * FROM paper_results"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY COALESCE(settled_at, created_at) DESC LIMIT %s"
    )
    return {"results": [_row(r) for r in request.app.state.db.fetch_all(sql, (*params, limit))]}


@router.get("/lessons")
def lessons(
    request: Request, module: str | None = None, limit: int = Query(default=50, le=200), sub: str = OwnerSub
) -> dict:
    db = request.app.state.db
    if module:
        rows = db.fetch_all("SELECT * FROM lessons WHERE module = %s ORDER BY id DESC LIMIT %s", (module, limit))
    else:
        rows = db.fetch_all("SELECT * FROM lessons ORDER BY id DESC LIMIT %s", (limit,))
    return {"lessons": [_row(r) for r in rows]}


@router.post("/journal/run")
def run(request: Request, sub: str = OwnerSub) -> dict:
    out = Learning(request.app.state.db).run()
    return json.loads(json.dumps(out, default=str))


@router.post("/journal/eval", status_code=202)
def run_eval(request: Request, sub: str = OwnerSub) -> dict:
    return {"job_id": enqueue(request.app.state.db, "eval", {})}


@router.get("/journal/evals")
def evals(request: Request, limit: int = Query(default=12, le=52), sub: str = OwnerSub) -> dict:
    rows = request.app.state.db.fetch_all("SELECT * FROM eval_runs ORDER BY id DESC LIMIT %s", (limit,))
    return {"evals": [_row(r) for r in rows]}


class LearningSettingsIn(BaseModel):
    graduation_n: int | None = Field(default=None, ge=5, le=1000)
    cost_bps_per_side: Decimal | None = Field(default=None, ge=0, le=500)
    weight_full_n: int | None = Field(default=None, ge=1, le=1000)


@router.put("/learning/settings")
def put_settings(body: LearningSettingsIn, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    changed = {k: v for k, v in body.model_dump().items() if v is not None}
    if not changed:
        raise HTTPException(status_code=422, detail="nothing to change")
    for k, v in changed.items():
        db.execute(
            "INSERT INTO learning_settings (key, value, updated_at) VALUES (%s, %s, now()) ON CONFLICT (key) "
            "DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (k, v),
        )
    # a new bar for graduation applies at once, from the same settled results
    learning = Learning(db)
    learning.update_weights()
    learning.update_lanes()
    return {k: str(v) for k, v in settings(db).items()}
