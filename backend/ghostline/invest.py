"""Invest this: "invest $50 tomorrow" split across the current long-term and swing ideas in the mix the scores
recommend (the best idea gets the biggest share), in fractional shares, every split through the risk rules against
the book the money goes into (with the amount added). A refused split is shown with its rule; its money goes to the
splits that pass, rechecked once. On approval the plan goes to the practice account (orders placed at the open of its
date, the amount deposited first unless the owner says not to) or onto the to-do list for real money."""

from __future__ import annotations

import datetime as dt
import json
from decimal import ROUND_DOWN, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from ghostline import ledger, risk, risk_service
from ghostline.db import Database
from ghostline.practice import Practice, next_business_day

PT = ZoneInfo("America/Los_Angeles")
MAX_SPLITS = 5
SHARES = Decimal("0.000001")
LANES = ("long", "swing")


def default_date(now: dt.datetime | None = None) -> dt.date:
    """Tomorrow's session: the next weekday (exchange holidays are not known; the market calendar is not built)."""
    local = (now or dt.datetime.now(dt.UTC)).astimezone(PT)
    return next_business_day(local.date())


def _weights(scores: list[Decimal]) -> list[Decimal]:
    sq = [s * s for s in scores]
    total = sum(sq, Decimal(0))
    return [x / total for x in sq] if total else []


