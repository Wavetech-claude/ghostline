"""Books, cash flows, trades, history and the portfolio. Every amount is a Decimal string in JSON."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ghostline import ledger, prices
from ghostline.auth import OwnerSub
from ghostline.ghost import Ghost
from ghostline.ledger import Trade
from ghostline.market import store
from ghostline.routes.claude import MEMO_SQL

router = APIRouter()
BOOKS = ("real", "practice", "ai", "ghost", "options")
SIDES = ("buy", "sell", "sell_to_open", "buy_to_close")
LANES = ("long", "swing", "puts", "day")


def _book_id(db: Any, kind: str) -> int:
    row = db.fetch_one("SELECT id FROM books WHERE kind = %s", (kind,))
    if not row:
        raise HTTPException(status_code=404, detail=f"no book {kind}")
    return int(row["id"])


def _dec(v: Any) -> str | None:
    return None if v is None else str(v)


def _qty(v: Any) -> str:
    """A quantity without trailing zeros: 5, 0.5, 1.25 (money keeps its two decimals)."""
    return format(Decimal(v).normalize(), "f")


def _row(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, Decimal):
            out[k] = _qty(v) if k == "quantity" else str(v)
        elif isinstance(v, dt.datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _flows(db: Any, book_id: int) -> list[Decimal]:
    return [Decimal(f["amount"]) for f in db.fetch_all("SELECT amount FROM cash_flows WHERE book_id = %s", (book_id,))]


# ── books and cash ─────────────────────────────────────────────────────────────────────────────────────────
@router.get("/books")
def books(request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    rows = db.fetch_all("SELECT id, kind, name, created_at FROM books ORDER BY id")
    out = []
    for b in rows:
        flows = _flows(db, b["id"])
        trades = [Trade.from_row(t) for t in db.fetch_all("SELECT * FROM trades WHERE book_id = %s", (b["id"],))]
        out.append({**_row(b), "deposits": str(ledger.money(sum((f for f in flows if f > 0), Decimal(0)))),
                    "withdrawals": str(ledger.money(-sum((f for f in flows if f < 0), Decimal(0)))),
                    "cash": str(ledger.cash_balance(flows, trades)), "trades": len(trades)})
    return {"books": out}


class CashFlow(BaseModel):
    # the ghost is the system's: it mirrors the AI book's flows (ghost.py), never typed in
    book: str = Field(pattern="^(real|practice|ai|options)$")
    amount: Decimal = Field(description="positive = deposit, negative = withdrawal; any amount")
    at: dt.datetime
    note: str | None = Field(default=None, max_length=500)

    @field_validator("amount")
    @classmethod
    def _not_zero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("amount must not be zero")
        return v.quantize(Decimal("0.01"))


@router.post("/cash-flows", status_code=201)
def add_cash_flow(body: CashFlow, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    bid = _book_id(db, body.book)
    row = db.fetch_one("INSERT INTO cash_flows (book_id, amount, at, note) VALUES (%s, %s, %s, %s) RETURNING *",
                       (bid, body.amount, body.at, body.note))
    with db.connection() as conn:
        conn.commit()
    out = _row(row)
    if body.book == "ai":
        # the ghost mirrors it now when SPY's price for the date is stored; the worker's half-hourly sync otherwise
        out["ghost"] = Ghost(db).sync()
    return out


@router.get("/cash-flows")
def cash_flows(request: Request, book: str | None = None, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    if book:
        rows = db.fetch_all("SELECT f.*, b.kind AS book FROM cash_flows f JOIN books b ON b.id = f.book_id "
                            "WHERE b.kind = %s ORDER BY at DESC, id DESC", (book,))
    else:
        rows = db.fetch_all("SELECT f.*, b.kind AS book FROM cash_flows f JOIN books b ON b.id = f.book_id "
                            "ORDER BY at DESC, id DESC")
    return {"cash_flows": [_row(r) for r in rows]}


# ── trades ─────────────────────────────────────────────────────────────────────────────────────────────────
class TradeIn(BaseModel):
    book: str = Field(pattern="^(real|practice|ai|options)$")
    ticker: str = Field(min_length=1, max_length=12)
    asset: str = Field(default="stock", pattern="^(stock|option)$")
    side: str = Field(pattern="^(buy|sell|sell_to_open|buy_to_close)$")
    quantity: Decimal = Field(gt=0, description="shares (fractions allowed) or contracts")
    fill: Decimal = Field(ge=0, description="price per share, or premium per share for an option")
    at: dt.datetime
    fees: Decimal = Field(default=Decimal(0), ge=0)
    broker: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    option_symbol: str | None = Field(default=None, max_length=32)
    lane: str | None = Field(default=None, pattern="^(long|swing|puts|day)$")
    idea_id: int | None = None
    memo_id: int | None = None

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    def check(self) -> None:
        if self.asset == "stock" and self.side not in ("buy", "sell"):
            raise HTTPException(status_code=422, detail="a stock trade is a buy or a sell")
        if self.asset == "option" and not self.option_symbol:
            raise HTTPException(status_code=422, detail="an option trade names its option_symbol")


class TradePatch(BaseModel):
    quantity: Decimal | None = Field(default=None, gt=0)
    fill: Decimal | None = Field(default=None, ge=0)
    at: dt.datetime | None = None
    fees: Decimal | None = Field(default=None, ge=0)
    broker: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    lane: str | None = Field(default=None, pattern="^(long|swing|puts|day)$")


_TRADE_SQL = ("SELECT t.*, b.kind AS book, (SELECT count(*) FROM attachments a WHERE a.trade_id = t.id) AS attachments "
              "FROM trades t JOIN books b ON b.id = t.book_id")


@router.post("/trades", status_code=201)
def add_trade(body: TradeIn, request: Request, sub: str = OwnerSub) -> dict:
    body.check()
    db = request.app.state.db
    bid = _book_id(db, body.book)
    row = db.fetch_one(
        "INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at, fees, broker, note, option_symbol, "
        "lane, idea_id, memo_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (bid, body.ticker, body.asset, body.side, body.quantity, body.fill, body.at, body.fees, body.broker, body.note,
         body.option_symbol, body.lane, body.idea_id, body.memo_id))
    with db.connection() as conn:
        conn.commit()
    return _row(db.fetch_one(_TRADE_SQL + " WHERE t.id = %s", (row["id"],)))


@router.patch("/trades/{trade_id}")
def patch_trade(trade_id: int, body: TradePatch, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=422, detail="nothing to change")
    sets = ", ".join(f"{k} = %s" for k in fields)
    n = db.execute(f"UPDATE trades SET {sets} WHERE id = %s", (*fields.values(), trade_id))  # noqa: S608 — keys are the model's
    if not n:
        raise HTTPException(status_code=404, detail="no such trade")
    return _row(db.fetch_one(_TRADE_SQL + " WHERE t.id = %s", (trade_id,)))


def _filters(book, ticker, since, until, side, asset, lane) -> tuple[str, list]:
    where, params = [], []
    for clause, value in (("b.kind = %s", book), ("t.ticker = %s", ticker.upper() if ticker else None),
                          ("t.at >= %s", since), ("t.at <= %s", until), ("t.side = %s", side),
                          ("t.asset = %s", asset), ("t.lane = %s", lane)):
        if value is not None and value != "":
            where.append(clause)
            params.append(value)
    return (" WHERE " + " AND ".join(where)) if where else "", params


@router.get("/trades")
def list_trades(request: Request, sub: str = OwnerSub, book: str | None = None, ticker: str | None = None,
                since: dt.datetime | None = None, until: dt.datetime | None = None, side: str | None = None,
                asset: str | None = None, lane: str | None = None, limit: int = Query(default=200, le=1000)) -> dict:
    db = request.app.state.db
    where, params = _filters(book, ticker, since, until, side, asset, lane)
    rows = db.fetch_all(_TRADE_SQL + where + " ORDER BY t.at DESC, t.id DESC LIMIT %s", (*params, limit))
    return {"trades": [_row(r) for r in rows]}


@router.get("/trades/{trade_id}")
def get_trade(trade_id: int, request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    row = db.fetch_one(_TRADE_SQL + " WHERE t.id = %s", (trade_id,))
    if not row:
        raise HTTPException(status_code=404, detail="no such trade")
    events = db.fetch_all("SELECT * FROM option_events WHERE trade_id = %s ORDER BY at", (trade_id,))
    files = db.fetch_all("SELECT id, content_type, bytes, filename, created_at FROM attachments WHERE trade_id = %s "
                         "ORDER BY id", (trade_id,))
    return {**_row(row), "option_events": [_row(e) for e in events], "attachment_list": [_row(f) for f in files]}


# ── history ────────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/history")
def history(request: Request, sub: str = OwnerSub, book: str | None = None, ticker: str | None = None,
            since: dt.datetime | None = None, until: dt.datetime | None = None, side: str | None = None,
            asset: str | None = None, lane: str | None = None, result: str | None = None) -> dict:
    """Closed trades with realized P&L (after fees), open positions with unrealized P&L at the latest stored price,
    and the totals of the filtered set. The FIFO runs over the whole book so a filter never breaks a lot."""
    db = request.app.state.db
    all_rows = db.fetch_all(_TRADE_SQL + " ORDER BY t.at, t.id")
    by_id = {r["id"]: r for r in all_rows}
    closed, positions = ledger.settle([Trade.from_row(r) for r in all_rows])
    where, params = _filters(book, ticker, since, until, side, asset, lane)
    keep = {r["id"] for r in db.fetch_all(_TRADE_SQL + where, params)}
    closed = [c for c in closed if c.trade_id in keep]
    if result in ("win", "loss"):
        closed = [c for c in closed if (c.pnl > 0) == (result == "win")]
    px = prices.latest(db, sorted({p.ticker for p in positions}))
    books = {r["id"]: r["kind"] for r in db.fetch_all("SELECT id, kind FROM books")}
    open_out = []
    for p in positions:
        if book and books.get(p.book_id) != book:
            continue
        if ticker and p.ticker != ticker.upper():
            continue
        q = px.get(p.ticker)
        open_out.append({"book": books.get(p.book_id), "ticker": p.ticker, "quantity": _qty(p.quantity),
                         "average_cost": str(p.average_cost), "cost_basis": str(p.cost_basis),
                         "price": _dec(q.price if q else None), "price_source": q.source if q else None,
                         "price_as_of": q.as_of.isoformat() if q else None,
                         "unrealized_pnl": _dec(p.unrealized(q.price if q else None))})
    closed_out = []
    for c in closed:
        t = by_id[c.trade_id]
        # a sale by hand still belongs to the idea and memo the shares were bought on
        opened = [by_id[o] for o in c.opened_trade_ids if o in by_id]
        idea_id = t.get("idea_id") or next((o["idea_id"] for o in opened if o.get("idea_id")), None)
        memo_id = t.get("memo_id") or next((o["memo_id"] for o in opened if o.get("memo_id")), None)
        closed_out.append({"trade_id": c.trade_id, "book": books.get(c.book_id), "ticker": c.ticker,
                           "asset": t["asset"], "lane": t.get("lane"), "quantity": _qty(c.quantity),
                           "at": c.at.isoformat(), "proceeds": str(c.proceeds), "cost": str(c.cost),
                           "pnl": str(c.pnl), "pnl_pct": _dec(c.pnl_pct),
                           "opened_trade_ids": list(c.opened_trade_ids), "idea_id": idea_id,
                           "memo_id": memo_id, "attachments": t.get("attachments")})
    totals = {k: _dec(v) if isinstance(v, Decimal) else v for k, v in ledger.totals(closed).items()}
    return {"closed": closed_out, "open": open_out, "totals": totals}


# ── portfolio ──────────────────────────────────────────────────────────────────────────────────────────────
def _day_pnls(db: Any, trades: list[Trade], positions: list[ledger.Position],
              px: dict[str, prices.Price]) -> dict[tuple[int, str], Decimal | None]:
    """Day P&L per (book, stock ticker) for every position held now or closed in its ticker's latest session. A ticker
    without a stored quote has no session, so its day P&L is unknown (None)."""
    sessions = {t: prices.session_day(q.as_of) for t, q in px.items()}
    closes = prices.previous_closes(db, sessions)
    held = {(p.book_id, p.ticker): p.quantity for p in positions}
    in_session: dict[tuple[int, str], list[Trade]] = {}
    for t in trades:
        if t.asset == "stock" and t.ticker in sessions and prices.session_day(t.at) == sessions[t.ticker]:
            in_session.setdefault((t.book_id, t.ticker), []).append(t)
    out: dict[tuple[int, str], Decimal | None] = {}
    for key in set(held) | set(in_session):
        q = px.get(key[1])
        out[key] = ledger.day_pnl(held.get(key, Decimal(0)), q.price if q else None, closes.get(key[1]),
                                  in_session.get(key, [])) if q else None
    return out


@router.get("/portfolio")
def portfolio(request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    books = db.fetch_all("SELECT id, kind, name FROM books ORDER BY id")
    all_rows = db.fetch_all("SELECT * FROM trades ORDER BY at, id")
    trades = [Trade.from_row(r) for r in all_rows]
    _, positions = ledger.settle(trades)
    recent = dt.datetime.now(dt.UTC) - dt.timedelta(days=5)      # a ticker sold out in its latest session still counts
    px = prices.latest(db, sorted({p.ticker for p in positions}
                                  | {t.ticker for t in trades if t.asset == "stock" and t.at >= recent}))
    day = _day_pnls(db, trades, positions, px)
    lanes = {(r["book_id"], r["ticker"]): r.get("lane") for r in all_rows if r.get("lane")}
    out_books, grand = [], Decimal(0)
    for b in books:
        flows = _flows(db, b["id"])
        cash = ledger.cash_balance(flows, [t for t in trades if t.book_id == b["id"]])
        pos_out, invested, priced = [], Decimal(0), True
        for p in (p for p in positions if p.book_id == b["id"]):
            q = px.get(p.ticker)
            value = p.value(q.price if q else None)
            if value is None:
                priced = False
            invested += value if value is not None else p.cost_basis
            pos_out.append({"ticker": p.ticker, "quantity": _qty(p.quantity), "average_cost": str(p.average_cost),
                            "cost_basis": str(p.cost_basis), "price": _dec(q.price if q else None),
                            "price_source": q.source if q else None, "price_as_of": q.as_of.isoformat() if q else None,
                            "value": _dec(value), "unrealized_pnl": _dec(p.unrealized(q.price if q else None)),
                            "day_pnl": _dec(day.get((p.book_id, p.ticker))),
                            "lane": lanes.get((p.book_id, p.ticker))})
        total = ledger.money(cash + invested)
        grand += total
        by_lane: dict[str, Decimal] = {}
        for p in pos_out:
            lane = p["lane"] or "none"
            by_lane[lane] = by_lane.get(lane, Decimal(0)) + Decimal(p["value"] or p["cost_basis"])
        deposits = sum((f for f in flows if f > 0), Decimal(0))
        net = sum(flows, Decimal(0))
        book_day = [v for (bid, _), v in day.items() if bid == b["id"]]
        known_day = [v for v in book_day if v is not None]
        out_books.append({"book": b["kind"], "name": b["name"], "cash": str(cash),
                          "invested": str(ledger.money(invested)), "total": str(total), "priced": priced,
                          "net_deposits": str(ledger.money(net)),
                          "total_pnl": str(ledger.money(total - net)) if deposits else None,
                          "day_pnl": str(ledger.money(sum(known_day, Decimal(0)))) if known_day else None,
                          "day_pnl_partial": len(known_day) < len(book_day),
                          "positions": pos_out,
                          "allocation": [{"name": k, "share": str((v / total).quantize(Decimal("0.0001")))
                                          if total else None} for k, v in sorted(by_lane.items())]})
    unpriced = "some positions have no stored price yet; shown at cost"
    return {"books": out_books, "total": str(ledger.money(grand)),
            "note": None if all(b["priced"] for b in out_books) else unpriced}


# ── one position ───────────────────────────────────────────────────────────────────────────────────────────
@router.get("/positions/{book}/{ticker}")
def position(book: str, ticker: str, request: Request, sub: str = OwnerSub) -> dict:
    """One stock in one book: shares, average cost, price with source and time, value, day and total P&L, the thesis
    it was bought on (the idea's reason, stop and target), the newest memo's call, recent news and the next report."""
    if book not in BOOKS:
        raise HTTPException(status_code=404, detail=f"no book {book}")
    db = request.app.state.db
    t = ticker.strip().upper()
    book_id = _book_id(db, book)
    rows = db.fetch_all("SELECT * FROM trades WHERE book_id = %s AND ticker = %s AND asset = 'stock' ORDER BY at, id",
                        (book_id, t))
    if not rows:
        raise HTTPException(status_code=404, detail=f"no {t} trades in the {book} book")
    trades = [Trade.from_row(r) for r in rows]
    closed, positions = ledger.settle(trades)
    pos = positions[0] if positions else None
    q = prices.latest(db, [t]).get(t)
    price = q.price if q else None
    day = _day_pnls(db, trades, positions, {t: q} if q else {}).get((book_id, t))
    realized = ledger.money(sum((c.pnl for c in closed), Decimal(0)))
    unrealized = pos.unrealized(price) if pos else ledger.money(Decimal(0))
    thesis = db.fetch_one(
        "SELECT i.id AS idea_id, i.lane, i.module, i.entry, i.stop, i.target, i.status, s.reason FROM trades t "
        "JOIN ideas i ON i.id = t.idea_id JOIN signals s ON s.id = i.signal_id "
        "WHERE t.book_id = %s AND t.ticker = %s AND t.side = 'buy' ORDER BY t.at DESC, t.id DESC LIMIT 1",
        (book_id, t))
    memo = db.fetch_one(MEMO_SQL + " WHERE ticker = %s ORDER BY id DESC LIMIT 1", (t,))
    report = db.fetch_one("SELECT * FROM earnings WHERE ticker = %s AND at >= %s "
                          "ORDER BY at LIMIT 1", (t, dt.datetime.now(prices.NEW_YORK).date()))
    return {"book": book, "ticker": t, "quantity": _qty(pos.quantity) if pos else "0",
            "average_cost": str(pos.average_cost) if pos else None,
            "cost_basis": str(pos.cost_basis) if pos else None, "price": _dec(price),
            "price_source": q.source if q else None, "price_as_of": q.as_of.isoformat() if q else None,
            "value": _dec(pos.value(price)) if pos else "0.00", "day_pnl": _dec(day),
            "unrealized_pnl": _dec(unrealized), "realized_pnl": str(realized),
            "total_pnl": _dec(None if unrealized is None else ledger.money(realized + unrealized)),
            "thesis": store.jsonable(thesis) if thesis else None, "memo": store.jsonable(memo) if memo else None,
            "news": store.as_json(store.news_for(db, t, limit=5)),
            "next_earnings": store.jsonable(report) if report else None}
