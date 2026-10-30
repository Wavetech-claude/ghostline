"""A book's value over time and its time-weighted return. Each weekday since the book's first flow: cash plus every
stock holding at that day's stored close (the last close before it; the last fill when no bar is stored). Deposits
and withdrawals count at the start of their day, so they never look like gains or losses. Short puts carry their
premium as cash and are not marked to market (stored option prices are snapshots, not a daily series)."""

from __future__ import annotations

import bisect
import datetime as dt
from collections import defaultdict
from decimal import Decimal
from typing import Any

from ghostline.db import Database

CENT = Decimal("0.01")


def _close_on(series: tuple[list[dt.date], list[Decimal]], day: dt.date) -> Decimal | None:
    days, px = series
    i = bisect.bisect_right(days, day) - 1
    return px[i] if i >= 0 else None


def equity_series(db: Database, kind: str, end: dt.date | None = None) -> dict[str, Any]:
    book = db.fetch_one("SELECT id FROM books WHERE kind = %s", (kind,))
    if not book:
        return {"book": kind, "points": [], "twr_pct": None}
    bid = book["id"]
    flows = db.fetch_all("SELECT amount, at FROM cash_flows WHERE book_id = %s ORDER BY at, id", (bid,))
    trades = db.fetch_all(
        "SELECT ticker, asset, side, quantity, fill, fees, at FROM trades WHERE book_id = %s ORDER BY at, id", (bid,)
    )
    if not flows:
        return {"book": kind, "points": [], "twr_pct": None}
    end = end or dt.date.today()
    start = flows[0]["at"].date()
    tickers = sorted({t["ticker"] for t in trades if t["asset"] == "stock"})
    closes: dict[str, tuple[list[dt.date], list[Decimal]]] = {}
    for tk in tickers:
        rows = db.fetch_all(
            "SELECT ts::date AS d, close FROM market_bars WHERE ticker = %s AND timeframe = '1D' "
            "AND ts::date <= %s ORDER BY ts",
            (tk, end),
        )
        closes[tk] = ([r["d"] for r in rows], [Decimal(r["close"]) for r in rows])
    flow_by_day: dict[dt.date, Decimal] = defaultdict(Decimal)
    for f in flows:
        flow_by_day[f["at"].date()] += Decimal(f["amount"])
    trades_by_day: dict[dt.date, list[dict]] = defaultdict(list)
    for t in trades:
        trades_by_day[t["at"].date()].append(t)

    spy_rows = db.fetch_all("SELECT ts::date AS d, close FROM market_bars WHERE ticker = 'SPY' AND timeframe = '1D' "
                            "AND ts::date <= %s ORDER BY ts", (end,))
    spy = ([r["d"] for r in spy_rows], [Decimal(r["close"]) for r in spy_rows])
    spy_start: Decimal | None = None
    cash = Decimal(0)
    held: dict[str, Decimal] = defaultdict(Decimal)
    last_fill: dict[str, Decimal] = {}
    points: list[dict[str, Any]] = []
    growth, prev_value = Decimal(1), None
    # flows and trades dated on a weekend count on the next weekday
    pending_flow = Decimal(0)
    pending_trades: list[dict] = []
    d = start
    while d <= end:
        pending_flow += flow_by_day.get(d, Decimal(0))
        pending_trades += trades_by_day.get(d, [])
        if d.weekday() >= 5:
            d += dt.timedelta(days=1)
            continue
        flow = pending_flow
        cash += flow
        for t in pending_trades:
            m = Decimal(100) if t["asset"] == "option" else Decimal(1)
            amount = Decimal(t["quantity"]) * Decimal(t["fill"]) * m
            fees = Decimal(t["fees"] or 0)
            if t["side"] in ("buy", "buy_to_close"):
                cash -= amount + fees
            else:
                cash += amount - fees
            if t["asset"] == "stock":
                held[t["ticker"]] += Decimal(t["quantity"]) if t["side"] == "buy" else -Decimal(t["quantity"])
                last_fill[t["ticker"]] = Decimal(t["fill"])
        pending_flow, pending_trades = Decimal(0), []
        value = cash
        for tk, q in held.items():
            if q == 0:
                continue
            days, px = closes.get(tk, ([], []))
            i = bisect.bisect_right(days, d) - 1
            price = px[i] if i >= 0 else last_fill.get(tk, Decimal(0))
            value += q * price
        value = value.quantize(CENT)
        if prev_value is not None and prev_value + flow > 0:
            growth *= value / (prev_value + flow)
        elif prev_value is None and flow > 0:
            growth *= value / flow
        spy_now = _close_on(spy, d)
        if spy_start is None:
            spy_start = spy_now
        points.append(
            {
                "date": d.isoformat(),
                "value": str(value),
                "net_flow": str(flow.quantize(CENT)),
                "twr_pct": str(((growth - 1) * 100).quantize(CENT)),
                "spy_pct": None if spy_now is None or not spy_start
                else str(((spy_now / spy_start - 1) * 100).quantize(CENT)),
            }
        )
        prev_value = value
        d += dt.timedelta(days=1)
    return {"book": kind, "points": points, "twr_pct": points[-1]["twr_pct"] if points else None}
