"""Corporate actions (the owner kept the proposed item, 2026-10-30): Alpaca's splits and cash dividends, applied to the
books that held the stock.

- The practice, AI, options and ghost books get them on their own:
  - a split on its ex-date, before the session opens;
  - a dividend on its payable date, on the shares held when the stock went ex (at the end of the day before).
- The real book waits for the owner. Each action is listed, and goes into the ledger only once the owner confirms the
  broker shows it; a dividend is booked with the amount the broker paid. "Not in my account" dismisses it.

In the ledger a split is a trade with side "split" (quantity = the ratio's new count, fill = its old count), and a
dividend is a trade with side "dividend" (quantity 1, fill = the cash paid); see migration 0011 and ledger.settle.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from ghostline import ledger
from ghostline.db import Database
from ghostline.ledger import Trade
from ghostline.market.calendar import NEW_YORK

EX_TIME = dt.time(4, 0)          # a split's place in the ledger: its ex-date, before any trade of the session
PAID_TIME = dt.time(9, 30)       # a dividend's cash: its payable date, at the open


def held(db: Database, book_id: int, ticker: str, before: dt.datetime) -> Decimal:
    """Shares of a stock a book held just before a moment."""
    rows = db.fetch_all("SELECT * FROM trades WHERE book_id = %s AND ticker = %s AND asset = 'stock' AND at < %s "
                        "ORDER BY at, id", (book_id, ticker, before))
    _, positions = ledger.settle([Trade.from_row(r) for r in rows])
    return sum((p.quantity for p in positions), Decimal(0))


def _n(d: Decimal) -> str:
    """A plain number: Decimal's normalize() alone writes 10 as "1E+1"."""
    return format(d.normalize(), "f")


def describe(a: dict, shares: Decimal, amount: Decimal | None) -> str:
    if a["kind"] == "cash_dividend":
        return (f"{a['ticker']} dividend: {ledger.money(amount or 0)} "
                f"({_n(a['rate'])} a share on {_n(shares)})")
    after = _n(shares * a["new_rate"] / a["old_rate"])
    return (f"{a['ticker']} {_n(a['new_rate'])}-for-{_n(a['old_rate'])} split: "
            f"{_n(shares)} shares become {after}")


def _book(conn: Any, a: dict, book_id: int, shares: Decimal, amount: Decimal | None) -> int:
    """The action's trade in one book."""
    if a["kind"] == "cash_dividend":
        at = dt.datetime.combine(a["payable_date"], PAID_TIME, tzinfo=NEW_YORK)
        side, quantity, fill = "dividend", Decimal(1), ledger.money(amount or 0)
    else:
        at = dt.datetime.combine(a["ex_date"], EX_TIME, tzinfo=NEW_YORK)
        side, quantity, fill = "split", a["new_rate"], a["old_rate"]
    row = conn.execute("INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at, fees, broker, note) "
                       "VALUES (%s, %s, 'stock', %s, %s, %s, %s, 0, 'corporate action', %s) RETURNING id",
                       (book_id, a["ticker"], side, quantity, fill, at,
                        f"{describe(a, shares, amount)} · Alpaca action {a['id']}")).fetchone()
    return row["id"]


