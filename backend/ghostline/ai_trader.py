"""The AI trader: a practice book the owner funds with any amount. It trades only the app's own open ideas, each
sized as a share of its own balance from the owner's risk rules, each order through the same rules as his; gains stay
in the book. Every order carries the idea and the reason. Exits: the idea's stop, its target, or its horizon passing.
The ghost (ghost.py) buys the S&P 500 with the same deposits on the same dates.

Copy: an AI trade repeats in the practice book at the same share of that book's balance, or, only for a lane that has
graduated in practice (lane_graduation, phase 7), becomes a to-do item for real money."""

from __future__ import annotations

import datetime as dt
import json
import logging
from decimal import Decimal
from typing import Any

from ghostline import ledger, occ, risk_service
from ghostline.db import Database
from ghostline.ghost import Ghost
from ghostline.ledger import Trade
from ghostline.practice import Practice

logger = logging.getLogger("ghostline.ai_trader")
SHARES = Decimal("0.000001")
MAX_POSITION_SHARE = Decimal("0.20")  # no single position above a fifth of the book (the ideas' own cap)


class AITrader:
    def __init__(self, db: Database, book: str = "ai") -> None:
        self.db = db
        self.book = book
        self.p = Practice(db)

    @property
    def book_id(self) -> int:
        return self.p.book_id(self.book)

    def _record(self, kind: str, result: dict) -> dict:
        self.db.execute(
            "INSERT INTO ai_trader_runs (kind, result) VALUES (%s, %s::jsonb)", (kind, json.dumps(result, default=str))
        )
        return result

    def _loss_share(self) -> Decimal | None:
        rules = risk_service.rule_set(self.db)
        if rules.missing:
            return None
        r = rules.rules.get("max_loss_per_idea_share")
        return r.value if r and r.enabled else Decimal("0.01")

    def _tried_today(self, idea_id: int) -> bool:
        row = self.db.fetch_one(
            "SELECT 1 AS ok FROM orders WHERE book_id = %s AND idea_id = %s AND "
            "(status IN ('open', 'filled') OR placed_at::date = CURRENT_DATE) LIMIT 1",
            (self.book_id, idea_id),
        )
        return bool(row)

    # ── entries ──
    def run_entries(self) -> dict[str, Any]:
        """Once a day after the scan: every open idea the book has not taken, best score first, while money lasts."""
        if self.book == "ai":
            Ghost(self.db).sync()
        facts = self.p.facts(self.book_id)
        if facts.equity <= 0:
            return self._record("entries", {"skipped": f"the {self.book} book has no money: assign a deposit to it"})
        loss_share = self._loss_share()
        if loss_share is None:
            return self._record("entries", {"skipped": "risk rules not set: no idea passes until they are"})
        lanes = ("puts",) if self.book == "options" else ("long", "swing", "puts")
        ideas = self.db.fetch_all(
            "SELECT i.*, s.reason, s.horizon_days, s.score FROM ideas i JOIN signals s ON s.id = i.signal_id "
            "WHERE i.status = 'open' AND i.expires_at > now() AND i.lane = ANY(%s) ORDER BY s.score DESC, i.id",
            (list(lanes),),
        )
        out: dict[str, Any] = {"placed": [], "refused": [], "skipped": []}
        for idea in ideas:
            if self._tried_today(idea["id"]):
                continue
            facts = self.p.facts(self.book_id)
            order = self._entry_order(idea, facts, loss_share)
            if isinstance(order, str):
                out["skipped"].append({"idea": idea["id"], "ticker": idea["ticker"], "why": order})
                continue
            row = self.p.place(order, by=f"{self.book}-trader")
            entry = {"idea": idea["id"], "ticker": idea["ticker"], "order": row["id"], "status": row["status"]}
            if row["status"] == "refused":
                entry["why"] = row["reason"]
                out["refused"].append(entry)
            else:
                out["placed"].append(entry)
        return self._record("entries" if self.book == "ai" else "options", out)

    def _entry_order(self, idea: dict, facts, loss_share: Decimal) -> dict | str:
        label = "AI trader" if self.book == "ai" else "Options trader"
        reason = f"{label}, idea #{idea['id']} ({idea['module']}, {idea['lane']}): {idea['reason']}"
        if idea["lane"] == "puts":
            if not idea.get("option_symbol"):
                return "a put idea without its option"
            opt = occ.parse(idea["option_symbol"])
            if opt.collateral_per_contract > facts.available:
                return f"one contract needs {opt.collateral_per_contract} of collateral; {facts.available} is available"
            return {
                "book": self.book,
                "ticker": opt.underlying,
                "asset": "option",
                "side": "sell_to_open",
                "type": "market",
                "tif": "day",
                "quantity": Decimal(1),
                "option_symbol": opt.symbol,
                "stop_loss": idea.get("stop"),
                "note": reason,
                "idea_id": idea["id"],
                "lane": "puts",
            }
        price = self.p.latest_price(idea["ticker"])
        stop = Decimal(idea["stop"]) if idea.get("stop") is not None else None
        if price is None:
            return "no stored price"
        if stop is None or stop >= price:
            return f"the stop {stop} is not under the price {price}"
        risk_money = facts.equity * loss_share
        qty = (risk_money / (price - stop)).quantize(SHARES)
        cap = (facts.equity * MAX_POSITION_SHARE / price).quantize(SHARES)
        afford = (facts.available * Decimal("0.98") / price).quantize(SHARES)
        qty = min(qty, cap, afford)
        if qty <= 0:
            return f"nothing affordable: {facts.available} available"
        return {
            "book": self.book,
            "ticker": idea["ticker"],
            "asset": "stock",
            "side": "buy",
            "type": "market",
            "tif": "day",
            "quantity": qty,
            "stop_loss": stop,
            "note": reason,
            "idea_id": idea["id"],
            "lane": idea["lane"],
        }

    # ── exits ──
    def run_exits(self) -> dict[str, Any]:
        """Every 30 minutes in the session: a held position whose idea's stop, target or horizon is reached is sold."""
        bid = self.book_id
        trades = [Trade.from_row(r) for r in self.db.fetch_all("SELECT * FROM trades WHERE book_id = %s", (bid,))]
        _, positions = ledger.settle(trades)
        out: dict[str, Any] = {"sold": [], "held": 0, "closed_puts": []}
        for pos in positions:
            if self.db.fetch_one(
                "SELECT 1 AS ok FROM orders WHERE book_id = %s AND ticker = %s AND side = 'sell' AND status = 'open'",
                (bid, pos.ticker),
            ):
                continue
            origin = self.db.fetch_one(
                "SELECT t.at, i.id, i.stop, i.target, s.horizon_days FROM trades t JOIN ideas i ON i.id = t.idea_id "
                "JOIN signals s ON s.id = i.signal_id WHERE t.book_id = %s AND t.ticker = %s AND t.side = 'buy' "
                "ORDER BY t.at DESC LIMIT 1",
                (bid, pos.ticker),
            )
            price = self.p.latest_price(pos.ticker)
            if not origin or price is None:
                out["held"] += 1  # assigned shares or no price: held, never sold on a guess
                continue
            why = None
            if origin["stop"] is not None and price <= Decimal(origin["stop"]):
                why = f"stop {Decimal(origin['stop'])} reached at {price}"
            elif origin["target"] is not None and Decimal(origin["target"]) > 0 and price >= Decimal(origin["target"]):
                why = f"target {Decimal(origin['target'])} reached at {price}"
            elif (dt.datetime.now(dt.UTC) - origin["at"]).days > int(origin["horizon_days"] or 0):
                why = f"the idea's {origin['horizon_days']}-day horizon passed"
            if not why:
                out["held"] += 1
                continue
            row = self.p.place(
                {
                    "book": self.book,
                    "ticker": pos.ticker,
                    "asset": "stock",
                    "side": "sell",
                    "type": "market",
                    "tif": "day",
                    "quantity": pos.quantity,
                    "idea_id": origin["id"],
                    "note": f"{self.book} trader exit, idea #{origin['id']}: {why}",
                },
                by=f"{self.book}-trader",
            )
            out["sold"].append({"ticker": pos.ticker, "order": row["id"], "status": row["status"], "why": why})
        for symbol, n in self.p.facts(bid).short_legs.items():
            leg = self.db.fetch_one(
                "SELECT i.id, i.stop FROM trades t JOIN ideas i ON i.id = t.idea_id WHERE t.book_id = %s AND "
                "t.option_symbol = %s AND t.side = 'sell_to_open' ORDER BY t.at DESC LIMIT 1",
                (bid, symbol),
            )
            opt = occ.parse(symbol)
            under = self.p.latest_price(opt.underlying)
            if not leg or leg["stop"] is None or under is None or under > Decimal(leg["stop"]):
                continue
            row = self.p.place(
                {
                    "book": self.book,
                    "ticker": opt.underlying,
                    "asset": "option",
                    "side": "buy_to_close",
                    "type": "market",
                    "tif": "day",
                    "quantity": n,
                    "option_symbol": symbol,
                    "idea_id": leg["id"],
                    "note": f"{self.book} trader exit, idea #{leg['id']}: {opt.underlying} at {under} is "
                    f"under the stop {Decimal(leg['stop'])}",
                },
                by=f"{self.book}-trader",
            )
            out["closed_puts"].append({"option": symbol, "order": row["id"], "status": row["status"]})
        return self._record("exits", out)

    # ── the account, for the screen ──
    def state(self) -> dict[str, Any]:
        f = self.p.facts(self.book_id)
        last = self.db.fetch_all(
            "SELECT kind, result, at FROM ai_trader_runs WHERE kind = ANY(%s) ORDER BY id DESC LIMIT 3",
            (["entries", "exits"] if self.book == "ai" else ["options"],),
        )
        return {
            "book": self.book,
            "cash": str(f.cash),
            "available": str(f.available),
            "unsettled": str(f.unsettled),
            "locked_collateral": str(f.locked_collateral),
            "equity": str(f.equity),
            "open_positions": f.open_positions,
            "short_puts": len(f.short_legs),
            "last_runs": [
                {
                    "kind": r["kind"],
                    "at": r["at"].isoformat(),
                    "result": r["result"] if isinstance(r["result"], dict) else json.loads(r["result"]),
                }
                for r in last
            ],
        }


