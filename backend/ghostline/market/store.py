"""Writing pulled rows into the tables, every row with its source and as_of; reading them back for the app."""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

from ghostline.db import Database
from ghostline.market import Bar, Earning, Filing, MacroPoint, NewsItem, Quote


def save_bars(db: Database, ticker: str, timeframe: str, bars: list[Bar], source: str, as_of: dt.datetime) -> int:
    with db.connection() as conn:
        for b in bars:
            conn.execute(
                "INSERT INTO market_bars (ticker, timeframe, ts, open, high, low, close, volume, source, as_of) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (ticker, timeframe, ts) DO UPDATE SET "
                "open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close, "
                "volume = EXCLUDED.volume, source = EXCLUDED.source, as_of = EXCLUDED.as_of",
                (ticker, timeframe, b.ts, b.open, b.high, b.low, b.close, b.volume, source, as_of))
        conn.commit()
    return len(bars)


def save_quotes(db: Database, quotes: list[Quote], source: str, as_of: dt.datetime) -> int:
    with db.connection() as conn:
        for q in quotes:
            conn.execute("INSERT INTO quotes (ticker, bid, ask, last, ts, source, as_of, delayed) "
                         "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                         (q.ticker, q.bid, q.ask, q.last, q.ts, source, as_of, q.delayed))
        conn.commit()
    return len(quotes)


def save_news(db: Database, items: list[NewsItem], source: str, as_of: dt.datetime) -> int:
    n = 0
    with db.connection() as conn:
        for i in items:
            cur = conn.execute("INSERT INTO news (ticker, headline, url, published_at, source, as_of, tone) "
                               "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (ticker, url) DO NOTHING",
                               (i.ticker, i.headline, i.url, i.published_at, source, as_of, i.tone))
            n += cur.rowcount
        conn.commit()
    return n


def save_earnings(db: Database, rows: list[Earning], source: str, as_of: dt.datetime) -> int:
    with db.connection() as conn:
        for e in rows:
            conn.execute("INSERT INTO earnings (ticker, at, estimate, actual, source, as_of) "
                         "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (ticker, at) DO UPDATE SET "
                         "estimate = COALESCE(EXCLUDED.estimate, earnings.estimate), "
                         "actual = COALESCE(EXCLUDED.actual, earnings.actual), source = EXCLUDED.source, "
                         "as_of = EXCLUDED.as_of",
                         (e.ticker, e.at, e.estimate, e.actual, source, as_of))
        conn.commit()
    return len(rows)


def save_filings(db: Database, rows: list[Filing], source: str, as_of: dt.datetime) -> int:
    n = 0
    with db.connection() as conn:
        for f in rows:
            cur = conn.execute("INSERT INTO filings (accession, ticker, form, filed_at, url, source, as_of) "
                               "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (accession) DO NOTHING",
                               (f.accession, f.ticker, f.form, f.filed_at, f.url, source, as_of))
            n += cur.rowcount
        conn.commit()
    return n


def save_macro(db: Database, points: list[MacroPoint], source: str, as_of: dt.datetime) -> int:
    with db.connection() as conn:
        for p in points:
            conn.execute("INSERT INTO macro_series (series, ts, value, source, as_of) VALUES (%s, %s, %s, %s, %s) "
                         "ON CONFLICT (series, ts) DO UPDATE SET value = EXCLUDED.value, source = EXCLUDED.source, "
                         "as_of = EXCLUDED.as_of", (p.series, p.ts, p.value, source, as_of))
        conn.commit()
    return len(points)


def save_fundamentals(db: Database, ticker: str, payload: dict, source: str, as_of: dt.datetime) -> None:
    db.execute("INSERT INTO fundamentals (ticker, as_of, source, payload) VALUES (%s, %s, %s, %s::jsonb) "
               "ON CONFLICT (ticker, source, as_of) DO NOTHING",
               (ticker, as_of, source, json.dumps(payload, default=str)))


# ── reads ──────────────────────────────────────────────────────────────────────────────────────────────────
def bars(db: Database, ticker: str, timeframe: str, since: dt.datetime | None = None, limit: int = 2000) -> list[dict]:
    if since:
        rows = db.fetch_all("SELECT * FROM market_bars WHERE ticker = %s AND timeframe = %s AND ts >= %s "
                            "ORDER BY ts LIMIT %s", (ticker, timeframe, since, limit))
    else:
        rows = db.fetch_all("SELECT * FROM (SELECT * FROM market_bars WHERE ticker = %s AND timeframe = %s "
                            "ORDER BY ts DESC LIMIT %s) t ORDER BY ts", (ticker, timeframe, limit))
    return rows


def latest_quotes(db: Database, tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    rows = db.fetch_all("SELECT DISTINCT ON (ticker) * FROM quotes WHERE ticker = ANY(%s) ORDER BY ticker, as_of DESC",
                        (tickers,))
    return {r["ticker"]: r for r in rows}


def news_for(db: Database, ticker: str, limit: int = 40) -> list[dict]:
    return db.fetch_all("SELECT * FROM news WHERE ticker = %s ORDER BY published_at DESC LIMIT %s", (ticker, limit))


def earnings_for(db: Database, ticker: str) -> list[dict]:
    return db.fetch_all("SELECT * FROM earnings WHERE ticker = %s ORDER BY at DESC LIMIT 12", (ticker,))


def filings_for(db: Database, ticker: str, limit: int = 30) -> list[dict]:
    return db.fetch_all("SELECT * FROM filings WHERE ticker = %s ORDER BY filed_at DESC LIMIT %s", (ticker, limit))


def tracked_tickers(db: Database) -> list[str]:
    """Every ticker the owner holds in any book or watches, plus the underlying of every open option leg."""
    rows = db.fetch_all("SELECT ticker FROM positions UNION SELECT ticker FROM watchlist UNION "
                        "SELECT ticker FROM trades WHERE asset = 'option' GROUP BY book_id, ticker, option_symbol "
                        "HAVING SUM(CASE WHEN side = 'sell_to_open' THEN quantity ELSE -quantity END) > 0 ORDER BY 1")
    return [r["ticker"] for r in rows]


def tracked_option_symbols(db: Database) -> list[str]:
    """Every option with an open short leg or an open practice order: its quote is pulled with the stocks' (Tradier
    quotes option symbols; Alpaca does not)."""
    rows = db.fetch_all(
        "SELECT option_symbol AS s FROM trades WHERE asset = 'option' GROUP BY book_id, option_symbol "
        "HAVING SUM(CASE WHEN side = 'sell_to_open' THEN quantity ELSE -quantity END) > 0 "
        "UNION SELECT option_symbol FROM orders WHERE status = 'open' AND asset = 'option' ORDER BY 1")
    return [r["s"] for r in rows]


def jsonable(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, dt.datetime | dt.date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def as_json(rows: list[dict]) -> list[dict[str, Any]]:
    return [jsonable(r) for r in rows]
