"""The practice account: order tickets filled at the real bid and ask from the stored quotes, next-business-day
settlement in a cash account, cash-secured puts with their collateral locked, assignment or expiry at the option's
expiry from the stored price. Nothing real moves. Every fill becomes an ordinary trade in the ledger, so history,
the portfolio and the risk checks read practice and real money the same way.

The pure part (fill rules, the settlement calendar) has no database; the engine below reads and writes one.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from ghostline import ledger, occ, prices, risk, risk_service
from ghostline.db import Database
from ghostline.ledger import Trade

logger = logging.getLogger("ghostline.practice")
PT = ZoneInfo("America/Los_Angeles")
SETTLE_HOUR = dt.time(6, 30)        # funds from a sale are usable at the next business day's open
CLOSE = dt.time(13, 0)              # the session's close, Pacific time
BUYING = ("buy", "buy_to_close")
OPENING = ("buy", "sell_to_open")
SIMULATED_BOOKS = ("practice", "ai", "options")   # the real book is placed by the owner at the broker, never here


# ── pure rules ─────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Fill:
    price: Decimal | None       # None = nothing fills on this quote
    triggered: bool             # a stop that crossed its price (stays triggered on later quotes)


def fill_price(side: str, kind: str, bid: Decimal | None, ask: Decimal | None, limit_price: Decimal | None,
               stop_price: Decimal | None, triggered: bool = False) -> Fill:
    """A buy pays the ask, a sale gets the bid. A limit fills when the market is at least as good as the limit, at the
    market's price. A stop becomes a market (or limit) order once the market crosses it. No quote, no fill."""
    buying = side in BUYING
    market = ask if buying else bid
    if market is None:
        return Fill(None, triggered)
    if kind in ("stop", "stop_limit") and not triggered:
        crossed = (market >= stop_price) if buying else (market <= stop_price)
        if not crossed:
            return Fill(None, False)
        triggered = True
    if kind in ("limit", "stop_limit"):
        good = (market <= limit_price) if buying else (market >= limit_price)
        return Fill(market if good else None, triggered)
    return Fill(market, triggered)


def next_business_day(d: dt.date) -> dt.date:
    """The next weekday. Exchange holidays are not known here (the market calendar is not built)."""
    n = d + dt.timedelta(days=1)
    while n.weekday() >= 5:
        n += dt.timedelta(days=1)
    return n


def settles_at(filled_at: dt.datetime) -> dt.datetime:
    """T+1: the open of the next business day, Pacific time."""
    local = filled_at.astimezone(PT)
    return dt.datetime.combine(next_business_day(local.date()), SETTLE_HOUR, tzinfo=PT)


def session_close(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, CLOSE, tzinfo=PT)


# ── the engine ─────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Facts:
    """What the book has right now, every number from the ledger and the stored prices."""
    cash: Decimal                   # deposits − buys + sales − fees
    unsettled: Decimal              # proceeds of sales not yet settled
    locked_collateral: Decimal      # strike × 100 per open short put contract
    available: Decimal              # cash − unsettled − locked: what an order may spend
    equity: Decimal                 # cash + positions at the latest stored price (at cost when none)
    open_positions: int
    short_legs: dict[str, Decimal]  # option symbol → open contracts sold
    paper_drawdown_share: Decimal