# ── copy ─────────────────────────────────────────────────────────────────────────────────────────────────────
class CopyRefused(Exception):
    pass


def lane_graduated(db: Database, lane: str | None) -> bool:
    if not lane:
        return False
    row = db.fetch_one("SELECT graduated FROM lane_graduation WHERE lane = %s", (lane,))
    return bool(row and row["graduated"])


def copy_trade(db: Database, trade_id: int, to: str) -> dict[str, Any]:
    """Repeat one AI trade: in practice at the same share of the practice balance, or as a to-do item for real money
    when the trade's lane has graduated. A copy of a sale sells what the target holds of that ticker."""
    p = Practice(db)
    t = db.fetch_one(
        "SELECT t.*, b.kind AS book FROM trades t JOIN books b ON b.id = t.book_id WHERE t.id = %s", (trade_id,)
    )
    if not t or t["book"] != "ai":
        raise CopyRefused("only an AI trader trade can be copied")
    if to == "real" and not lane_graduated(db, t.get("lane")):
        raise CopyRefused(f"the {t.get('lane') or 'unknown'} lane has not graduated in practice: copy it to practice")
    target = "practice" if to == "practice" else "real"
    ai_equity = p.facts(p.book_id("ai")).equity
    tgt_facts = p.facts(p.book_id(target))
    note = f"copied from AI trader trade #{trade_id}"
    idea = db.fetch_one("SELECT stop FROM ideas WHERE id = %s", (t["idea_id"],)) if t.get("idea_id") else None
    if t["asset"] == "option":
        qty = Decimal(1)
    elif t["side"] == "buy":
        share = (Decimal(t["quantity"]) * Decimal(t["fill"]) / ai_equity) if ai_equity > 0 else Decimal(0)
        price = p.latest_price(t["ticker"]) or Decimal(t["fill"])
        qty = (share * tgt_facts.equity / price).quantize(SHARES) if tgt_facts.equity > 0 else Decimal(0)
        if qty <= 0:
            raise CopyRefused(f"the {target} book has no money to copy into")
    else:
        row = db.fetch_one(
            "SELECT quantity FROM positions WHERE book_id = %s AND ticker = %s", (p.book_id(target), t["ticker"])
        )
        qty = Decimal(row["quantity"]) if row else Decimal(0)
        if qty <= 0:
            raise CopyRefused(f"the {target} book holds no {t['ticker']} to sell")
    if target == "practice":
        row = p.place(
            {
                "book": "practice",
                "ticker": t["ticker"],
                "asset": t["asset"],
                "side": t["side"],
                "type": "market",
                "tif": "day",
                "quantity": qty,
                "option_symbol": t.get("option_symbol"),
                "stop_loss": idea["stop"] if idea else None,
                "note": note,
                "idea_id": t.get("idea_id"),
                "lane": t.get("lane"),
            },
            by="owner",
        )
        return {
            "to": "practice",
            "order": {k: (str(v) if isinstance(v, (Decimal, dt.datetime)) else v) for k, v in row.items()},
        }
    todo = db.fetch_one(
        "INSERT INTO todos (source, source_id, ticker, asset, side, quantity, limit_price, option_symbol, lane, "
        "idea_id, note) VALUES ('ai_copy', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            trade_id,
            t["ticker"],
            t["asset"],
            t["side"],
            qty,
            t["fill"],
            t.get("option_symbol"),
            t.get("lane"),
            t.get("idea_id"),
            note,
        ),
    )
    with db.connection() as conn:
        conn.commit()
    return {"to": "real", "todo_id": int(todo["id"]), "quantity": str(qty)}
