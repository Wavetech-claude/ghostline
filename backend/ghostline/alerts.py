"""Price alerts: above or below a price, a day move of at least a percent, crossing a moving average, earnings within
a number of days. The worker checks them every 30 minutes in the session; a fired alert pushes once (production;
staging logs) and stays fired until the owner turns it back on. A ticker with no stored price is not checked."""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from ghostline import indicators
from ghostline.claude import push
from ghostline.config import Settings
from ghostline.db import Database
from ghostline.market import store
from ghostline.practice import Practice

logger = logging.getLogger("ghostline.alerts")
KINDS = ("above", "below", "pct_move", "ma_cross", "earnings")


def evaluate(alert: dict, price: Decimal | None, closes: list[float], days_to_earnings: int | None) -> str | None:
    """The message when the alert fires now, else None."""
    t, v, kind = alert["ticker"], Decimal(alert["value"]), alert["kind"]
    if kind == "earnings":
        if days_to_earnings is not None and 0 <= days_to_earnings <= v:
            return f"{t} reports earnings in {days_to_earnings} day{'s' if days_to_earnings != 1 else ''}"
        return None
    if price is None:
        return None
    if kind == "above" and price >= v:
        return f"{t} is at {price}, at or above {v}"
    if kind == "below" and price <= v:
        return f"{t} is at {price}, at or below {v}"
    if kind == "pct_move" and closes:
        prev = Decimal(str(closes[-1]))
        if prev > 0:
            move = (price / prev - 1) * 100
            if abs(move) >= v:
                return f"{t} moved {move.quantize(Decimal('0.01'))}% from the last close {prev} to {price}"
        return None
    if kind == "ma_cross":
        n = int(v)
        if len(closes) < n + 1:
            return None
        sma = indicators.sma(closes, n)
        before, ma_before = closes[-1], sma[-1]
        series_now = closes + [float(price)]
        ma_now = indicators.sma(series_now, n)[-1]
        if ma_before is None or ma_now is None:
            return None
        if (before - ma_before) * (float(price) - ma_now) < 0:
            side = "above" if float(price) > ma_now else "below"
            return f"{t} crossed {side} its {n}-day average ({ma_now:.2f}) at {price}"
    return None


def check(db: Database, settings: Settings, pusher=push.send) -> dict[str, Any]:
    p = Practice(db)
    out: dict[str, Any] = {"checked": 0, "fired": [], "no_price": 0}
    for a in db.fetch_all("SELECT * FROM alerts WHERE status = 'active' ORDER BY id"):
        price = p.latest_price(a["ticker"])
        bars = store.bars(db, a["ticker"], "1D", limit=260)
        today = dt.date.today()
        closes = [float(b["close"]) for b in bars if b["ts"].date() < today]
        msg = evaluate(a, price, closes, p._days_to_earnings(a["ticker"]))
        db.execute("UPDATE alerts SET last_checked = now() WHERE id = %s", (a["id"],))
        out["checked"] += 1
        if price is None and a["kind"] != "earnings":
            out["no_price"] += 1
        if not msg:
            continue
        db.execute(
            "UPDATE alerts SET status = 'triggered', triggered_at = now(), message = %s WHERE id = %s", (msg, a["id"])
        )
        sent = pusher(
            db, settings, f"{a['ticker']} alert", msg, {"kind": "alert", "alert_id": a["id"], "ticker": a["ticker"]}
        )
        out["fired"].append({"id": a["id"], "message": msg, "push": sent})
    return out
