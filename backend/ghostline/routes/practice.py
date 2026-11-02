"""The practice account over the API: order tickets, the account's buying power, the practice settings, option
expirations and chains with the collateral each put needs and whether the book covers it, and options practice."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ghostline import corrections, ledger, occ
from ghostline.auth import OwnerSub
from ghostline.ghost import Ghost
from ghostline.market import NotConfigured, RateLimited, greeks, store, utcnow
from ghostline.market.limits import Limiter
from ghostline.market.sources import Alpaca, Tradier, configured
from ghostline.practice import Practice, ReplaceRefused

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
    book: str = Field(default="practice", pattern="^(practice|ai|options)$")
    ticker: str = Field(default="", max_length=12)
    asset: str = Field(default="stock", pattern="^(stock|option)$")
    side: str = Field(pattern="^(buy|sell|sell_to_open|buy_to_close)$")
    type: str = Field(default="market", pattern="^(market|limit|stop|stop_limit|trailing_stop)$")
    tif: str = Field(default="day", pattern="^(day|gtc)$")
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0, description="the worst case the risk check measures to")
    option_symbol: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=500)
    trail_amount: Decimal | None = Field(default=None, gt=0, description="trailing stop: the gap in dollars")
    trail_percent: Decimal | None = Field(default=None, gt=0, lt=100, description="trailing stop: the gap in percent")
    take_profit: Decimal | None = Field(default=None, gt=0, description="bracket: the exit's limit price")
    bracket_stop: Decimal | None = Field(default=None, gt=0, description="bracket: the exit's stop price")

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
        if self.type == "trailing_stop":
            if self.asset != "stock":
                raise HTTPException(status_code=422, detail="a trailing stop is a stock order")
            if (self.trail_amount is None) == (self.trail_percent is None):
                raise HTTPException(status_code=422, detail="a trailing stop has one trail: an amount or a percent")
            if self.stop_price is not None or self.limit_price is not None:
                raise HTTPException(status_code=422, detail="a trailing stop sets its own stop from the trail")
        elif self.trail_amount is not None or self.trail_percent is not None:
            raise HTTPException(status_code=422, detail="only a trailing stop has a trail")
        if (self.take_profit is None) != (self.bracket_stop is None):
            raise HTTPException(status_code=422, detail="a bracket has both a take-profit and a stop")
        if self.take_profit is not None:
            if self.asset != "stock" or self.side != "buy" or self.type not in ("market", "limit"):
                raise HTTPException(status_code=422, detail="a bracket is a stock buy, market or limit")
            if self.take_profit <= self.bracket_stop:
                raise HTTPException(status_code=422, detail="the take-profit must be above the stop")
            if self.limit_price is not None and not self.bracket_stop < self.limit_price < self.take_profit:
                raise HTTPException(status_code=422,
                                    detail="a bracket's limit sits between its stop and its take-profit")


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
    try:
        Practice(db).cancel(order_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="no such order") from exc
    except ReplaceRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _row(db.fetch_one("SELECT o.*, b.kind AS book FROM orders o JOIN books b ON b.id = o.book_id "
                             "WHERE o.id = %s", (order_id,)))


class ReplaceIn(BaseModel):
    """What a replacement may change; the order type, side, ticker and book stay (Robinhood's rule)."""
    quantity: Decimal | None = Field(default=None, gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)
    tif: str | None = Field(default=None, pattern="^(day|gtc)$")
    trail_amount: Decimal | None = Field(default=None, gt=0)
    trail_percent: Decimal | None = Field(default=None, gt=0, lt=100)
    take_profit: Decimal | None = Field(default=None, gt=0)
    bracket_stop: Decimal | None = Field(default=None, gt=0)


@router.post("/practice/orders/{order_id}/replace", status_code=201)
def replace_order(order_id: int, body: ReplaceIn, request: Request, sub: str = OwnerSub) -> dict:
    """Replace an open limit, stop or stop-limit order: the new order passes every check, then the old one ends
    'replaced' in the same transaction. Refused (409) = the old order stays open, unchanged."""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="nothing to change")
    db = request.app.state.db
    try:
        new = Practice(db).replace(order_id, changes)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReplaceRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _row(db.fetch_one("SELECT o.*, b.kind AS book FROM orders o JOIN books b ON b.id = o.book_id "
                             "WHERE o.id = %s", (new["id"],)))


class ResetIn(BaseModel):
    book: str = Field(pattern="^(practice|ai|options)$")
    deposit: Decimal | None = Field(default=None, gt=0, description="a fresh starting deposit, optional")


@router.post("/practice/reset")
def reset_book(body: ResetIn, request: Request, sub: str = OwnerSub) -> dict:
    """Start a practice, AI or options book over, as a paper account is reset: every trade and deposit of the book is
    voided (kept, shown with "show voided"), open orders are cancelled, and the AI book takes its S&P 500 ghost with it.
    Learning (module weights, graduation, lessons) is untouched: it reads paper results of ideas, not the books."""
    db = request.app.state.db
    try:
        out = corrections.reset(db, body.book)
    except corrections.Refused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if body.deposit is not None:
        row = db.fetch_one("INSERT INTO cash_flows (book_id, amount, at, note) SELECT id, %s, now(), %s FROM books "
                           "WHERE kind = %s RETURNING id", (ledger.money(body.deposit), "starting deposit after reset",
                                                            body.book))
        with db.connection() as conn:
            conn.commit()
        out["deposit_id"] = row["id"]
        if body.book == "ai":
            out["ghost_sync"] = Ghost(db).sync()
    return out


