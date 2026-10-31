"""Reconciliation routes: the month's report, the owner's "no" to a suggestion, and broker statements
(ghostline.reconcile holds the logic)."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ghostline import reconcile
from ghostline.auth import OwnerSub

router = APIRouter()


@router.get("/reconciliation")
def reconciliation(request: Request, month: str | None = None, sub: str = OwnerSub) -> dict:
    try:
        return reconcile.report(request.app.state.db, month)
    except ValueError:
        raise HTTPException(status_code=422, detail="month is YYYY-MM") from None


class DecisionIn(BaseModel):
    kind: str = Field(pattern="^(idea|memo)$")
    ref_id: int
    reason: str | None = Field(default=None, max_length=500)


@router.post("/decisions", status_code=201)
def decline(request: Request, body: DecisionIn, sub: str = OwnerSub) -> dict:
    """The owner says no to an idea or a memo's buy call, with an optional reason; saying it again replaces it."""
    db = request.app.state.db
    table = "ideas" if body.kind == "idea" else "memos"
    if not db.fetch_one(f"SELECT id FROM {table} WHERE id = %s", (body.ref_id,)):
        raise HTTPException(status_code=404, detail=f"no {body.kind} {body.ref_id}")
    reason = (body.reason or "").strip() or None
    db.execute("INSERT INTO suggestion_decisions (kind, ref_id, reason) VALUES (%s, %s, %s) ON CONFLICT (kind, ref_id) "
               "DO UPDATE SET reason = EXCLUDED.reason, decided_at = now()", (body.kind, body.ref_id, reason))
    return {"kind": body.kind, "ref_id": body.ref_id, "outcome": "declined", "reason": reason}


@router.delete("/decisions/{kind}/{ref_id}")
def undo(request: Request, kind: str, ref_id: int, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    if not db.execute("DELETE FROM suggestion_decisions WHERE kind = %s AND ref_id = %s", (kind, ref_id)):
        raise HTTPException(status_code=404, detail="no such decision")
    return {"kind": kind, "ref_id": ref_id, "undone": True}


class Holding(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    quantity: Decimal = Field(ge=0)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


class StatementIn(BaseModel):
    as_of: dt.date
    cash: Decimal
    positions: list[Holding] = Field(default_factory=list, max_length=300)
    note: str | None = Field(default=None, max_length=500)


@router.post("/broker-statements", status_code=201)
def add_statement(request: Request, body: StatementIn, sub: str = OwnerSub) -> dict:
    """A statement from the broker, typed in by the owner; the answer is its comparison with the real book."""
    if body.as_of > dt.date.today() + dt.timedelta(days=1):
        raise HTTPException(status_code=422, detail="a statement cannot be dated in the future")
    db = request.app.state.db
    positions = [{"ticker": h.ticker, "quantity": str(h.quantity)} for h in body.positions]
    with db.connection() as conn:
        row = conn.execute("INSERT INTO broker_statements (as_of, cash, positions, note) "
                           "VALUES (%s, %s, %s::jsonb, %s) RETURNING *",
                           (body.as_of, body.cash, json.dumps(positions), body.note)).fetchone()
        conn.commit()
    return reconcile.compare(db, row)
