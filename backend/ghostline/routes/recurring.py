"""Recurring buys in practice: list, create, pause or resume, and delete (kept, marked deleted, with its runs)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ghostline.auth import OwnerSub
from ghostline.market.calendar import NEW_YORK, Calendar
from ghostline.recurring import next_run

router = APIRouter()
RUNS_SHOWN = 10


def _json(r: dict) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, dt.date | dt.datetime) else v)
            for k, v in r.items()}


class PlanIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    dollars: Decimal = Field(gt=0, le=1_000_000, decimal_places=2)
    every: str = Field(pattern="^(day|week|two_weeks|month)$")
    weekday: int | None = Field(default=None, ge=0, le=4, description="week, two_weeks: Monday 0 … Friday 4")
    month_day: int | None = Field(default=None, ge=1, le=31, description="month: the day; a short month uses its last")
    starts_on: dt.date | None = None
    note: str | None = Field(default=None, max_length=300)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    def check(self) -> None:
        if self.every in ("week", "two_weeks") and self.weekday is None:
            raise HTTPException(status_code=422, detail="a weekly plan names its weekday (Monday 0 … Friday 4)")
        if self.every == "month" and self.month_day is None:
            raise HTTPException(status_code=422, detail="a monthly plan names its day of the month")
        if self.every not in ("week", "two_weeks") and self.weekday is not None:
            raise HTTPException(status_code=422, detail="only a weekly plan has a weekday")
        if self.every != "month" and self.month_day is not None:
            raise HTTPException(status_code=422, detail="only a monthly plan has a day of the month")


class StatusIn(BaseModel):
    status: str = Field(pattern="^(active|paused)$")


def _plan_out(db: Any, plan: dict, cal: Calendar, today: dt.date) -> dict:
    runs = db.fetch_all("SELECT r.*, o.status AS order_status, o.fill_price, o.quantity FROM recurring_runs r "
                        "LEFT JOIN orders o ON o.id = r.order_id WHERE r.plan_id = %s ORDER BY r.day DESC LIMIT %s",
                        (plan["id"], RUNS_SHOWN))
    nxt = next_run(plan, today, cal) if plan["status"] == "active" else None
    return {**_json(plan), "next_run": nxt.isoformat() if nxt else None, "runs": [_json(r) for r in runs]}


@router.get("/recurring")
def plans(request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    cal, today = Calendar.load(db), dt.datetime.now(NEW_YORK).date()
    rows = db.fetch_all("SELECT * FROM recurring_plans WHERE status <> 'deleted' ORDER BY id")
    return {"plans": [_plan_out(db, p, cal, today) for p in rows]}


@router.post("/recurring", status_code=201)
def create(body: PlanIn, request: Request, sub: str = OwnerSub) -> dict:
    body.check()
    db = request.app.state.db
    today = dt.datetime.now(NEW_YORK).date()
    row = db.fetch_one("INSERT INTO recurring_plans (ticker, dollars, every, weekday, month_day, starts_on, note) "
                       "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                       (body.ticker, body.dollars, body.every, body.weekday, body.month_day,
                        body.starts_on or today, body.note))
    return _plan_out(db, row, Calendar.load(db), today)


@router.patch("/recurring/{plan_id}")
def set_status(plan_id: int, body: StatusIn, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    n = db.execute("UPDATE recurring_plans SET status = %s, updated_at = now() WHERE id = %s AND status <> 'deleted'",
                   (body.status, plan_id))
    if not n:
        raise HTTPException(status_code=404, detail="no such plan")
    row = db.fetch_one("SELECT * FROM recurring_plans WHERE id = %s", (plan_id,))
    return _plan_out(db, row, Calendar.load(db), dt.datetime.now(NEW_YORK).date())


@router.delete("/recurring/{plan_id}")
def delete(plan_id: int, request: Request, sub: str = OwnerSub) -> dict:
    """The plan stops and leaves the list; it and its runs stay on record (nothing is ever deleted)."""
    n = request.app.state.db.execute("UPDATE recurring_plans SET status = 'deleted', updated_at = now() "
                                     "WHERE id = %s AND status <> 'deleted'", (plan_id,))
    if not n:
        raise HTTPException(status_code=404, detail="no such plan")
    return {"id": plan_id, "status": "deleted"}
