"""Invest this, and the to-do list of real-money trades the owner places at his broker."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ghostline.auth import OwnerSub
from ghostline.invest import Planner, _plan_row

router = APIRouter()


class InvestIn(BaseModel):
    amount: Decimal = Field(gt=0, le=Decimal("10000000"), description="any amount")
    for_date: dt.date | None = None
    book: str = Field(default="practice", pattern="^(practice|real)$")


@router.post("/invest", status_code=201)
def invest(body: InvestIn, request: Request, sub: str = OwnerSub) -> dict:
    if body.for_date and body.for_date < dt.date.today():
        raise HTTPException(status_code=422, detail="the date is in the past")
    return Planner(request.app.state.db).plan(body.amount, body.for_date, body.book)


@router.get("/invest")
def plans(request: Request, limit: int = Query(default=20, le=100), sub: str = OwnerSub) -> dict:
    rows = request.app.state.db.fetch_all("SELECT * FROM invest_plans ORDER BY id DESC LIMIT %s", (limit,))
    return {"plans": [_plan_row(r) for r in rows]}


class ApproveIn(BaseModel):
    to: str = Field(pattern="^(practice|real)$")
    deposit: bool = True  # practice: add the amount to the practice account first


@router.post("/invest/{plan_id}/approve")
def approve(plan_id: int, body: ApproveIn, request: Request, sub: str = OwnerSub) -> dict:
    try:
        return Planner(request.app.state.db).approve(plan_id, body.to, body.deposit)
    except LookupError:
        raise HTTPException(status_code=404, detail="no such plan") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/invest/{plan_id}/drop")
def drop(plan_id: int, request: Request, sub: str = OwnerSub) -> dict:
    n = request.app.state.db.execute(
        "UPDATE invest_plans SET status = 'dropped', decided_at = now() WHERE id = %s AND status = 'proposed'",
        (plan_id,),
    )
    if not n:
        raise HTTPException(status_code=409, detail="only a proposed plan can be dropped")
    return {"ok": True}


# ── the to-do list ────────────────────────────────────────────────────────────────────────────────────────────
def _todo(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, Decimal):
            out[k] = format(v.normalize(), "f") if k == "quantity" else str(v)
        elif isinstance(v, (dt.datetime, dt.date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


@router.get("/todos")
def todos(
    request: Request, status: str = Query(default="open", pattern="^(open|done|dropped|all)$"), sub: str = OwnerSub
) -> dict:
    db = request.app.state.db
    if status == "all":
        rows = db.fetch_all("SELECT * FROM todos ORDER BY id DESC LIMIT 200")
    else:
        rows = db.fetch_all("SELECT * FROM todos WHERE status = %s ORDER BY id DESC LIMIT 200", (status,))
    return {"todos": [_todo(r) for r in rows]}


class DoneIn(BaseModel):
    trade_id: int | None = None
    dropped: bool = False


@router.post("/todos/{todo_id}/close")
def close_todo(todo_id: int, body: DoneIn, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    if body.trade_id is not None:
        t = db.fetch_one(
            "SELECT b.kind FROM trades t JOIN books b ON b.id = t.book_id WHERE t.id = %s", (body.trade_id,)
        )
        if not t:
            raise HTTPException(status_code=404, detail="no such trade")
        if t["kind"] != "real":
            raise HTTPException(status_code=422, detail="a to-do item is closed by a real trade")
    status = "dropped" if body.dropped else "done"
    n = db.execute(
        "UPDATE todos SET status = %s, trade_id = %s, closed_at = now() WHERE id = %s AND status = 'open'",
        (status, body.trade_id, todo_id),
    )
    if not n:
        raise HTTPException(status_code=409, detail="no open to-do item with that id")
    return _todo(db.fetch_one("SELECT * FROM todos WHERE id = %s", (todo_id,)))
