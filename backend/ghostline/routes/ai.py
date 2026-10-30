"""The AI trader and its ghost, options practice on any amount, and value over time for any book."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ghostline import series
from ghostline.ai_trader import AITrader, CopyRefused, copy_trade, lane_graduated
from ghostline.auth import OwnerSub

router = APIRouter()


def _row(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, Decimal):
            out[k] = format(v.normalize(), "f") if k == "quantity" else str(v)
        elif isinstance(v, (dt.datetime, dt.date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _trades(db, book: str, limit: int) -> list[dict]:
    rows = db.fetch_all(
        "SELECT t.id, t.ticker, t.asset, t.side, t.quantity, t.fill, t.fees, t.at, t.note, t.option_symbol, t.lane, "
        "t.idea_id, i.module FROM trades t JOIN books b ON b.id = t.book_id LEFT JOIN ideas i ON i.id = t.idea_id "
        "WHERE b.kind = %s ORDER BY t.at DESC, t.id DESC LIMIT %s",
        (book, limit),
    )
    grads = {r["lane"]: r["graduated"] for r in db.fetch_all("SELECT lane, graduated FROM lane_graduation")}
    out = []
    for r in rows:
        row = _row(r)
        row["copy_real_allowed"] = bool(grads.get(r.get("lane")))
        row["copy_real_reason"] = (
            None
            if row["copy_real_allowed"]
            else (f"the {r.get('lane') or 'unknown'} lane has not graduated in practice")
        )
        out.append(row)
    return out


@router.get("/ai-trader")
def ai_trader(request: Request, limit: int = Query(default=50, le=200), sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    ai, ghost = series.equity_series(db, "ai"), series.equity_series(db, "ghost")
    return {
        "state": AITrader(db).state(),
        "trades": _trades(db, "ai", limit),
        "series": {"ai": ai["points"], "ghost": ghost["points"]},
        "twr_pct": {"ai": ai["twr_pct"], "ghost": ghost["twr_pct"]},
        "ghost_trades": _trades(db, "ghost", 20),
        "lanes": [_row(r) for r in db.fetch_all("SELECT * FROM lane_graduation ORDER BY lane")],
    }


@router.post("/ai-trader/run")
def run_now(request: Request, sub: str = OwnerSub) -> dict:
    t = AITrader(request.app.state.db)
    return {"exits": t.run_exits(), "entries": t.run_entries()}


class CopyIn(BaseModel):
    to: str = Field(pattern="^(practice|real)$")


@router.post("/ai-trader/trades/{trade_id}/copy", status_code=201)
def copy(trade_id: int, body: CopyIn, request: Request, sub: str = OwnerSub) -> dict:
    try:
        return copy_trade(request.app.state.db, trade_id, body.to)
    except CopyRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/series")
def book_series(
    request: Request, book: str = Query(pattern="^(real|practice|ai|ghost|options)$"), sub: str = OwnerSub
) -> dict:
    return series.equity_series(request.app.state.db, book)


@router.get("/lanes")
def lanes(request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    return {
        "lanes": [
            {**_row(r), "copy_real_allowed": lane_graduated(db, r["lane"])}
            for r in db.fetch_all("SELECT * FROM lane_graduation ORDER BY lane")
        ]
    }


# ── options practice on any amount ───────────────────────────────────────────────────────────────────────────
@router.get("/options-practice")
def options_practice(request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    t = AITrader(db, book="options")
    s = series.equity_series(db, "options")
    return {"state": t.state(), "trades": _trades(db, "options", 50), "series": s["points"], "twr_pct": s["twr_pct"]}


@router.post("/options-practice/run")
def options_run(request: Request, sub: str = OwnerSub) -> dict:
    t = AITrader(request.app.state.db, book="options")
    return {"exits": t.run_exits(), "entries": t.run_entries()}
