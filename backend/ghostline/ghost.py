"""The S&P 500 ghost: every deposit or withdrawal on the AI trader's book is mirrored on the ghost book on the same
date and turned into SPY at that date's stored close. Beating the ghost over time is the test of whether the AI adds
anything. A flow whose date has no stored SPY price yet waits for the next run; nothing is priced by guess."""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from ghostline import ledger
from ghostline.db import Database

logger = logging.getLogger("ghostline.ghost")
FUND = "SPY"
SHARES = Decimal("0.000001")


class Ghost:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _book(self, kind: str) -> int:
        row = self.db.fetch_one("SELECT id FROM books WHERE kind = %s", (kind,))
        if not row:
            raise ValueError(f"no book {kind}")
        return int(row["id"])

    def price_on(self, day: dt.date) -> tuple[Decimal, str, dt.date] | None:
        """SPY's close on the day, else the last close before it (a weekend or holiday deposit takes Friday's close).
        A close more than five days older than the day is not used: the bars are behind, so the flow waits."""
        bar = self.db.fetch_one(
            "SELECT close, source, ts FROM market_bars WHERE ticker = %s AND timeframe = '1D' AND ts::date <= %s "
            "ORDER BY ts DESC LIMIT 1",
            (FUND, day),
        )
        if bar and (day - bar["ts"].date()).days <= 5:
            return Decimal(bar["close"]), bar["source"], bar["ts"].date()
        if day >= dt.date.today():
            q = self.db.fetch_one(
                "SELECT last, bid, ask, source, ts FROM quotes WHERE ticker = %s ORDER BY as_of DESC LIMIT 1", (FUND,)
            )
            if q and q["ts"].date() >= day - dt.timedelta(days=5):
                px = (
                    q["last"]
                    if q["last"] is not None
                    else ((q["bid"] + q["ask"]) / 2 if q["bid"] is not None and q["ask"] is not None else None)
                )
                if px is not None:
                    return Decimal(px), q["source"], q["ts"].date()
        return None

    def held(self, ghost_id: int) -> Decimal:
        row = self.db.fetch_one("SELECT quantity FROM positions WHERE book_id = %s AND ticker = %s", (ghost_id, FUND))
        return Decimal(row["quantity"]) if row else Decimal(0)

    def sync(self) -> dict[str, Any]:
        """Mirror every AI flow that has no ghost twin yet, oldest first."""
        ai_id, ghost_id = self._book("ai"), self._book("ghost")
        flows = self.db.fetch_all(
            "SELECT f.* FROM cash_flows f LEFT JOIN ghost_links g ON g.ai_flow_id = f.id "
            "WHERE f.book_id = %s AND g.ai_flow_id IS NULL ORDER BY f.at, f.id",
            (ai_id,),
        )
        out = {"mirrored": 0, "waiting": 0}
        for f in flows:
            day = f["at"].date()
            priced = self.price_on(day)
            if priced is None:
                out["waiting"] += 1
                logger.info("ghost: AI flow %s on %s waits for an SPY price", f["id"], day)
                continue
            price, source, price_date = priced
            amount = Decimal(f["amount"])
            with self.db.connection() as conn:
                g = conn.execute(
                    "INSERT INTO cash_flows (book_id, amount, at, note) VALUES (%s, %s, %s, %s) RETURNING id",
                    (ghost_id, amount, f["at"], f"mirrors AI trader flow #{f['id']}"),
                ).fetchone()
                if amount > 0:
                    side, qty = "buy", (amount / price).quantize(SHARES)
                else:
                    held = self.held(ghost_id)
                    side, qty = "sell", min((-amount / price).quantize(SHARES), held)
                trade_id = None
                if qty > 0:
                    t = conn.execute(
                        "INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at, fees, broker, note) "
                        "VALUES (%s, %s, 'stock', %s, %s, %s, %s, 0, 'ghost', %s) RETURNING id",
                        (
                            ghost_id,
                            FUND,
                            side,
                            qty,
                            price,
                            f["at"],
                            f"{side} {FUND} with AI flow #{f['id']} ({ledger.money(amount)}) at the {price_date} close "
                            f"({source})",
                        ),
                    ).fetchone()
                    trade_id = t["id"]
                conn.execute(
                    "INSERT INTO ghost_links (ai_flow_id, ghost_flow_id, trade_id, price, price_source, "
                    "price_date) VALUES (%s, %s, %s, %s, %s, %s)",
                    (f["id"], g["id"], trade_id, price, source, price_date),
                )
                conn.commit()
            out["mirrored"] += 1
        return out
