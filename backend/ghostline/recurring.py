"""Automatic recurring buys in the practice book (the owner's go, 2026-11-01): "buy $X of TICKER every day, week,
two weeks or month". The worker places each run as an ordinary practice market order on trading days, sized in
fractional shares at the ask, so it passes the same risk rules and lands in the same history as any other order.

A plan's own date (its weekday, every other week from its start, or its day of the month, the last day in a short
month) moves to the next session when the market is closed that day, as Robinhood's recurring investments do; the run
records both dates. A run the rules refuse, or that finds no price, is recorded as skipped with the reason.

Practice book only: the AI book trades its own ideas, and its S&P 500 ghost mirrors deposits, not purchases."""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import ROUND_DOWN, Decimal
from typing import Any

from ghostline.db import Database
from ghostline.market.calendar import NEW_YORK, Calendar
from ghostline.practice import Practice

SHARES = Decimal("0.000001")
EVERY = ("day", "week", "two_weeks", "month")
LOOKAHEAD_DAYS = 70          # the next run is found within ten weeks (a monthly plan always has one)


def is_scheduled(plan: dict, day: dt.date) -> bool:
    """Whether `day` is one of the plan's own dates (before any move for a closed market)."""
    if day < plan["starts_on"]:
        return False
    every = plan["every"]
    if every == "day":
        return True
    if every == "week":
        return day.weekday() == plan["weekday"]
    if every == "two_weeks":
        first = plan["starts_on"] + dt.timedelta(days=(plan["weekday"] - plan["starts_on"].weekday()) % 7)
        return day >= first and (day - first).days % 14 == 0
    if every == "month":
        last = calendar.monthrange(day.year, day.month)[1]
        return day.day == min(plan["month_day"], last)
    raise ValueError(f"unknown frequency {every!r}")


def due(plan: dict, day: dt.date, cal: Calendar) -> dt.date | None:
    """The plan's date that runs on `day`, or None. A session runs every plan date since the previous session, so a
    date the market was closed (a weekend, a holiday) runs on the next session. A daily plan runs on sessions only."""
    if not cal.is_session(day):
        return None
    if plan["every"] == "day":
        return day if day >= plan["starts_on"] else None
    d, prev = day, cal.previous_session(day)
    while d > prev:
        if is_scheduled(plan, d):
            return d
        d -= dt.timedelta(days=1)
    return None


def next_run(plan: dict, today: dt.date, cal: Calendar) -> dt.date | None:
    """The first session on or after `today` when the plan runs."""
    d = max(today, plan["starts_on"])
    for _ in range(LOOKAHEAD_DAYS):
        if due(plan, d, cal):
            return d
        d += dt.timedelta(days=1)
    return None


class Recurring:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.p = Practice(db)

    def run_due(self, today: dt.date | None = None) -> dict[str, Any]:
        """Place every active plan due today, once each (a second run the same day changes nothing)."""
        today = today or dt.datetime.now(NEW_YORK).date()
        cal = Calendar.load(self.db)
        out: dict[str, Any] = {"placed": 0, "skipped": 0, "not_due": 0}
        if not cal.is_session(today):
            return {**out, "closed": today.isoformat()}
        for plan in self.db.fetch_all("SELECT * FROM recurring_plans WHERE status = 'active' AND starts_on <= %s "
                                      "ORDER BY id", (today,)):
            scheduled = due(plan, today, cal)
            if scheduled is None:
                out["not_due"] += 1
                continue
            if self.db.fetch_one("SELECT 1 AS done FROM recurring_runs WHERE plan_id = %s AND day = %s",
                                 (plan["id"], today)):
                continue
            status, order_id, reason = self._place(plan, scheduled, today)
            if scheduled != today:
                moved = f"scheduled for {scheduled.isoformat()}, a day the market was closed"
                reason = f"{moved}; {reason}" if reason else moved
            self.db.execute("INSERT INTO recurring_runs (plan_id, day, scheduled, status, order_id, reason) VALUES "
                            "(%s, %s, %s, %s, %s, %s) ON CONFLICT (plan_id, day) DO NOTHING",
                            (plan["id"], today, scheduled, status, order_id, reason))
            out[status] += 1
        return out

    def _place(self, plan: dict, scheduled: dt.date, today: dt.date) -> tuple[str, int | None, str | None]:
        q = self.p.latest_quote(plan["ticker"])
        price = Decimal(q["ask"]) if q and q.get("ask") is not None else self.p.latest_price(plan["ticker"])
        if not price or price <= 0:
            return "skipped", None, f"no stored price for {plan['ticker']}"
        qty = (Decimal(plan["dollars"]) / price).quantize(SHARES, rounding=ROUND_DOWN)
        if qty <= 0:
            return "skipped", None, f"{plan['dollars']} buys no share at {price}"
        row = self.p.place({"book": "practice", "ticker": plan["ticker"], "asset": "stock", "side": "buy",
                            "type": "market", "tif": "day", "quantity": qty,
                            "note": f"recurring buy #{plan['id']}: {plan['dollars']} of {plan['ticker']}"},
                           by="recurring")
        if row["status"] == "refused":
            return "skipped", row["id"], row.get("reason")
        return "placed", row["id"], None
