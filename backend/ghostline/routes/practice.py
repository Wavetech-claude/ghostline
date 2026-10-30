"""The practice account over the API: order tickets, the account's buying power, the practice settings, option
expirations and chains with the collateral each put needs and whether the book covers it, and options practice."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ghostline import ledger, occ
from ghostline.auth import OwnerSub
from ghostline.market import NotConfigured, RateLimited, dec, store, utcnow
from ghostline.market.limits import Limiter
from ghostline.market.sources import Tradier
from ghostline.practice import Practice

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


class OrderIn(BaseModel):
    book: str = Field(default="practice", pattern="^(practice|ai)$")
    ticker: str = Field(default="", max_length=12)
    asset: str = Field(default="stock", pattern="^(stock|option)$")
    side: str = Field(pattern="^(buy|sell|sell_to_open|buy_to_close)$")
    type: str = Field(default="market", pattern="^(market|limit|stop|stop_limit)$")
    tif: str = Field(default="day", pattern="^(day|gtc)$")
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0, description="the worst case the risk check measures to")
    option_symbol: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("ticker", "option_symbol")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else v

    def check(self) -> None:
        if self.asset == "stock":
            if self.side not in ("buy", "sell"):
                raise HTTPException(status_code=422, detail="a stock order is a buy or a sell")
            if not self.ticker:
                raise HTTPException(status_code=422, detail="a stock order names its ticker")
            if self.option_symbol:
                raise HTTPException(status_code=422, detail="a stock order carries no option symbol")
        else:
            if self.side not in ("sell_to_open", "buy_to_close"):
                raise HTTPException(status_code=422, detail="an option order sells to open or buys to close")
            try:
                occ.parse(self.option_symbol or "")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if self.type in ("limit", "stop_limit") and self.limit_price is None:
            raise HTTPException(status_code=422, detail=f"a {self.type} order needs a limit price")
        if self.type in ("stop", "stop_limit") and self.stop_price is None:
            raise HTTPException(status_code=422, detail=f"a {self.type} order needs a stop price")


@router.post("/practice/orders", status_code=201)
def place_order(body: OrderIn, request: Request, sub: str = OwnerSub) -> dict:
    body.check()
    row = Practice(request.app.state.db).place(body.model_dump(), by=sub)
    return _row(row)


@router.post("/options/practice", status_code=201)
def options_practice(body: OrderIn, request: Request, sub: str = OwnerSub) -> dict:
    """Sell a cash-secured put to open, or buy it back: the same ticket, always an option."""
    if body.asset != "option":
        raise HTTPException(status_code=422, detail="options practice takes an option order")
    return place_order(body, request, sub)


@router.get("/practice/orders")
def list_orders(request: Request, sub: str = OwnerSub, book: str | None = None, status: str | None = None,
                limit: int = Query(default=200, le=1000)) -> dict:
    db = request.app.state.db
    where, params = [], []
    if book:
        where.append("b.kind = %s")
        params.append(book)
    if status:
        where.append("o.status = %s")
        params.append(status)
    sql = ("SELECT o.*, b.kind AS book FROM orders o JOIN books b ON b.id = o.book_id"
           + ((" WHERE " + " AND ".join(where)) if where else "") + " ORDER BY o.id DESC LIMIT %s")
    return {"orders": [_row(r) for r in db.fetch_all(sql, (*params, limit))]}


@router.delete("/practice/orders/{order_id}")
def cancel_order(order_id: int, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    row = db.fetch_one("SELECT status FROM orders WHERE id = %s", (order_id,))
    if not row:
        raise HTTPException(status_code=404, detail="no such order")
    if row["status"] != "open":
        raise HTTPException(status_code=409, detail=f"the order is {row['status']}, not open")
    db.execute("UPDATE orders SET status = 'cancelled', reason = 'cancelled by the owner' WHERE id = %s", (order_id,))
    return _row(db.fetch_one("SELECT o.*, b.kind AS book FROM orders o JOIN books b ON b.id = o.book_id "
                             "WHERE o.id = %s", (order_id,)))


@router.get("/practice/account")
def practice_account(request: Request, book: str = Query(default="practice", pattern="^(practice|ai)$"),
                     sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    p = Practice(db)
    bid = p.book_id(book)
    f = p.facts(bid)
    open_orders = db.fetch_all("SELECT * FROM orders WHERE book_id = %s AND status = 'open' ORDER BY id DESC", (bid,))
    legs = []
    for symbol, n in f.short_legs.items():
        try:
            o = occ.parse(symbol)
            legs.append({"option_symbol": symbol, "underlying": o.underlying, "expiry": o.expiry.isoformat(),
                         "strike": str(o.strike), "contracts": format(n.normalize(), "f"),
                         "collateral": str(ledger.money(o.collateral_per_contract * n))})
        except ValueError:
            legs.append({"option_symbol": symbol, "contracts": format(n.normalize(), "f")})
    pending = db.fetch_all("SELECT s.trade_id, s.amount, s.settles_at FROM settlements s JOIN trades t "
                           "ON t.id = s.trade_id WHERE t.book_id = %s AND NOT s.settled ORDER BY s.settles_at", (bid,))
    return {"book": book, "cash": str(f.cash), "unsettled": str(f.unsettled),
            "locked_collateral": str(f.locked_collateral), "available": str(f.available), "equity": str(f.equity),
            "open_positions": f.open_positions, "paper_drawdown_share": str(f.paper_drawdown_share),
            "fee_per_order": str(ledger.money(p.fee())), "short_puts": legs,
            "open_orders": [_row(r) for r in open_orders], "pending_settlements": [_row(r) for r in pending]}


class SettingsIn(BaseModel):
    fee_per_order: Decimal = Field(ge=0)


@router.put("/practice/settings")
def put_settings(body: SettingsIn, request: Request, sub: str = OwnerSub) -> dict:
    request.app.state.db.execute("INSERT INTO practice_settings (key, value, updated_at) VALUES "
                                 "('fee_per_order', %s, now()) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                                 "updated_at = now()", (body.fee_per_order,))
    return {"fee_per_order": str(ledger.money(body.fee_per_order))}


# ── option chains (Tradier, counted) ───────────────────────────────────────────────────────────────────────
def _tradier(request: Request) -> Tradier:
    made = getattr(request.app.state, "tradier", None)
    return made or Tradier()


def _counted(db: Any, endpoint: str, fn, *args) -> tuple[Any, str | None]:
    lim = Limiter(db)
    try:
        lim.check("tradier")
        result = fn(*args)
        lim.record("tradier", endpoint, "ok", rows=len(result) if hasattr(result, "__len__") else 1)
        return result, None
    except NotConfigured as exc:
        lim.record("tradier", endpoint, "skipped", str(exc))
        return None, "Tradier is not configured: add its key and the chains appear"
    except RateLimited as exc:
        lim.record("tradier", endpoint, "skipped", str(exc))
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 — the answer says what failed; nothing is guessed
        lim.record("tradier", endpoint, "error", f"{type(exc).__name__}: {exc}")
        return None, f"Tradier did not answer: {type(exc).__name__}"


@router.get("/options/expirations")
def expirations(request: Request, ticker: str, sub: str = OwnerSub) -> dict:
    t = ticker.strip().upper()
    dates, why = _counted(request.app.state.db, "expirations", _tradier(request).expirations, t)
    return {"ticker": t, "expirations": [d.isoformat() for d in dates or []], "configured": why is None, "reason": why}


@router.get("/options/chain")
def chain(request: Request, ticker: str, expiry: dt.date,
          book: str = Query(default="practice", pattern="^(practice|ai)$"), sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    t = ticker.strip().upper()
    raw, why = _counted(db, "chain", _tradier(request).chain, t, expiry)
    facts = Practice(db).facts(Practice(db).book_id(book))
    rows: list[dict] = []
    as_of = utcnow()
    for o in raw or []:
        symbol = str(o.get("symbol") or "")
        try:
            parsed = occ.parse(symbol)
        except ValueError:
            continue
        greeks = o.get("greeks") or {}
        row = {"option_symbol": parsed.symbol, "underlying": parsed.underlying, "expiry": parsed.expiry,
               "strike": parsed.strike, "kind": parsed.kind, "bid": dec(o.get("bid")), "ask": dec(o.get("ask")),
               "last": dec(o.get("last")),
               "volume": dec(o.get("volume")), "open_interest": dec(o.get("open_interest")),
               "iv": dec(greeks.get("mid_iv") or greeks.get("smv_vol")), "delta": dec(greeks.get("delta"))}
        rows.append(row)
    if rows:
        with db.connection() as conn:
            for r in rows:
                conn.execute("INSERT INTO option_chains (option_symbol, underlying, expiry, strike, kind, bid, ask, "
                             "last, volume, open_interest, iv, delta, source, as_of) VALUES (%s, %s, %s, %s, %s, %s, "
                             "%s, %s, %s, %s, %s, %s, 'tradier', %s) ON CONFLICT DO NOTHING",
                             (r["option_symbol"], r["underlying"], r["expiry"], r["strike"], r["kind"], r["bid"],
                              r["ask"], r["last"], r["volume"], r["open_interest"], r["iv"], r["delta"], as_of))
            conn.commit()
        # the chain's bid and ask double as the option's quote, so a practice order can fill against it
        quotes = [store.Quote(r["option_symbol"], r["bid"], r["ask"], r["last"], as_of, True) for r in rows]
        store.save_quotes(db, quotes, "tradier", as_of)
    out = []
    for r in sorted(rows, key=lambda x: (x["kind"], x["strike"])):
        collateral = r["strike"] * 100 if r["kind"] == "put" else None
        out.append({**_row(r), "collateral": str(collateral) if collateral is not None else None,
                    "covered": bool(collateral is not None and collateral <= facts.available)})
    return {"ticker": t, "expiry": expiry.isoformat(), "configured": why is None, "reason": why, "source": "tradier",
            "as_of": as_of.isoformat() if rows else None, "available": str(facts.available), "book": book,
            "options": out}