@router.get("/practice/account")
def practice_account(request: Request, book: str = Query(default="practice", pattern="^(practice|ai|options)$"),
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
    pending = db.fetch_all("SELECT s.trade_id, s.amount, s.settles_at FROM settlements s JOIN live_trades t "
                           "ON t.id = s.trade_id WHERE t.book_id = %s AND NOT s.settled ORDER BY s.settles_at", (bid,))
    return {"book": book, "last_reset": corrections.last_reset(db, book), "cash": str(f.cash),
            "unsettled": str(f.unsettled),
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


# ── option chains: Tradier (the owner's account) when configured, else Alpaca's free indicative feed ──────────
# Alpaca is asked in production only (staging pulls nothing; it reads production's stored chains), every call
# counted against its limit. Alpaca's prices are indicative (its estimate, not the exchanges' quote), and IV and
# delta are computed from them (market/greeks.py): the answer says so.
EXPIRY_DAYS = 120          # the expiries offered from Alpaca: about four months out, enough for 30-45-day puts
ALPACA_NOTE = "indicative (Alpaca): Alpaca's estimate of the price, not the exchanges' quote; IV and delta computed"


def _tradier(request: Request) -> Tradier:
    made = getattr(request.app.state, "tradier", None)
    return made or Tradier()


def _alpaca(request: Request) -> Alpaca:
    made = getattr(request.app.state, "alpaca", None)
    return made or Alpaca()


def _counted(db: Any, source: str, endpoint: str, fn, *args) -> tuple[Any, str | None]:
    lim = Limiter(db)
    name = source.capitalize()
    try:
        lim.check(source)
        result = fn(*args)
        lim.record(source, endpoint, "ok", rows=len(result) if hasattr(result, "__len__") else 1)
        return result, None
    except NotConfigured as exc:
        lim.record(source, endpoint, "skipped", str(exc))
        return None, f"{name} is not configured: add its key and the chains appear"
    except RateLimited as exc:
        lim.record(source, endpoint, "skipped", str(exc))
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 — the answer says what failed; nothing is guessed
        lim.record(source, endpoint, "error", f"{type(exc).__name__}: {exc}")
        return None, f"{name} did not answer: {type(exc).__name__}"


def _chain_source(request: Request) -> str | None:
    """Which source answers a chain now: Tradier when its key is set; else Alpaca in production; else none (the
    stored chains, production's copy on staging)."""
    if configured().get("tradier"):
        return "tradier"
    if configured().get("alpaca") and request.app.state.settings.is_production:
        return "alpaca"
    return None


@router.get("/options/expirations")
def expirations(request: Request, ticker: str, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    t = ticker.strip().upper()
    source = _chain_source(request)
    today = dt.date.today()
    if source == "alpaca":
        dates, why = _counted(db, "alpaca", "option-expirations", _alpaca(request).option_expirations, t, today,
                              today + dt.timedelta(days=EXPIRY_DAYS))
    elif source == "tradier":
        dates, why = _counted(db, "tradier", "expirations", _tradier(request).expirations, t)
    else:
        dates = store.stored_expirations(db, t, today)
        why = None if dates else "Options are not configured: add Tradier's key (Alpaca's answers in production)"
    return {"ticker": t, "expirations": [d.isoformat() for d in dates or []], "configured": why is None,
            "reason": why, "source": source or "stored", "indicative": source == "alpaca" or None}


@router.get("/options/chain")
def chain(request: Request, ticker: str, expiry: dt.date,
          book: str = Query(default="practice", pattern="^(practice|ai|options)$"), sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    t = ticker.strip().upper()
    source = _chain_source(request)
    as_of = utcnow()
    if source == "alpaca":
        raw, why = _counted(db, "alpaca", "option-chain", _alpaca(request).option_chain, t, expiry)
        greeks.fill(raw or [], store.spot_of(db, t), dt.date.today())
        rows = store.save_chain(db, raw or [], as_of, "alpaca")
    elif source == "tradier":
        raw, why = _counted(db, "tradier", "chain", _tradier(request).chain, t, expiry)
        rows = store.save_chain(db, raw or [], as_of)
    else:
        rows = store.stored_chain(db, t, expiry)
        why = None if rows else "Options are not configured: add Tradier's key (Alpaca's answers in production)"
        as_of = max((r["as_of"] for r in rows), default=as_of)
        source = rows[0]["source"] if rows else "stored"
    facts = Practice(db).facts(Practice(db).book_id(book))
    out = []
    for r in sorted(rows, key=lambda x: (x["kind"], x["strike"])):
        collateral = r["strike"] * 100 if r["kind"] == "put" else None
        out.append({**_row(r), "collateral": str(collateral) if collateral is not None else None,
                    "covered": bool(collateral is not None and collateral <= facts.available)})
    return {"ticker": t, "expiry": expiry.isoformat(), "configured": why is None, "reason": why, "source": source,
            "indicative": source == "alpaca" or None, "note": ALPACA_NOTE if source == "alpaca" else None,
            "as_of": as_of.isoformat() if rows else None, "available": str(facts.available), "book": book,
            "options": out}