def apply(db: Database, today: dt.date | None = None) -> dict[str, int]:
    """Every action that is due: a split from its ex-date, a dividend from its payable date. Each book is handled
    once per action (corporate_action_books), so running again changes nothing."""
    today = today or dt.datetime.now(NEW_YORK).date()
    books = db.fetch_all("SELECT id, kind FROM books ORDER BY id")
    out = {"applied": 0, "pending": 0, "waiting": 0}
    for a in db.fetch_all("SELECT * FROM corporate_actions WHERE ex_date <= %s ORDER BY ex_date, id", (today,)):
        dividend = a["kind"] == "cash_dividend"
        if dividend and (a["payable_date"] is None or a["payable_date"] > today or not a["rate"]):
            out["waiting"] += 1
            continue
        if not dividend and not (a["new_rate"] and a["old_rate"]):
            continue
        ex_open = dt.datetime.combine(a["ex_date"], EX_TIME, tzinfo=NEW_YORK)
        done = {r["book_id"] for r in db.fetch_all(
            "SELECT book_id FROM corporate_action_books WHERE action_id = %s", (a["id"],))}
        for b in books:
            if b["id"] in done:
                continue
            shares = held(db, b["id"], a["ticker"], ex_open)
            if shares <= 0:
                continue
            amount = ledger.money(shares * a["rate"]) if dividend else None
            with db.connection() as conn:
                if b["kind"] == "real":
                    conn.execute("INSERT INTO corporate_action_books (action_id, book_id, status, shares, amount) "
                                 "VALUES (%s, %s, 'pending', %s, %s)", (a["id"], b["id"], shares, amount))
                    out["pending"] += 1
                else:
                    trade_id = _book(conn, a, b["id"], shares, amount)
                    conn.execute("INSERT INTO corporate_action_books (action_id, book_id, status, shares, amount, "
                                 "trade_id, decided_at) VALUES (%s, %s, 'applied', %s, %s, %s, now())",
                                 (a["id"], b["id"], shares, amount, trade_id))
                    out["applied"] += 1
                conn.commit()
    return out


def pending(db: Database) -> list[dict[str, Any]]:
    """The real book's actions waiting for the owner, oldest first."""
    rows = db.fetch_all(
        "SELECT a.*, c.shares, c.amount FROM corporate_action_books c JOIN corporate_actions a ON a.id = c.action_id "
        "JOIN books b ON b.id = c.book_id WHERE b.kind = 'real' AND c.status = 'pending' ORDER BY a.ex_date, a.id")
    return [{"id": r["id"], "kind": r["kind"], "ticker": r["ticker"], "ex_date": r["ex_date"].isoformat(),
             "payable_date": r["payable_date"].isoformat() if r["payable_date"] else None,
             "shares": _n(r["shares"]), "amount": str(r["amount"]) if r["amount"] is not None else None,
             "new_rate": _n(r["new_rate"]) if r["new_rate"] else None,
             "old_rate": _n(r["old_rate"]) if r["old_rate"] else None,
             "text": describe(r, r["shares"], r["amount"])} for r in rows]


def decide(db: Database, action_id: str, confirm: bool, amount: Decimal | None = None) -> dict[str, Any]:
    """The owner's answer for the real book: confirm (a dividend with the amount the broker paid, when given) writes
    the action into the ledger; dismiss records that the broker does not show it. Raises LookupError when nothing
    waits under that id."""
    row = db.fetch_one(
        "SELECT a.*, c.book_id, c.shares, c.amount FROM corporate_action_books c "
        "JOIN corporate_actions a ON a.id = c.action_id JOIN books b ON b.id = c.book_id "
        "WHERE b.kind = 'real' AND c.status = 'pending' AND a.id = %s", (action_id,))
    if not row:
        raise LookupError(action_id)
    with db.connection() as conn:
        if confirm:
            paid = ledger.money(amount) if amount is not None and row["kind"] == "cash_dividend" else row["amount"]
            trade_id = _book(conn, row, row["book_id"], row["shares"], paid)
            conn.execute("UPDATE corporate_action_books SET status = 'confirmed', amount = %s, trade_id = %s, "
                         "decided_at = now() WHERE action_id = %s AND book_id = %s",
                         (paid, trade_id, action_id, row["book_id"]))
        else:
            trade_id = None
            conn.execute("UPDATE corporate_action_books SET status = 'dismissed', decided_at = now() "
                         "WHERE action_id = %s AND book_id = %s", (action_id, row["book_id"]))
        conn.commit()
    return {"id": action_id, "status": "confirmed" if confirm else "dismissed", "trade_id": trade_id}
