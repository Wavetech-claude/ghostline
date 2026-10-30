"""The latest stored price per ticker, with its source and time. Phase 2 fills the `quotes` table; until it exists
every price is unknown, and the app says so instead of showing a number."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ghostline.db import Database


@dataclass(frozen=True)
class Price:
    ticker: str
    price: Decimal
    source: str
    as_of: Any


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
