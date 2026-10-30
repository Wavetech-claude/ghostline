"""Ideas by lane, the signals behind them, and a scan the owner can run now."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request

from ghostline.auth import OwnerSub
from ghostline.ideas import Scan

router = APIRouter()


def _row(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, Decimal):
            out[k] = format(v.normalize(), "f") if k == "quantity" else str(v)
        elif isinstance(v, (dt.datetime, dt.date)):
            out[k] = v.isoformat()
        elif k == "evidence" and isinstance(v, str):
            out[k] = json.loads(v)
        else:
            out[k] = v
    return out


# live: the idea's module has graduated from settled paper results; lane_graduated: its lane may be copied for real
_IDEA_SQL = (
    "SELECT i.*, s.score, s.reason, s.horizon_days, s.as_of, s.source, s.direction, "
    "COALESCE(mw.graduated, false) AS live, COALESCE(lg.graduated, false) AS lane_graduated FROM ideas i "
    "JOIN signals s ON s.id = i.signal_id LEFT JOIN module_weights mw ON mw.module = i.module "
    "LEFT JOIN lane_graduation lg ON lg.lane = i.lane"
)


@router.get("/ideas")
def ideas(
    request: Request,
    lane: str | None = Query(default=None, pattern="^(long|swing|puts)$"),
    status: str | None = Query(default=None, pattern="^(proposed|open|refused|expired|settled)$"),
    limit: int = Query(default=100, le=500),
    sub: str = OwnerSub,
) -> dict:
    db = request.app.state.db
    where, params = [], []
    if lane:
        where.append("i.lane = %s")
        params.append(lane)
    if status:
        where.append("i.status = %s")
        params.append(status)
    else:
        where.append("i.status IN ('open', 'refused')")
        where.append("i.expires_at > now()")
    sql = (
        _IDEA_SQL
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY i.status = 'open' DESC, s.score DESC, i.id DESC LIMIT %s"
    )
    rows = db.fetch_all(sql, (*params, limit))
    counts = db.fetch_all(
        "SELECT lane, status, count(*) AS n FROM ideas WHERE expires_at > now() GROUP BY lane, status"
    )
    last = db.fetch_one("SELECT max(day) AS day, count(*) AS n FROM signals WHERE day = (SELECT max(day) FROM signals)")
    return {
        "ideas": [_row(r) for r in rows],
        "counts": [_row(c) for c in counts],
        "last_scan": {
            "day": last["day"].isoformat() if last and last["day"] else None,
            "signals": int(last["n"]) if last else 0,
        },
    }


@router.get("/ideas/{idea_id}")
def idea(idea_id: int, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    row = db.fetch_one(_IDEA_SQL + " WHERE i.id = %s", (idea_id,))
    if not row:
        raise HTTPException(status_code=404, detail="no such idea")
    sig = db.fetch_one("SELECT * FROM signals WHERE id = %s", (row["signal_id"],))
    decision = (
        db.fetch_one("SELECT * FROM risk_decisions WHERE id = %s", (row["risk_decision_id"],))
        if row.get("risk_decision_id")
        else None
    )
    return {**_row(row), "signal": _row(sig) if sig else None, "decision": _row(decision) if decision else None}


@router.get("/signals")
def signals(
    request: Request,
    ticker: str | None = None,
    module: str | None = None,
    day: dt.date | None = None,
    limit: int = Query(default=200, le=1000),
    sub: str = OwnerSub,
) -> dict:
    db = request.app.state.db
    where, params = [], []
    if ticker:
        where.append("ticker = %s")
        params.append(ticker.upper())
    if module:
        where.append("module = %s")
        params.append(module)
    where.append("day = %s" if day else "day = (SELECT max(day) FROM signals)")
    if day:
        params.append(day)
    rows = db.fetch_all(
        "SELECT * FROM signals WHERE " + " AND ".join(where) + " ORDER BY score DESC, id LIMIT %s", (*params, limit)
    )
    return {"signals": [_row(r) for r in rows]}


@router.get("/market/regime")
def market_regime(request: Request, sub: str = OwnerSub) -> dict:
    row = request.app.state.db.fetch_one(
        "SELECT * FROM signals WHERE module = 'macro_regime' ORDER BY day DESC, id DESC LIMIT 1"
    )
    return {"regime": _row(row) if row else None}


@router.post("/ideas/scan")
def scan_now(request: Request, sub: str = OwnerSub) -> dict:
    """Runs the daily scan now, on what is stored. Minutes, not seconds, with a big universe."""
    s = Scan(request.app.state.db)
    out = s.run()
    out["expired"] = s.expire()
    return out
