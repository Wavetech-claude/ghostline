"""Price alerts and monthly statements."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from ghostline import statements
from ghostline.auth import OwnerSub

router = APIRouter()


def _row(r: dict) -> dict:
    return {
        k: (str(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else v)
        for k, v in r.items()
    }


class AlertIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    kind: str = Field(pattern="^(above|below|pct_move|ma_cross|earnings)$")
    value: Decimal = Field(gt=0, description="a price, a percent, a moving-average period, or days")
    note: str | None = Field(default=None, max_length=300)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    def check(self) -> None:
        if self.kind == "ma_cross" and (self.value != self.value.to_integral() or not 2 <= self.value <= 250):
            raise HTTPException(status_code=422, detail="a moving-average period is a whole number from 2 to 250")
        if self.kind == "earnings" and (self.value != self.value.to_integral() or self.value > 60):
            raise HTTPException(status_code=422, detail="earnings alerts take a whole number of days up to 60")
        if self.kind == "pct_move" and self.value > 100:
            raise HTTPException(status_code=422, detail="a percent move is at most 100")


@router.get("/alerts")
def alerts(request: Request, ticker: str | None = None, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    if ticker:
        rows = db.fetch_all("SELECT * FROM alerts WHERE ticker = %s ORDER BY id DESC", (ticker.strip().upper(),))
    else:
        rows = db.fetch_all("SELECT * FROM alerts ORDER BY status = 'active' DESC, id DESC LIMIT 200")
    return {"alerts": [_row(r) for r in rows]}


@router.post("/alerts", status_code=201)
def add_alert(body: AlertIn, request: Request, sub: str = OwnerSub) -> dict:
    body.check()
    row = request.app.state.db.fetch_one(
        "INSERT INTO alerts (ticker, kind, value, note) VALUES (%s, %s, %s, %s) RETURNING *",
        (body.ticker, body.kind, body.value, body.note),
    )
    with request.app.state.db.connection() as conn:
        conn.commit()
    return _row(row)


class AlertState(BaseModel):
    active: bool


@router.put("/alerts/{alert_id}")
def set_alert(alert_id: int, body: AlertState, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    status = "active" if body.active else "off"
    n = db.execute(
        "UPDATE alerts SET status = %s, triggered_at = CASE WHEN %s = 'active' THEN NULL ELSE triggered_at "
        "END WHERE id = %s",
        (status, status, alert_id),
    )
    if not n:
        raise HTTPException(status_code=404, detail="no such alert")
    return _row(db.fetch_one("SELECT * FROM alerts WHERE id = %s", (alert_id,)))


@router.delete("/alerts/{alert_id}")
def delete_alert(alert_id: int, request: Request, sub: str = OwnerSub) -> dict:
    if not request.app.state.db.execute("DELETE FROM alerts WHERE id = %s", (alert_id,)):
        raise HTTPException(status_code=404, detail="no such alert")
    return {"ok": True}


# ── statements ───────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/statements")
def statement(
    request: Request,
    book: str = Query(pattern="^(real|practice|ai|ghost|options)$"),
    month: str = Query(description="YYYY-MM"),
    format: str = Query(default="json", pattern="^(json|csv|pdf)$"),
    sub: str = OwnerSub,
):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(status_code=422, detail="month is YYYY-MM")
    s = statements.build(request.app.state.db, book, month)
    name = f"ghostline-{book}-{month}"
    if format == "csv":
        return Response(
            statements.to_csv(s),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )
    if format == "pdf":
        return Response(
            statements.to_pdf(s),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'},
        )
    return s