class Planner:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.p = Practice(db)

    def candidates(self) -> list[dict]:
        rows = self.db.fetch_all(
            "SELECT i.id, i.ticker, i.lane, i.module, i.stop, i.target, s.score, s.reason FROM ideas i "
            "JOIN signals s ON s.id = i.signal_id WHERE i.status = 'open' AND i.expires_at > now() "
            "AND i.lane = ANY(%s) AND i.stop IS NOT NULL ORDER BY s.score DESC, i.id",
            (list(LANES),),
        )
        seen, out = set(), []
        for r in rows:
            if r["ticker"] in seen:
                continue
            seen.add(r["ticker"])
            out.append(r)
        return out[:MAX_SPLITS]

    def _price(self, ticker: str) -> tuple[Decimal | None, str]:
        q = self.p.latest_quote(ticker)
        if q and q.get("ask") is not None:
            return Decimal(q["ask"]), f"{q['source']} ask, {q['ts'].isoformat()}"
        px = self.p.latest_price(ticker)
        return px, "latest stored price"

    def _check(self, idea: dict, dollars: Decimal, book: str, amount: Decimal, open_count: int) -> dict:
        price, price_src = self._price(idea["ticker"])
        split: dict[str, Any] = {
            "idea_id": idea["id"],
            "ticker": idea["ticker"],
            "lane": idea["lane"],
            "module": idea["module"],
            "score": str(idea["score"]),
            "reason": idea["reason"],
            "dollars": str(ledger.money(dollars)),
        }
        if price is None or price <= 0:
            split.update(decision="refuse", why="no stored price")
            return split
        stop = Decimal(idea["stop"])
        shares = (dollars / price).quantize(SHARES, rounding=ROUND_DOWN)
        max_loss = ledger.money(shares * (price - stop)) if stop < price else None
        f = self.p.facts(self.p.book_id(book))
        u = self.p.latest_quote(idea["ticker"]) or {}
        spread = None
        if u.get("bid") is not None and u.get("ask") is not None and Decimal(u["ask"]) > 0:
            spread = ((Decimal(u["ask"]) - Decimal(u["bid"])) / Decimal(u["ask"])).quantize(Decimal("0.0001"))
        decision = risk_service.check_and_record(
            self.db,
            risk.Idea(
                kind=idea["lane"],
                max_loss=max_loss,
                days_to_earnings=self.p._days_to_earnings(idea["ticker"]),
                avg_volume=self.p._avg_volume(idea["ticker"]),
                spread_share=spread,
                book=book,
            ),
            risk.Account(
                balance=f.equity + amount,
                cash_set_aside=f.available + amount,
                open_ideas=f.open_positions + len(f.short_legs) + open_count,
                paper_drawdown_share=f.paper_drawdown_share,
            ),
            "invest_split",
        )
        split.update(
            price=str(price),
            price_source=price_src,
            shares=format(shares.normalize(), "f"),
            stop=str(stop),
            max_loss=None if max_loss is None else str(max_loss),
            decision=decision.result,
            why=(f"{decision.rule}: {decision.reason}" if decision.rule else decision.reason),
        )
        return split

    def plan(self, amount: Decimal, for_date: dt.date | None, book: str) -> dict:
        amount = ledger.money(amount)
        for_date = for_date or default_date()
        ideas = self.candidates()
        splits: list[dict] = []
        if ideas:
            floor = max(Decimal(1), amount * Decimal("0.05"))
            weights = _weights([Decimal(i["score"]) for i in ideas])
            keep = [(i, w) for i, w in zip(ideas, weights, strict=True) if amount * w >= floor]
            ideas = [i for i, _ in keep] or ideas[:1]
            # first pass: the recommended mix
            weights = _weights([Decimal(i["score"]) for i in ideas])
            first = [
                self._check(i, amount * w, book, amount, n) for n, (i, w) in enumerate(zip(ideas, weights, strict=True))
            ]
            passed = [i for i, s in zip(ideas, first, strict=True) if s["decision"] == "pass"]
            refused = [s for s in first if s["decision"] != "pass"]
            if refused and passed:
                # second pass: the refused money goes to the splits that passed, each rechecked
                weights = _weights([Decimal(i["score"]) for i in passed])
                splits = [
                    self._check(i, amount * w, book, amount, n)
                    for n, (i, w) in enumerate(zip(passed, weights, strict=True))
                ]
                for r in refused:
                    r["dollars"] = "0.00"
                    r["why"] = f"{r['why']} (its money moved to the ideas that pass)"
                splits += refused
            else:
                splits = first
        placed = sum((Decimal(s["dollars"]) for s in splits if s["decision"] == "pass"), Decimal(0))
        unallocated = ledger.money(amount - placed)
        row = self.db.fetch_one(
            "INSERT INTO invest_plans (amount, for_date, book, splits, unallocated) VALUES (%s, %s, %s, %s::jsonb, %s) "
            "RETURNING *",
            (amount, for_date, book, json.dumps(splits), unallocated),
        )
        with self.db.connection() as conn:
            conn.commit()
        note = None
        if not ideas:
            note = "no open idea in the long-term or swing lanes: nothing to split into yet"
        elif unallocated > 0:
            note = f"{unallocated} has no idea that passes the rules; it stays as cash"
        return {**_plan_row(row), "note": note}

    # ── approval ──
    def approve(self, plan_id: int, to: str, deposit: bool = True) -> dict:
        plan = self.db.fetch_one("SELECT * FROM invest_plans WHERE id = %s", (plan_id,))
        if not plan:
            raise LookupError("no such plan")
        if plan["status"] != "proposed":
            raise ValueError(f"the plan is {plan['status']}")
        splits = plan["splits"] if isinstance(plan["splits"], list) else json.loads(plan["splits"])
        ok = [s for s in splits if s["decision"] == "pass" and Decimal(s["dollars"]) > 0]
        if to == "real":
            ids = []
            for s in ok:
                t = self.db.fetch_one(
                    "INSERT INTO todos (source, source_id, ticker, side, quantity, limit_price, lane, idea_id, note) "
                    "VALUES ('invest', %s, %s, 'buy', %s, %s, %s, %s, %s) RETURNING id",
                    (
                        plan_id,
                        s["ticker"],
                        Decimal(s["shares"]),
                        Decimal(s["price"]),
                        s["lane"],
                        s["idea_id"],
                        f"Invest this #{plan_id}: {s['dollars']} into {s['ticker']} ({s['module']})",
                    ),
                )
                ids.append(int(t["id"]))
            self.db.execute(
                "UPDATE invest_plans SET status = 'placed', approved_to = 'real', decided_at = now(), "
                "placed_at = now(), result = %s::jsonb WHERE id = %s",
                (json.dumps({"todos": ids}), plan_id),
            )
            return {
                **_plan_row(self.db.fetch_one("SELECT * FROM invest_plans WHERE id = %s", (plan_id,))),
                "todos": ids,
            }
        if deposit:
            self.db.execute(
                "INSERT INTO cash_flows (book_id, amount, at, note) VALUES (%s, %s, now(), %s)",
                (self.p.book_id("practice"), plan["amount"], f"Invest this #{plan_id}"),
            )
        self.db.execute(
            "UPDATE invest_plans SET status = 'approved', approved_to = 'practice', decided_at = now() WHERE id = %s",
            (plan_id,),
        )
        today = dt.datetime.now(dt.UTC).astimezone(PT).date()
        if plan["for_date"] <= today:
            self.place(plan_id)
        return _plan_row(self.db.fetch_one("SELECT * FROM invest_plans WHERE id = %s", (plan_id,)))

    def place(self, plan_id: int) -> dict:
        plan = self.db.fetch_one("SELECT * FROM invest_plans WHERE id = %s", (plan_id,))
        splits = plan["splits"] if isinstance(plan["splits"], list) else json.loads(plan["splits"])
        orders = []
        for s in splits:
            if s["decision"] != "pass" or Decimal(s["dollars"]) <= 0:
                continue
            # a buy fills at the ask: size at the ask and round the shares down, or the dollars come up short
            ask, _ = self._price(s["ticker"])
            price = ask or Decimal(s["price"])
            qty = (Decimal(s["dollars"]) / price).quantize(SHARES, rounding=ROUND_DOWN)
            row = self.p.place(
                {
                    "book": "practice",
                    "ticker": s["ticker"],
                    "asset": "stock",
                    "side": "buy",
                    "type": "market",
                    "tif": "day",
                    "quantity": qty,
                    "stop_loss": Decimal(s["stop"]),
                    "idea_id": s["idea_id"],
                    "lane": s["lane"],
                    "note": f"Invest this #{plan_id}: {s['dollars']} into {s['ticker']}",
                },
                by="owner",
            )
            orders.append(
                {"ticker": s["ticker"], "order": row["id"], "status": row["status"], "why": row.get("reason")}
            )
        self.db.execute(
            "UPDATE invest_plans SET status = 'placed', placed_at = now(), result = %s::jsonb WHERE id = %s",
            (json.dumps({"orders": orders}, default=str), plan_id),
        )
        return {"plan": plan_id, "orders": orders}

    def place_due(self) -> dict:
        """At the open: every approved practice plan whose date has come."""
        today = dt.datetime.now(dt.UTC).astimezone(PT).date()
        due = self.db.fetch_all(
            "SELECT id FROM invest_plans WHERE status = 'approved' AND approved_to = 'practice' "
            "AND for_date <= %s ORDER BY id",
            (today,),
        )
        return {"placed": [self.place(int(r["id"])) for r in due]}


def _plan_row(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, (dt.datetime, dt.date)):
            out[k] = v.isoformat()
        elif k in ("splits", "result") and isinstance(v, str):
            out[k] = json.loads(v)
        else:
            out[k] = v
    return out
