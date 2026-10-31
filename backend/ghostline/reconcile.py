"""Reconciliation, as the owner designed it on 2026-10-30. It sets what the app suggested against what the owner did,
and the real book against the broker's statement.

- **Suggestions.** A suggestion is an idea that passed the rules, or a memo's buy call that belongs to no idea, made in
  the month. Each one gets an outcome:
  - took: a real buy carries its idea or memo, or its to-do was closed with a real trade;
  - declined: the owner said no (with an optional reason), or dropped its to-do;
  - lapsed: an idea expired, or a buy call is 30 days old, with nothing done;
  - open: none of these yet.
- **Own calls.** An own call is a real buy carrying no idea and no memo, and closing no to-do.
- **Results.** Each idea shows the paper result the learning loop keeps for it: what following it did, or would have
  done. A taken suggestion and an own call show the realized P&L, to date, of the shares they opened. The P&L follows
  /history: a close counts for the first lot it consumed.
- **The broker check.** It compares the latest statement the owner entered with the real book on that day: stock
  shares and cash. Options are not compared yet.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from ghostline import ledger
from ghostline.claude import push
from ghostline.config import Settings
from ghostline.db import Database
from ghostline.ledger import Trade
from ghostline.market.calendar import NEW_YORK, Calendar

PT = ZoneInfo("America/Los_Angeles")
MEMO_LAPSES_AFTER = dt.timedelta(days=30)
TWO = Decimal("0.01")
OPENS = ("buy", "sell_to_open")


def month_bounds(month: str | None, today: dt.date | None = None) -> tuple[dt.datetime, dt.datetime, str]:
    """A month as "YYYY-MM" (the current one, Pacific time, when none is given) and its start and end in Pacific time.
    Raises ValueError for anything else."""
    today = today or dt.datetime.now(PT).date()
    y, m = (int(x) for x in (month or f"{today.year}-{today.month:02d}").split("-"))
    start = dt.datetime(y, m, 1, tzinfo=PT)
    return start, dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=PT), f"{y}-{m:02d}"


def _s(d: Decimal | None) -> str | None:
    return None if d is None else str(d.quantize(TWO))


def _real_trades(db: Database, before: dt.datetime | None = None) -> list[dict]:
    sql = "SELECT t.* FROM trades t JOIN books b ON b.id = t.book_id WHERE b.kind = 'real'"
    params: tuple = ()
    if before is not None:
        sql, params = sql + " AND t.at < %s", (before,)
    return db.fetch_all(sql + " ORDER BY t.at, t.id", params)


def _realized_by_opening(rows: list[dict]) -> dict[int, Decimal]:
    """Realized P&L to date per opening trade: each close counts for the first lot it consumed (as /history does)."""
    out: dict[int, Decimal] = defaultdict(Decimal)
    closed, _ = ledger.settle([Trade.from_row(r) for r in rows])
    for c in closed:
        if c.opened_trade_ids:
            out[c.opened_trade_ids[0]] += c.pnl
    return out


def report(db: Database, month: str | None = None, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.UTC)
    start, end, label = month_bounds(month)
    rows = _real_trades(db)
    realized = _realized_by_opening(rows)
    opens = [r for r in rows if r["side"] in OPENS]
    took_idea: dict[int, list[dict]] = defaultdict(list)
    took_memo: dict[int, list[dict]] = defaultdict(list)
    for r in opens:
        if r.get("idea_id"):
            took_idea[r["idea_id"]].append(r)
        elif r.get("memo_id"):
            took_memo[r["memo_id"]].append(r)
    by_id = {r["id"]: r for r in rows}
    via_todo = set()
    for t in db.fetch_all("SELECT idea_id, trade_id FROM todos WHERE status = 'done' AND idea_id IS NOT NULL "
                          "AND trade_id IS NOT NULL"):
        via_todo.add(t["trade_id"])
        trade = by_id.get(t["trade_id"])
        if trade and trade not in took_idea[t["idea_id"]]:
            took_idea[t["idea_id"]].append(trade)      # "I Placed It": the to-do knows its idea, the trade may not
    declined = {(d["kind"], d["ref_id"]): d["reason"] for d in db.fetch_all(
        "SELECT kind, ref_id, reason FROM suggestion_decisions")}
    for t in db.fetch_all("SELECT DISTINCT idea_id FROM todos WHERE status = 'dropped' AND idea_id IS NOT NULL"):
        declined.setdefault(("idea", t["idea_id"]), "Dropped from To Place at Your Broker")

    items: list[dict[str, Any]] = []
    for i in db.fetch_all(
            "SELECT i.id, i.ticker, i.lane, i.module, i.entry, i.quantity, i.created_at, i.expires_at, "
            "p.status AS paper_status, p.result_pct, p.excess_pct FROM ideas i "
            "LEFT JOIN paper_results p ON p.idea_id = i.id "
            "WHERE i.status <> 'refused' AND i.created_at >= %s AND i.created_at < %s ORDER BY i.created_at, i.id",
            (start, end)):
        taken = took_idea.get(i["id"], [])
        outcome = ("took" if taken else "declined" if ("idea", i["id"]) in declined
                   else "lapsed" if i["expires_at"] <= now else "open")
        items.append(_item("idea", i["id"], i["ticker"], i["lane"], i["created_at"], outcome,
                           declined.get(("idea", i["id"])), i.get("entry"), i.get("quantity"), taken, realized,
                           {"status": i["paper_status"], "result_pct": _s(i["result_pct"]),
                            "excess_pct": _s(i["excess_pct"])} if i["paper_status"] else None))
    for m in db.fetch_all(
            "SELECT id, ticker, created_at FROM memos WHERE call = 'buy' AND idea_id IS NULL "
            "AND created_at >= %s AND created_at < %s ORDER BY created_at, id", (start, end)):
        taken = took_memo.get(m["id"], [])
        outcome = ("took" if taken else "declined" if ("memo", m["id"]) in declined
                   else "lapsed" if m["created_at"] + MEMO_LAPSES_AFTER <= now else "open")
        items.append(_item("memo", m["id"], m["ticker"], "buy call", m["created_at"], outcome,
                           declined.get(("memo", m["id"])), None, None, taken, realized, None))

    own = [r for r in opens if not r.get("idea_id") and not r.get("memo_id") and r["id"] not in via_todo
           and start <= r["at"] < end]
    own_out = [{"trade_id": r["id"], "ticker": r["ticker"], "side": r["side"], "quantity": str(r["quantity"]),
                "fill": str(r["fill"]), "at": r["at"].isoformat(), "realized": _s(realized.get(r["id"]))} for r in own]
    summary: dict[str, Any] = {}
    for outcome in ("took", "declined", "lapsed", "open"):
        group = [x for x in items if x["outcome"] == outcome]
        settled = [Decimal(x["paper"]["excess_pct"]) for x in group
                   if x["paper"] and x["paper"]["status"] == "settled" and x["paper"]["excess_pct"] is not None]
        real = [Decimal(x["realized"]) for x in group if x["realized"] is not None]
        summary[outcome] = {"n": len(group), "paper_settled": len(settled),
                            "paper_excess_avg": _s(sum(settled, Decimal(0)) / len(settled)) if settled else None,
                            "realized": _s(sum(real, Decimal(0))) if real else None}
    own_real = [Decimal(x["realized"]) for x in own_out if x["realized"] is not None]
    summary["own_calls"] = {"n": len(own_out), "realized": _s(sum(own_real, Decimal(0))) if own_real else None}
    statement = db.fetch_one("SELECT * FROM broker_statements ORDER BY as_of DESC, id DESC LIMIT 1")
    return {"month": label, "suggestions": items, "own_calls": own_out, "summary": summary,
            "broker": compare(db, statement) if statement else None}


def _item(kind: str, ref: int, ticker: str, label: str, at: dt.datetime, outcome: str, reason: str | None,
          entry: Any, quantity: Any, taken: list[dict], realized: dict[int, Decimal],
          paper: dict | None) -> dict[str, Any]:
    first = taken[0] if taken else None
    real = [realized[t["id"]] for t in taken if t["id"] in realized]
    return {"kind": kind, "id": ref, "ticker": ticker, "label": label, "at": at.isoformat(), "outcome": outcome,
            "reason": reason if outcome == "declined" else None,
            "suggested": {"entry": None if entry is None else str(entry),
                          "quantity": None if quantity is None else str(quantity)},
            "taken": {"trade_id": first["id"], "quantity": str(first["quantity"]), "fill": str(first["fill"]),
                      "at": first["at"].isoformat()} if first else None,
            "paper": paper, "realized": _s(sum(real, Decimal(0))) if real else None}


def compare(db: Database, statement: dict) -> dict[str, Any]:
    """The statement against the real book at the end of its day (Pacific time): stock shares and cash."""
    until = dt.datetime.combine(statement["as_of"] + dt.timedelta(days=1), dt.time(0), tzinfo=PT)
    rows = _real_trades(db, until)
    trades = [Trade.from_row(r) for r in rows]
    _, positions = ledger.settle([t for t in trades if t.asset == "stock"])
    app_shares: dict[str, Decimal] = defaultdict(Decimal)
    for p in positions:
        app_shares[p.ticker] += p.quantity
    broker_shares = {h["ticker"].upper(): Decimal(str(h["quantity"])) for h in statement["positions"]}
    real = db.fetch_one("SELECT id FROM books WHERE kind = 'real'")
    flows = [Decimal(f["amount"]) for f in db.fetch_all(
        "SELECT amount FROM cash_flows WHERE book_id = %s AND at < %s", (real["id"], until))] if real else []
    app_cash = ledger.money(ledger.cash_balance(flows, trades))
    lines = []
    for t in sorted(set(app_shares) | set(broker_shares)):
        a, b = app_shares.get(t, Decimal(0)), broker_shares.get(t, Decimal(0))
        if a == 0 and b == 0:
            continue
        lines.append({"ticker": t, "app": str(a.normalize()), "broker": str(b.normalize()),
                      "difference": str((b - a).normalize()), "match": a == b})
    cash_diff = Decimal(statement["cash"]) - app_cash
    return {"statement_id": statement["id"], "as_of": statement["as_of"].isoformat(),
            "shares": lines,
            "cash": {"app": str(app_cash), "broker": str(Decimal(statement["cash"])), "difference": str(cash_diff),
                     "match": cash_diff == 0},
            "matches": all(x["match"] for x in lines) and cash_diff == 0,
            "note": "Stock shares and cash are compared; options are not compared yet."}


def first_session_of_month(cal: Calendar, day: dt.date) -> dt.date:
    first = day.replace(day=1)
    while not cal.is_session(first):
        first += dt.timedelta(days=1)
    return first


def remind(db: Database, settings: Settings, now: dt.datetime | None = None) -> dict[str, Any]:
    """On the month's first trading day: one push asking the owner to check the real book against the statement."""
    today = (now or dt.datetime.now(dt.UTC)).astimezone(NEW_YORK).date()
    if today != first_session_of_month(Calendar.load(db), today):
        return {"sent": 0, "skipped": "not the month's first trading day"}
    return push.send(db, settings, "Monthly check",
                     "Compare Ghostline with your broker statement: Account → Reconcile.", {"kind": "reconcile"})