class Practice:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ── reads ──
    def fee(self) -> Decimal:
        row = self.db.fetch_one("SELECT value FROM practice_settings WHERE key = 'fee_per_order'")
        return Decimal(row["value"]) if row else Decimal(0)

    def book_id(self, kind: str) -> int:
        row = self.db.fetch_one("SELECT id FROM books WHERE kind = %s", (kind,))
        if not row:
            raise ValueError(f"no book {kind}")
        return int(row["id"])

    def short_legs(self, book_id: int) -> dict[str, Decimal]:
        rows = self.db.fetch_all(
            "SELECT option_symbol, SUM(CASE WHEN side = 'sell_to_open' THEN quantity ELSE -quantity END) AS open_qty "
            "FROM trades WHERE book_id = %s AND asset = 'option' GROUP BY option_symbol "
            "HAVING SUM(CASE WHEN side = 'sell_to_open' THEN quantity ELSE -quantity END) > 0", (book_id,))
        return {r["option_symbol"]: Decimal(r["open_qty"]) for r in rows}

    def facts(self, book_id: int) -> Facts:
        flows = [Decimal(f["amount"]) for f in
                 self.db.fetch_all("SELECT amount FROM cash_flows WHERE book_id = %s", (book_id,))]
        rows = self.db.fetch_all("SELECT * FROM trades WHERE book_id = %s ORDER BY at, id", (book_id,))
        trades = [Trade.from_row(r) for r in rows]
        cash = ledger.cash_balance(flows, trades)
        un = self.db.fetch_one("SELECT COALESCE(SUM(s.amount), 0) AS amount FROM settlements s JOIN trades t "
                               "ON t.id = s.trade_id WHERE t.book_id = %s AND NOT s.settled", (book_id,))
        unsettled = ledger.money(un["amount"]) if un else Decimal(0)
        legs = self.short_legs(book_id)
        locked = Decimal(0)
        for symbol, n in legs.items():
            try:
                locked += occ.parse(symbol).collateral_per_contract * n
            except ValueError:
                logger.warning("open leg %s is not an OCC symbol; no collateral counted", symbol)
        closed, positions = ledger.settle(trades)
        px = prices.latest(self.db, sorted({p.ticker for p in positions}))
        equity = cash
        for p in positions:
            q = px.get(p.ticker)
            value = p.value(q.price if q else None)
            equity += value if value is not None else p.cost_basis
        return Facts(cash=cash, unsettled=unsettled, locked_collateral=ledger.money(locked),
                     available=ledger.money(cash - unsettled - locked), equity=ledger.money(equity),
                     open_positions=len(positions), short_legs=legs,
                     paper_drawdown_share=self._drawdown(flows, closed, ledger.money(equity)))

    @staticmethod
    def _drawdown(flows: list[Decimal], closed: list[ledger.Closed], equity: Decimal) -> Decimal:
        """How far the book sits under its best point: the peak of (net deposits + realized P&L to date) against the
        equity now. Zero while there is no peak above the deposits."""
        net = sum(flows, Decimal(0))
        peak = net
        run = net
        for c in sorted(closed, key=lambda x: x.at):
            run += c.pnl
            peak = max(peak, run)
        if peak <= 0 or equity >= peak:
            return Decimal(0)
        return ((peak - equity) / peak).quantize(Decimal("0.0001"))

    def latest_quote(self, symbol: str) -> dict | None:
        return self.db.fetch_one("SELECT * FROM quotes WHERE ticker = %s ORDER BY as_of DESC LIMIT 1", (symbol,))

    def latest_price(self, ticker: str) -> Decimal | None:
        """The last trade, else the mid, else yesterday's close; None when nothing is stored (never a guess)."""
        q = self.latest_quote(ticker)
        if q:
            if q.get("last") is not None:
                return Decimal(q["last"])
            if q.get("bid") is not None and q.get("ask") is not None:
                return (Decimal(q["bid"]) + Decimal(q["ask"])) / 2
        bar = self.db.fetch_one("SELECT close FROM market_bars WHERE ticker = %s AND timeframe = '1D' "
                                "ORDER BY ts DESC LIMIT 1", (ticker,))
        return Decimal(bar["close"]) if bar else None

    def _avg_volume(self, ticker: str) -> Decimal | None:
        row = self.db.fetch_one("SELECT AVG(volume) AS v FROM (SELECT volume FROM market_bars WHERE ticker = %s AND "
                                "timeframe = '1D' ORDER BY ts DESC LIMIT 20) t", (ticker,))
        return Decimal(row["v"]) if row and row["v"] is not None else None

    def _days_to_earnings(self, ticker: str) -> int | None:
        row = self.db.fetch_one("SELECT at FROM earnings WHERE ticker = %s AND at >= CURRENT_DATE ORDER BY at LIMIT 1",
                                (ticker,))
        return (row["at"] - dt.date.today()).days if row else None

    def _open_interest(self, symbol: str) -> Decimal | None:
        row = self.db.fetch_one("SELECT open_interest FROM option_chains WHERE option_symbol = %s "
                                "ORDER BY as_of DESC LIMIT 1", (symbol,))
        return Decimal(row["open_interest"]) if row and row["open_interest"] is not None else None

    # ── placing ──
    def place(self, o: dict[str, Any], by: str) -> dict:
        """Validate, run the risk rules for an opening order, check the cash account, store the order, and fill it at
        once when the stored quote allows. The answer is the order row; a refusal carries its reason."""
        book_kind = o.get("book", "practice")
        if book_kind not in SIMULATED_BOOKS:
            raise ValueError(f"{book_kind} is not a simulated book: real money is placed at the broker")
        book_id = self.book_id(book_kind)
        symbol = o.get("option_symbol")
        opt = None
        if o["asset"] == "option":
            opt = occ.parse(symbol)
            if opt.kind != "put":
                return self._store(o, book_id, "refused", "only puts are sold here: cash-secured puts only")
            if opt.expiry < dt.date.today():
                return self._store(o, book_id, "refused", f"{opt.symbol} expired on {opt.expiry}")
            o["ticker"] = opt.underlying
            symbol = opt.symbol
        quote = self.latest_quote(symbol or o["ticker"])
        bid = Decimal(quote["bid"]) if quote and quote.get("bid") is not None else None
        ask = Decimal(quote["ask"]) if quote and quote.get("ask") is not None else None
        qty = Decimal(o["quantity"])
        facts = self.facts(book_id)
        fee = self.fee()

        # a closing order needs the position; an opening order needs the rules and the cash
        if o["side"] == "sell":
            held = self._held(book_id, o["ticker"])
            if held < qty:
                return self._store(o, book_id, "refused", f"you hold {held} {o['ticker']} in {book_kind}, not {qty}")
        elif o["side"] == "buy_to_close":
            open_n = facts.short_legs.get(symbol, Decimal(0))
            if open_n < qty:
                return self._store(o, book_id, "refused", f"{open_n} open {symbol} contracts, not {qty}")
        else:
            ref = o.get("limit_price") or o.get("stop_price") or (ask if o["side"] == "buy" else bid)
            decision = self._risk(o, opt, qty, ref, bid, ask, facts, book_kind)
            if not decision.passed:
                return self._store(o, book_id, "refused", decision.reason, rule=decision.rule)
            if o["side"] == "buy":
                if ref is None:
                    return self._store(o, book_id, "refused", f"no stored price for {o['ticker']} yet")
                need = ledger.money(qty * Decimal(ref) + fee)
                if need > facts.available:
                    why = (f"needs {need}, available {facts.available}"
                           + (f" ({facts.unsettled} unsettled)" if facts.unsettled else ""))
                    return self._store(o, book_id, "refused", why)
            else:   # sell_to_open a put: the collateral comes out of the available cash
                need = ledger.money(opt.collateral_per_contract * qty + fee)
                if need > facts.available:
                    return self._store(o, book_id, "refused",
                                       f"collateral {need} exceeds the available cash {facts.available}")
        row = self._store(o, book_id, "open", None)
        if quote:
            self.try_fill(row, quote)
            row = self.db.fetch_one("SELECT * FROM orders WHERE id = %s", (row["id"],))
        return row

    def _risk(self, o, opt, qty, ref, bid, ask, facts: Facts, book_kind: str) -> risk.Decision:
        stop_loss = Decimal(o["stop_loss"]) if o.get("stop_loss") else None
        if opt is None:
            kind = "swing"
            if ref is None:
                max_loss = None
            elif stop_loss and stop_loss < Decimal(ref):
                max_loss = ledger.money(qty * (Decimal(ref) - stop_loss))
            else:
                max_loss = ledger.money(qty * Decimal(ref))
            collateral, oi = None, None
        else:
            kind = "puts"
            premium = bid if bid is not None else Decimal(0)
            if stop_loss and stop_loss < opt.strike:
                max_loss = ledger.money((opt.strike - stop_loss - premium) * 100 * qty)
            else:
                max_loss = ledger.money((opt.strike - premium) * 100 * qty)
            collateral = ledger.money(opt.collateral_per_contract * qty)
            oi = self._open_interest(opt.symbol)
            # the spread floor is on the underlying: a premium's own spread is wide by nature
            u = self.latest_quote(opt.underlying) or {}
            bid = Decimal(u["bid"]) if u.get("bid") is not None else None
            ask = Decimal(u["ask"]) if u.get("ask") is not None else None
        spread = ((ask - bid) / ask).quantize(Decimal("0.0001")) if ask and bid is not None and ask > 0 else None
        idea = risk.Idea(kind=kind, max_loss=max_loss, collateral=collateral,
                         days_to_earnings=self._days_to_earnings(o["ticker"]), avg_volume=self._avg_volume(o["ticker"]),
                         open_interest=oi, spread_share=spread, is_day_trade=False, book=book_kind)
        account = risk.Account(balance=facts.equity, cash_set_aside=facts.available,
                               open_ideas=facts.open_positions + len(facts.short_legs),
                               paper_drawdown_share=facts.paper_drawdown_share)
        return risk_service.check_and_record(self.db, idea, account, "order")

    def _held(self, book_id: int, ticker: str) -> Decimal:
        row = self.db.fetch_one("SELECT quantity FROM positions WHERE book_id = %s AND ticker = %s", (book_id, ticker))
        return Decimal(row["quantity"]) if row else Decimal(0)

    def _store(self, o: dict, book_id: int, status: str, reason: str | None, rule: str | None = None) -> dict:
        why = f"{rule}: {reason}" if rule else reason
        row = self.db.fetch_one(
            "INSERT INTO orders (book_id, ticker, asset, side, type, tif, quantity, limit_price, stop_price, "
            "stop_loss, option_symbol, status, reason, note, idea_id, lane) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (book_id, o["ticker"], o["asset"], o["side"], o.get("type", "market"), o.get("tif", "day"),
             o["quantity"], o.get("limit_price"), o.get("stop_price"), o.get("stop_loss"), o.get("option_symbol"),
             status, why, o.get("note"), o.get("idea_id"), o.get("lane")))
        with self.db.connection() as conn:
            conn.commit()
        return row

    # ── filling ──
    def try_fill(self, order: dict, quote: dict) -> bool:
        bid = Decimal(quote["bid"]) if quote.get("bid") is not None else None
        ask = Decimal(quote["ask"]) if quote.get("ask") is not None else None
        f = fill_price(order["side"], order["type"], bid, ask,
                       Decimal(order["limit_price"]) if order.get("limit_price") is not None else None,
                       Decimal(order["stop_price"]) if order.get("stop_price") is not None else None,
                       bool(order.get("triggered")))
        if f.triggered and not order.get("triggered"):
            self.db.execute("UPDATE orders SET triggered = true WHERE id = %s", (order["id"],))
        if f.price is None:
            return False
        self._fill(order, f.price, quote)
        return True

    def _fill(self, order: dict, price: Decimal, quote: dict) -> None:
        now = dt.datetime.now(dt.UTC)
        fee = self.fee()
        with self.db.connection() as conn:
            filled = (f"order {order['id']} filled at the {'ask' if order['side'] in BUYING else 'bid'} "
                      f"({quote['source']}, quote of {quote['ts'].isoformat()})")
            note = f"{order['note']} · {filled}" if order.get("note") else filled
            t = conn.execute(
                "INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at, fees, broker, note, "
                "option_symbol, idea_id, lane) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'practice', %s, %s, %s, %s) "
                "RETURNING id",
                (order["book_id"], order["ticker"], order["asset"], order["side"], order["quantity"], price, now, fee,
                 note, order.get("option_symbol"), order.get("idea_id"), order.get("lane"))).fetchone()
            if order["side"] not in BUYING:
                m = Decimal(100) if order["asset"] == "option" else Decimal(1)
                proceeds = ledger.money(Decimal(order["quantity"]) * price * m - fee)
                conn.execute("INSERT INTO settlements (trade_id, amount, settles_at) VALUES (%s, %s, %s)",
                             (t["id"], proceeds, settles_at(now)))
            conn.execute("UPDATE orders SET status = 'filled', filled_at = %s, fill_price = %s, trade_id = %s "
                         "WHERE id = %s", (now, price, t["id"], order["id"]))
            conn.commit()

    def fill_open_orders(self) -> dict:
        """Every open order against the latest stored quote for its symbol; settlements that are due are settled."""
        out = {"filled": 0, "open": 0, "settled": self.settle_due()}
        for order in self.db.fetch_all("SELECT * FROM orders WHERE status = 'open' ORDER BY id"):
            quote = self.latest_quote(order.get("option_symbol") or order["ticker"])
            if quote and self.try_fill(order, quote):
                out["filled"] += 1
            else:
                out["open"] += 1
        return out

    # ── the session's end ──
    def settle_due(self, now: dt.datetime | None = None) -> int:
        return self.db.execute("UPDATE settlements SET settled = true WHERE NOT settled AND settles_at <= %s",
                               (now or dt.datetime.now(dt.UTC),))

    def expire_day_orders(self, now: dt.datetime | None = None) -> int:
        """A day order still open after its session's close expires."""
        now = now or dt.datetime.now(dt.UTC)
        close = session_close(now.astimezone(PT).date())
        if now < close:
            close = session_close(next_business_day(now.astimezone(PT).date()) - dt.timedelta(days=1))
            close = close if close <= now else session_close(now.astimezone(PT).date() - dt.timedelta(days=1))
        return self.db.execute("UPDATE orders SET status = 'expired', reason = 'day order not filled by the close' "
                               "WHERE status = 'open' AND tif = 'day' AND placed_at <= %s", (close,))

    def expire_options(self, today: dt.date | None = None) -> dict:
        """Every open short put at or past its expiry: assigned (100 shares per contract bought at the strike) when
        the underlying's stored price is under the strike, expired worthless otherwise. No stored price, no action."""
        today = today or dt.date.today()
        out = {"assigned": 0, "expired": 0, "waiting": 0}
        for b in self.db.fetch_all("SELECT id FROM books WHERE kind IN ('practice', 'ai', 'options')"):
            for symbol, n in self.short_legs(b["id"]).items():
                try:
                    opt = occ.parse(symbol)
                except ValueError:
                    continue
                if opt.expiry > today:
                    continue
                price = self.latest_price(opt.underlying)
                if price is None:
                    logger.warning("%s expired but %s has no stored price; waiting", symbol, opt.underlying)
                    out["waiting"] += 1
                    continue
                assigned = opt.kind == "put" and price < opt.strike
                self._close_leg(b["id"], opt, n, assigned, price)
                out["assigned" if assigned else "expired"] += 1
        return out

    def _close_leg(self, book_id: int, opt: occ.Option, n: Decimal, assigned: bool, price: Decimal) -> None:
        at = dt.datetime.combine(opt.expiry, CLOSE, tzinfo=PT)
        kind = "assignment" if assigned else "expiry"
        with self.db.connection() as conn:
            opened = conn.execute("SELECT id FROM trades WHERE book_id = %s AND option_symbol = %s AND "
                                  "side = 'sell_to_open' ORDER BY at DESC LIMIT 1", (book_id, opt.symbol)).fetchone()
            t = conn.execute(
                "INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at, fees, broker, note, "
                "option_symbol) VALUES (%s, %s, 'option', 'buy_to_close', %s, 0, %s, 0, 'practice', %s, %s) "
                "RETURNING id",
                (book_id, opt.underlying, n, at,
                 f"{kind} at expiry: {opt.underlying} at {price} vs strike {opt.strike}", opt.symbol)).fetchone()
            conn.execute("INSERT INTO option_events (trade_id, kind, at, strike, shares, note) VALUES "
                         "(%s, %s, %s, %s, %s, %s)",
                         (opened["id"] if opened else t["id"], kind, at, opt.strike, n * 100 if assigned else None,
                          f"{opt.underlying} at {price}"))
            if assigned:
                conn.execute("INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at, fees, broker, "
                             "note) VALUES (%s, %s, 'stock', 'buy', %s, %s, %s, 0, 'practice', %s)",
                             (book_id, opt.underlying, n * 100, opt.strike, at,
                              f"assigned from {opt.symbol} ({n} contracts)"))
            conn.commit()

    def close_session(self, now: dt.datetime | None = None) -> dict:
        now = now or dt.datetime.now(dt.UTC)
        return {"expired_orders": self.expire_day_orders(now), "settled": self.settle_due(now),
                **{f"options_{k}": v for k, v in self.expire_options(now.astimezone(PT).date()).items()}}
