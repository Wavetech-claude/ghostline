"""The latest stored price per ticker, with its source and time. Phase 2 fills the `quotes` table; until it exists
every price is unknown, and the app says so instead of showing a number."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from ghostline.db import Database


@dataclass(frozen=True)
class Price:
    ticker: str
    price: Decimal
    source: str
    as_of: Any


NEW_YORK = ZoneInfo("America/New_York")


def session_day(ts: dt.datetime) -> dt.date:
    """The New York trading date of a moment. Quotes are pulled on weekdays during the session (the worker's
    schedule), so a quote's New York date is its session."""
    return ts.astimezone(NEW_YORK).date()


def previous_closes(db: Database, sessions: dict[str, dt.date]) -> dict[str, Decimal]:
    """Per ticker, the last stored daily close strictly before its session. A bar's UTC date is its session date (both
    data providers stamp a daily bar at New York midnight; the database role runs in UTC)."""
    if not sessions:
        return {}
    tickers = list(sessions)
    rows = db.fetch_all(
        "SELECT DISTINCT ON (b.ticker) b.ticker, b.close FROM market_bars b "
        "JOIN unnest(%s::text[], %s::date[]) AS w (ticker, day) ON w.ticker = b.ticker "
        "WHERE b.timeframe = '1D' AND b.ts::date < w.day ORDER BY b.ticker, b.ts DESC",
        (tickers, [sessions[t] for t in tickers]))
    return {r["ticker"]: Decimal(r["close"]) for r in rows}


def latest(db: Database, tickers: list[str]) -> dict[str, Price]:
    if not tickers:
        return {}
    exists = db.fetch_one("SELECT 1 AS ok FROM information_schema.tables WHERE table_name = 'quotes'")
    if not exists:
        return {}
    rows = db.fetch_all(
        "SELECT DISTINCT ON (ticker) ticker, COALESCE(last, (bid + ask) / 2) AS price, source, as_of FROM quotes "
        "WHERE ticker = ANY(%s) ORDER BY ticker, as_of DESC", (tickers,))
    return {r["ticker"]: Price(r["ticker"], Decimal(r["price"]), r["source"], r["as_of"]) for r in rows
            if r.get("price") is not None}
