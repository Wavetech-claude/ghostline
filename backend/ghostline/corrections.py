"""Correcting the ledger without losing anything (migration 0015; the owner approved, 2026-11-01).

How real apps do it (docs: the competitor audit): a broker never lets a filled trade be edited, and fixes its own
errors by voiding the wrong entry and booking a correct one, both kept; a tracker you type trades into lets you edit or
delete them; a paper account is reset as a whole. Ghostline keeps each model where it fits, and never deletes:

- **The real book** (typed in by the owner): edit any field of a trade or a cash flow, or void it. A voided entry
  stays, with when and why, and leaves every number; unvoid brings it back. Every edit, void and unvoid is a row in
  ledger_edits. An entry written by a corporate action can be voided but not edited: its numbers come from the
  action. A conversion (a ticker change or merger, 0014) is two rows written together; voiding one voids both.
- **The practice, AI and options books** (filled by the app): a fill is never edited. reset() voids every entry of
  one book at once ("reset YYYY-MM-DD") and cancels its open orders; resetting the AI book also voids the S&P 500
  ghost's entries, which only mirror the AI book's deposits. Learning is untouched: module weights, graduation, lessons
  and the test set read paper results of ideas (paper_results), never a book's trades.

A change that would leave a later sale closing more shares than the book holds is refused, naming the sale: voiding
a buy whose shares were sold, or cutting its quantity below what was sold, would make the ledger quietly close what
exists instead (ledger.settle) and every P&L after it would be wrong. Void or edit the sale first.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from ghostline import ledger
from ghostline.db import Database
from ghostline.ledger import Trade
from ghostline.market.calendar import NEW_YORK

TRADE_FIELDS = ("ticker", "asset", "side", "quantity", "fill", "at", "fees", "broker", "note", "option_symbol", "lane")
FLOW_FIELDS = ("amount", "at", "note")
OWNER_SIDES = ("buy", "sell", "sell_to_open", "buy_to_close")       # what the owner types; the rest are actions'
CLOSING = ("sell", "buy_to_close", "cash_out")
RESETTABLE = ("practice", "ai", "options")
TOLERANCE = Decimal("0.000001")     # the share column's precision (numeric(18, 6))


class Refused(Exception):
    """The change is not allowed; the message says why and what to do instead."""


class NotFound(LookupError):
    pass


# ── the guard: no sale may close more than the book holds ──────────────────────────────────────────────────
def shortfalls(trades: list[Trade]) -> dict[int, Decimal]:
    """Per closing trade, how much of it found no open lot to close (0 for a sale that closed all it sold)."""
    closed, _ = ledger.settle(trades)
    got: dict[int, Decimal] = {}
    for c in closed:
        got[c.trade_id] = got.get(c.trade_id, Decimal(0)) + c.quantity
    return {t.id: t.quantity - got.get(t.id, Decimal(0)) for t in trades if t.side in CLOSING}


def _check_no_new_shortfall(before: list[Trade], after: list[Trade]) -> None:
    was, now = shortfalls(before), shortfalls(after)
    worse = sorted(i for i, short in now.items() if short > was.get(i, Decimal(0)) + TOLERANCE)
    if worse:
        ids = ", ".join(f"#{i}" for i in worse)
        raise Refused(f"this would leave sale {ids} selling more than the book holds: void or edit that sale first")


def _book_trades(conn: Any, book_id: int) -> list[Trade]:
    rows = conn.execute("SELECT * FROM live_trades WHERE book_id = %s", (book_id,)).fetchall()
    return [Trade.from_row(r) for r in rows]


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    return str(v)


def _log(conn: Any, entity: str, entity_id: int, action: str, field: str | None = None, old: Any = None,
         new: Any = None, reason: str | None = None) -> None:
    conn.execute("INSERT INTO ledger_edits (entity, entity_id, action, field, old, new, reason) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                 (entity, entity_id, action, field, _text(old), _text(new), reason))


def history(db: Database, entity: str, entity_id: int) -> list[dict]:
    return db.fetch_all("SELECT action, field, old, new, reason, by_whom, at FROM ledger_edits "
                        "WHERE entity = %s AND entity_id = %s ORDER BY id", (entity, entity_id))


# ── trades ─────────────────────────────────────────────────────────────────────────────────────────────────
def _trade(conn: Any, trade_id: int) -> dict:
    # includes voided: the entry being corrected may be voided
    row = conn.execute("SELECT t.*, b.kind AS book FROM trades t JOIN books b ON b.id = t.book_id "  # includes voided
                       "WHERE t.id = %s FOR UPDATE OF t", (trade_id,)).fetchone()
    if not row:
        raise NotFound(f"no trade {trade_id}")
    return row


def _from_action(conn: Any, row: dict) -> bool:
    if row["side"] not in OWNER_SIDES or row.get("broker") == "corporate action":
        return True
    return conn.execute("SELECT 1 AS x FROM corporate_action_books WHERE trade_id = %s", (row["id"],)).fetchone() \
        is not None


def edit_problem(conn: Any, row: dict) -> str | None:
    """Why this trade cannot be edited at all, before its new values are even checked; None when it can."""
    if row["book"] != "real":
        return (f"only the real book is edited; the {row['book']} book's fills stay as they filled "
                "(reset the book instead)")
    if row["voided_at"] is not None:
        return "this trade is voided: unvoid it first"
    if _from_action(conn, row):
        return "this entry was written by a corporate action: it cannot be edited, but it can be voided"
    return None


def check_editable(db: Database, row: dict) -> None:
    with db.connection() as conn:
        why = edit_problem(conn, row)
    if why:
        raise Refused(why)


def edit_trade(db: Database, trade_id: int, changes: dict[str, Any]) -> list[str]:
    """Apply already-validated changes to a real-book trade. Returns the fields that changed."""
    unknown = set(changes) - set(TRADE_FIELDS)
    if unknown:
        raise Refused(f"these fields cannot be edited: {', '.join(sorted(unknown))}")
    with db.connection() as conn:
        row = _trade(conn, trade_id)
        why = edit_problem(conn, row)
        if why:
            raise Refused(why)
        changed = {k: v for k, v in changes.items() if _text(v) != _text(row.get(k))}
        if not changed:
            return []
        before = _book_trades(conn, row["book_id"])
        after_row = {**row, **changed}
        after = [Trade.from_row(after_row) if t.id == trade_id else t for t in before]
        _check_no_new_shortfall(before, after)
        sets = ", ".join(f"{k} = %s" for k in changed)
        conn.execute(f"UPDATE trades SET {sets} WHERE id = %s",  # noqa: S608 — keys are TRADE_FIELDS
                     (*changed.values(), trade_id))
        for k, v in changed.items():
            _log(conn, "trade", trade_id, "edit", k, row.get(k), v)
        conn.commit()
    return list(changed)


def _partners(conn: Any, row: dict) -> list[dict]:
    """The other half of a conversion: written at the same moment in the same book, each naming the other ticker."""
    if row["side"] not in ("convert_out", "convert_in"):
        return []
    other = "convert_in" if row["side"] == "convert_out" else "convert_out"
    return conn.execute(
        "SELECT * FROM trades WHERE book_id = %s AND side = %s AND at = %s AND ticker = %s "  # includes voided
        "AND related_ticker = %s AND id <> %s FOR UPDATE",
        (row["book_id"], other, row["at"], row["related_ticker"], row["ticker"], row["id"])).fetchall()


def set_trade_void(db: Database, trade_id: int, void: bool, reason: str | None = None) -> list[int]:
    """Void (or unvoid) one real-book trade, with its conversion partner when it has one. Returns the ids changed."""
    if void and not (reason or "").strip():
        raise Refused("say why it is voided")
    with db.connection() as conn:
        row = _trade(conn, trade_id)
        if row["book"] != "real":
            raise Refused(f"only real-book trades are voided one by one; reset the {row['book']} book instead")
        if (row["voided_at"] is not None) == void:
            raise Refused(f"this trade is already {'voided' if void else 'not voided'}")
        group = [row] + [p for p in _partners(conn, row) if (p["voided_at"] is not None) != void]
        ids = {g["id"] for g in group}
        before = _book_trades(conn, row["book_id"])
        if void:
            after = [t for t in before if t.id not in ids]
        else:
            after = before + [Trade.from_row(g) for g in group]
        _check_no_new_shortfall(before, after)
        for g in group:
            if void:
                conn.execute("UPDATE trades SET voided_at = now(), void_reason = %s WHERE id = %s",
                             (reason.strip(), g["id"]))
            else:
                conn.execute("UPDATE trades SET voided_at = NULL, void_reason = NULL WHERE id = %s", (g["id"],))
            _log(conn, "trade", g["id"], "void" if void else "unvoid", reason=reason.strip() if void else None)
        conn.commit()
    return sorted(ids)


# ── cash flows ─────────────────────────────────────────────────────────────────────────────────────────────
def _flow(conn: Any, flow_id: int) -> dict:
    row = conn.execute("SELECT f.*, b.kind AS book "
                       "FROM cash_flows f JOIN books b ON b.id = f.book_id "  # includes voided
                       "WHERE f.id = %s FOR UPDATE OF f", (flow_id,)).fetchone()
    if not row:
        raise NotFound(f"no cash flow {flow_id}")
    if row["book"] != "real":
        raise Refused(f"only real-book deposits are corrected one by one; reset the {row['book']} book instead")
    return row


def edit_flow(db: Database, flow_id: int, changes: dict[str, Any]) -> list[str]:
    unknown = set(changes) - set(FLOW_FIELDS)
    if unknown:
        raise Refused(f"these fields cannot be edited: {', '.join(sorted(unknown))}")
    with db.connection() as conn:
        row = _flow(conn, flow_id)
        if row["voided_at"] is not None:
            raise Refused("this entry is voided: unvoid it first")
        changed = {k: v for k, v in changes.items() if _text(v) != _text(row.get(k))}
        if not changed:
            return []
        sets = ", ".join(f"{k} = %s" for k in changed)
        conn.execute(f"UPDATE cash_flows SET {sets} WHERE id = %s",  # noqa: S608 — keys are FLOW_FIELDS
                     (*changed.values(), flow_id))
        for k, v in changed.items():
            _log(conn, "cash_flow", flow_id, "edit", k, row.get(k), v)
        conn.commit()
    return list(changed)


def set_flow_void(db: Database, flow_id: int, void: bool, reason: str | None = None) -> None:
    if void and not (reason or "").strip():
        raise Refused("say why it is voided")
    with db.connection() as conn:
        row = _flow(conn, flow_id)
        if (row["voided_at"] is not None) == void:
            raise Refused(f"this entry is already {'voided' if void else 'not voided'}")
        if void:
            conn.execute("UPDATE cash_flows SET voided_at = now(), void_reason = %s WHERE id = %s",
                         (reason.strip(), flow_id))
        else:
            conn.execute("UPDATE cash_flows SET voided_at = NULL, void_reason = NULL WHERE id = %s", (flow_id,))
        _log(conn, "cash_flow", flow_id, "void" if void else "unvoid", reason=reason.strip() if void else None)
        conn.commit()


# ── reset a simulated book ─────────────────────────────────────────────────────────────────────────────────
def reset(db: Database, book: str, today: dt.date | None = None) -> dict[str, Any]:
    """Void every live trade and cash flow of a practice, AI or options book and cancel its open orders, in one
    transaction. The AI book takes the ghost with it. Nothing is deleted; the entries show again with "show voided"."""
    if book not in RESETTABLE:
        raise Refused(f"only the practice, AI and options books are reset, not {book}")
    day = today or dt.datetime.now(NEW_YORK).date()
    reason = f"reset {day.isoformat()}"
    kinds = [book, "ghost"] if book == "ai" else [book]
    out: dict[str, Any] = {"book": book, "reason": reason, "trades": 0, "cash_flows": 0, "orders_cancelled": 0}
    with db.connection() as conn:
        for kind in kinds:
            bid = conn.execute("SELECT id FROM books WHERE kind = %s", (kind,)).fetchone()["id"]
            for entity, table in (("trade", "trades"), ("cash_flow", "cash_flows")):
                ids = [r["id"] for r in conn.execute(
                    f"UPDATE {table} SET voided_at = now(), void_reason = %s "  # noqa: S608 — fixed table names
                    "WHERE book_id = %s AND voided_at IS NULL RETURNING id", (reason, bid)).fetchall()]
                for i in ids:
                    _log(conn, entity, i, "void", reason=reason)
                out["trades" if entity == "trade" else "cash_flows"] += len(ids)
            out["orders_cancelled"] += conn.execute(
                "UPDATE orders SET status = 'cancelled', reason = %s WHERE book_id = %s AND status = 'open'",
                (f"cancelled: book {reason}", bid)).rowcount
        conn.commit()
    if book == "ai":
        out["ghost"] = True
    return out


def last_reset(db: Database, book: str) -> str | None:
    row = db.fetch_one("SELECT max(t.void_reason) AS r "
                       "FROM trades t JOIN books b ON b.id = t.book_id "  # includes voided
                       "WHERE b.kind = %s AND t.void_reason LIKE 'reset %%'", (book,))
    row2 = db.fetch_one("SELECT max(f.void_reason) AS r "
                        "FROM cash_flows f JOIN books b ON b.id = f.book_id "  # includes voided
                        "WHERE b.kind = %s AND f.void_reason LIKE 'reset %%'", (book,))
    found = [r["r"] for r in (row, row2) if r and r["r"]]
    return max(found)[len("reset "):] if found else None
