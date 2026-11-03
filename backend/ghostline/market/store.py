"""Writing pulled rows into the tables, every row with its source and as_of; reading them back for the app."""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ghostline.db import Database
from ghostline.market import Bar, CorporateAction, Earning, Filing, MacroPoint, MarketSession, NewsItem, Quote


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


INTRADAY = ("5Min", "30Min")


def prune_intraday(db: Database, ticker: str, keep_days: int) -> int:
    """Remove one ticker's intraday bars older than keep_days (daily bars are never touched)."""
    return db.execute("DELETE FROM market_bars WHERE ticker = %s AND timeframe = ANY(%s) "
                      "AND ts < now() - make_interval(days => %s)", (ticker, list(INTRADAY), keep_days))


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
            conn.execute("INSERT INTO earnings (ticker, at, estimate, actual, hour, revenue_estimate, revenue_actual, "
                         "source, as_of) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                         "ON CONFLICT (ticker, at) DO UPDATE SET "
                         "estimate = COALESCE(EXCLUDED.estimate, earnings.estimate), "
                         "actual = COALESCE(EXCLUDED.actual, earnings.actual), "
                         "hour = COALESCE(EXCLUDED.hour, earnings.hour), "
                         "revenue_estimate = COALESCE(EXCLUDED.revenue_estimate, earnings.revenue_estimate), "
                         "revenue_actual = COALESCE(EXCLUDED.revenue_actual, earnings.revenue_actual), "
                         "source = EXCLUDED.source, as_of = EXCLUDED.as_of",
                         (e.ticker, e.at, e.estimate, e.actual, e.hour, e.revenue_estimate, e.revenue_actual,
                          source, as_of))
        conn.commit()
    return len(rows)


def idea_tickers(db: Database) -> list[str]:
    """The stocks of ideas still on offer (proposed) or taken and running (open)."""
    rows = db.fetch_all("SELECT DISTINCT ticker FROM ideas WHERE status IN ('proposed', 'open') ORDER BY 1")
    return [r["ticker"] for r in rows]


def held_tickers(db: Database) -> set[str]:
    return {r["ticker"] for r in db.fetch_all("SELECT DISTINCT ticker FROM positions")}


def watched_tickers(db: Database) -> set[str]:
    return {r["ticker"] for r in db.fetch_all("SELECT ticker FROM watchlist")}


def ticker_names(db: Database, tickers: list[str]) -> dict[str, str]:
    if not tickers:
        return {}
    rows = db.fetch_all("SELECT ticker, name FROM tickers WHERE ticker = ANY(%s)", (tickers,))
    return {r["ticker"]: r["name"] for r in rows}


def earnings_between(db: Database, tickers: list[str], since: dt.date, until: dt.date) -> list[dict]:
    if not tickers:
        return []
    return db.fetch_all("SELECT * FROM earnings WHERE ticker = ANY(%s) AND at BETWEEN %s AND %s ORDER BY at, ticker",
                        (tickers, since, until))


def past_earnings(db: Database, tickers: list[str], before: dt.date, per_ticker: int = 4) -> dict[str, list[dt.date]]:
    """Each stock's last report dates before a day, newest first, at most `per_ticker` each."""
    if not tickers:
        return {}
    rows = db.fetch_all("SELECT ticker, at FROM (SELECT ticker, at, row_number() OVER (PARTITION BY ticker ORDER BY at "
                        "DESC) AS n FROM earnings WHERE ticker = ANY(%s) AND at < %s) x WHERE n <= %s ORDER BY ticker, "
                        "at DESC", (tickers, before, per_ticker))
    out: dict[str, list[dt.date]] = {}
    for r in rows:
        out.setdefault(r["ticker"], []).append(r["at"])
    return out


def analyst_pulled_at(db: Database, ticker: str, source: str) -> dt.datetime | None:
    row = db.fetch_one("SELECT at FROM analyst_pulls WHERE ticker = %s AND source = %s", (ticker, source))
    return row["at"] if row else None


def mark_analyst_pull(db: Database, ticker: str, source: str, rows: int) -> None:
    db.execute("INSERT INTO analyst_pulls (ticker, source, at, rows) VALUES (%s, %s, now(), %s) "
               "ON CONFLICT (ticker, source) DO UPDATE SET at = now(), rows = EXCLUDED.rows", (ticker, source, rows))


def save_analyst_trends(db: Database, ticker: str, rows: list[dict], source: str, as_of: dt.datetime) -> int:
    with db.connection() as conn:
        for r in rows:
            conn.execute("INSERT INTO analyst_trends (ticker, period, strong_buy, buy, hold, sell, strong_sell, "
                         "source, as_of) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                         "ON CONFLICT (ticker, period) DO UPDATE SET "
                         "strong_buy = EXCLUDED.strong_buy, buy = EXCLUDED.buy, hold = EXCLUDED.hold, "
                         "sell = EXCLUDED.sell, strong_sell = EXCLUDED.strong_sell, source = EXCLUDED.source, "
                         "as_of = EXCLUDED.as_of",
                         (ticker, r["period"], r["strong_buy"], r["buy"], r["hold"], r["sell"], r["strong_sell"],
                          source, as_of))
        conn.commit()
    return len(rows)


def save_analyst_consensus(db: Database, ticker: str, grades: dict | None, targets: dict | None, source: str,
                           as_of: dt.datetime) -> None:
    """One row per stock; a part that did not answer keeps what was stored."""
    g, t = grades or {}, targets or {}
    db.execute("INSERT INTO analyst_consensus (ticker, strong_buy, buy, hold, sell, strong_sell, consensus, "
               "target_high, target_low, target_consensus, target_median, source, as_of) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (ticker) DO UPDATE SET "
               "strong_buy = COALESCE(EXCLUDED.strong_buy, analyst_consensus.strong_buy), "
               "buy = COALESCE(EXCLUDED.buy, analyst_consensus.buy), "
               "hold = COALESCE(EXCLUDED.hold, analyst_consensus.hold), "
               "sell = COALESCE(EXCLUDED.sell, analyst_consensus.sell), "
               "strong_sell = COALESCE(EXCLUDED.strong_sell, analyst_consensus.strong_sell), "
               "consensus = COALESCE(EXCLUDED.consensus, analyst_consensus.consensus), "
               "target_high = COALESCE(EXCLUDED.target_high, analyst_consensus.target_high), "
               "target_low = COALESCE(EXCLUDED.target_low, analyst_consensus.target_low), "
               "target_consensus = COALESCE(EXCLUDED.target_consensus, analyst_consensus.target_consensus), "
               "target_median = COALESCE(EXCLUDED.target_median, analyst_consensus.target_median), "
               "source = EXCLUDED.source, as_of = EXCLUDED.as_of",
               (ticker, g.get("strong_buy"), g.get("buy"), g.get("hold"), g.get("sell"), g.get("strong_sell"),
                g.get("consensus"), t.get("target_high"), t.get("target_low"), t.get("target_consensus"),
                t.get("target_median"), source, as_of))


def analysts_for(db: Database, ticker: str, months: int = 4) -> dict:
    trends = db.fetch_all("SELECT period, strong_buy, buy, hold, sell, strong_sell, source, as_of FROM analyst_trends "
                          "WHERE ticker = %s ORDER BY period DESC LIMIT %s", (ticker, months))
    consensus = db.fetch_one("SELECT * FROM analyst_consensus WHERE ticker = %s", (ticker,))
    return {"trends": trends, "consensus": consensus}


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


def save_sessions(db: Database, sessions: list[MarketSession], start: dt.date, end: dt.date, source: str,
                  as_of: dt.datetime) -> int:
    """The calendar from start to end, replaced as one: a day the answer no longer holds (a closure announced later)
    stops being a session."""
    with db.connection() as conn:
        conn.execute("DELETE FROM market_sessions WHERE day BETWEEN %s AND %s", (start, end))
        for s in sessions:
            conn.execute("INSERT INTO market_sessions (day, opens_at, closes_at, settles_on, source, as_of) "
                         "VALUES (%s, %s, %s, %s, %s, %s)",
                         (s.day, s.opens_at, s.closes_at, s.settles_on, source, as_of))
        conn.commit()
    return len(sessions)


def save_actions(db: Database, actions: list[CorporateAction], source: str, as_of: dt.datetime) -> int:
    with db.connection() as conn:
        for a in actions:
            conn.execute("INSERT INTO corporate_actions (id, kind, ticker, ex_date, record_date, payable_date, "
                         "old_rate, new_rate, rate, new_ticker, source, as_of) "
                         "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                         "ON CONFLICT (id) DO UPDATE SET payable_date = EXCLUDED.payable_date, rate = EXCLUDED.rate, "
                         "old_rate = EXCLUDED.old_rate, new_rate = EXCLUDED.new_rate, "
                         "new_ticker = EXCLUDED.new_ticker, as_of = EXCLUDED.as_of",
                         (a.id, a.kind, a.ticker, a.ex_date, a.record_date, a.payable_date, a.old_rate, a.new_rate,
                          a.rate, a.new_ticker, source, as_of))
        conn.commit()
    return len(actions)


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
    """Every ticker the owner holds in any book, watches or has an active price alert on, plus the underlying of
    every open option leg."""
    rows = db.fetch_all("SELECT ticker FROM positions UNION SELECT ticker FROM watchlist UNION "
                        "SELECT ticker FROM alerts WHERE status = 'active' UNION "
                        "SELECT ticker FROM live_trades WHERE asset = 'option' GROUP BY book_id, ticker, option_symbol "
                        "HAVING SUM(CASE WHEN side = 'sell_to_open' THEN quantity ELSE -quantity END) > 0 ORDER BY 1")
    return [r["ticker"] for r in rows]


MARKET_TICKERS = ("SPY", "RSP", "IWM", "HYG", "LQD", "TLT", "XLY", "XLP")   # the regime's ratios and the ghost's SPY


def scan_tickers(db: Database) -> list[str]:
    """What the daily bars and earnings pulls cover: the tracked tickers, the market ETFs, and today's universe."""
    rows = db.fetch_all("SELECT ticker FROM scan_universe WHERE day >= CURRENT_DATE - 1")
    found = set(tracked_tickers(db)) | set(MARKET_TICKERS) | {r["ticker"] for r in rows}
    return sorted(found)


def save_universe(db: Database, day: dt.date, rows: list[dict]) -> int:
    n = 0
    with db.connection() as conn:
        for r in rows:
            conn.execute("INSERT INTO scan_universe (ticker, day, reason, dollar_volume, change_pct) "
                         "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (ticker, day) DO NOTHING",
                         (r["ticker"], day, r["reason"], r.get("dollar_volume"), r.get("change_pct")))
            n += 1
        conn.commit()
    return n


def save_chain(db: Database, raw: list[dict], as_of: dt.datetime, source: str = "tradier") -> list[dict]:
    """Parse a chain (Tradier's shape; Alpaca's is converted to it), store every option with its bid, ask, open
    interest, IV and delta, and keep each option's bid and ask as its quote (a practice order fills against it).
    Returns the parsed rows."""
    from ghostline import occ  # late: occ has no dependency on the store
    from ghostline.market import dec

    rows: list[dict] = []
    for o in raw or []:
        try:
            parsed = occ.parse(str(o.get("symbol") or ""))
        except ValueError:
            continue
        greeks = o.get("greeks") or {}
        rows.append({"option_symbol": parsed.symbol, "underlying": parsed.underlying, "expiry": parsed.expiry,
                     "strike": parsed.strike, "kind": parsed.kind, "bid": dec(o.get("bid")), "ask": dec(o.get("ask")),
                     "last": dec(o.get("last")), "volume": dec(o.get("volume")),
                     "open_interest": dec(o.get("open_interest")),
                     "iv": dec(greeks.get("mid_iv") or greeks.get("smv_vol")), "delta": dec(greeks.get("delta"))})
    if rows:
        with db.connection() as conn:
            for r in rows:
                conn.execute("INSERT INTO option_chains (option_symbol, underlying, expiry, strike, kind, bid, ask, "
                             "last, volume, open_interest, iv, delta, source, as_of) VALUES (%s, %s, %s, %s, %s, %s, "
                             "%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                             (r["option_symbol"], r["underlying"], r["expiry"], r["strike"], r["kind"], r["bid"],
                              r["ask"], r["last"], r["volume"], r["open_interest"], r["iv"], r["delta"], source, as_of))
            conn.commit()
        save_quotes(db, [Quote(r["option_symbol"], r["bid"], r["ask"], r["last"], as_of, True) for r in rows],
                    source, as_of)
        for r in rows:
            r["source"] = source
    return rows


def stored_chain(db: Database, ticker: str, expiry: dt.date) -> list[dict]:
    """The latest stored snapshot of each option of one expiry (production's copy, on staging)."""
    return db.fetch_all("SELECT DISTINCT ON (option_symbol) * FROM option_chains WHERE underlying = %s AND expiry = %s "
                        "ORDER BY option_symbol, as_of DESC", (ticker, expiry))


def stored_expirations(db: Database, ticker: str, since: dt.date) -> list[dt.date]:
    return [r["expiry"] for r in db.fetch_all("SELECT DISTINCT expiry FROM option_chains WHERE underlying = %s "
                                              "AND expiry >= %s ORDER BY expiry", (ticker, since))]


def spot_of(db: Database, ticker: str) -> float | None:
    """A stock's latest price for option math: its latest quote's mid (or last), else its last daily close."""
    q = latest_quotes(db, [ticker]).get(ticker)
    if q:
        bid, ask, last = q.get("bid"), q.get("ask"), q.get("last")
        if bid and ask:
            return float((bid + ask) / 2)
        if last:
            return float(last)
    b = bars(db, ticker, "1D", limit=1)
    return float(b[-1]["close"]) if b else None


def put_candidates(db: Database) -> list[str]:
    """Stocks the owner would happily own (the requirements' rule for selling puts): what the owner holds or
    watches, and the tickers of open long-term ideas."""
    rows = db.fetch_all("SELECT ticker FROM positions UNION SELECT ticker FROM watchlist UNION "
                        "SELECT ticker FROM ideas WHERE lane = 'long' AND status = 'open' AND expires_at > now() "
                        "ORDER BY 1")
    return [r["ticker"] for r in rows if r["ticker"] not in MARKET_TICKERS]


def tracked_option_symbols(db: Database) -> list[str]:
    """Every option with an open short leg or an open practice order: its quote is pulled with the stocks' (Tradier's
    quotes take option symbols; without Tradier, Alpaca's indicative option snapshots price them)."""
    rows = db.fetch_all(
        "SELECT option_symbol AS s FROM live_trades WHERE asset = 'option' GROUP BY book_id, option_symbol "
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


def save_tickers(db: Database, rows: list[tuple[str, str, str | None]], source: str, as_of: dt.datetime) -> int:
    """The ticker directory, refreshed in place (one row per ticker)."""
    with db.connection() as conn:
        for ticker, name, cik in rows:
            conn.execute("INSERT INTO tickers (ticker, name, cik, source, as_of) VALUES (%s, %s, %s, %s, %s) "
                         "ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name, cik = EXCLUDED.cik, "
                         "source = EXCLUDED.source, as_of = EXCLUDED.as_of", (ticker, name, cik, source, as_of))
        conn.commit()
    return len(rows)


HIDDEN_KINDS = ("WARRANT", "RIGHT", "UNIT")      # left out of search unless asked for


def search_tickers(db: Database, q: str, limit: int = 20, include_all: bool = False) -> list[dict]:
    """Search as you type, Robinhood style. A stock matches when its ticker starts with the text or a word of its name
    does ("n" finds Nvidia, Netflix and Nike; "net" finds Netflix). Order: what the owner holds or watches, then the
    exact ticker, then the most traded (20-day average dollar volume), then the ticker. Warrants, rights and units are
    left out unless `include_all`; ETFs are in. A brand the company's name lacks ("google" for Alphabet) matches by
    the prefix of its alias (ticker_aliases)."""
    q = q.strip()
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = escaped.upper() + "%"
    word = r"\m" + re.escape(q)                     # \m: the start of a word, in PostgreSQL's regular expressions
    return db.fetch_all(
        "SELECT t.ticker, t.name, t.kind, (w.ticker IS NOT NULL) AS watched, (p.ticker IS NOT NULL) AS held "
        "FROM tickers t LEFT JOIN watchlist w ON w.ticker = t.ticker "
        "LEFT JOIN (SELECT DISTINCT ticker FROM positions) p ON p.ticker = t.ticker "
        "WHERE (t.ticker LIKE %s OR t.name ~* %s "
        "OR EXISTS (SELECT 1 FROM ticker_aliases a WHERE a.ticker = t.ticker AND a.alias LIKE %s)) "
        "AND (%s OR t.kind IS NULL OR NOT (t.kind = ANY(%s))) "
        "ORDER BY (w.ticker IS NOT NULL OR p.ticker IS NOT NULL) DESC, (t.ticker = %s) DESC, "
        "t.dollar_volume_20d DESC NULLS LAST, t.ticker LIMIT %s",
        (like, word, escaped.lower() + "%", include_all, list(HIDDEN_KINDS), q.upper(), limit))


def save_ticker_reference(db: Database, rows: list[tuple[str, str, str | None, str | None]], source: str,
                          as_of: dt.datetime) -> int:
    """Massive's ticker list: each ticker's type, and the tickers the SEC's company list lacks (most ETFs). A name the
    SEC gave is kept."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO tickers (ticker, name, cik, kind, source, as_of) "
                            "VALUES (%s, %s, %s, %s, %s, %s) "
                            "ON CONFLICT (ticker) DO UPDATE SET kind = EXCLUDED.kind, "
                            "cik = COALESCE(tickers.cik, EXCLUDED.cik)",
                            [(t, name, cik, kind, source, as_of) for t, name, kind, cik in rows])
        conn.commit()
    return len(rows)


def mark_suffix_kinds(db: Database) -> int:
    """Tickers no list typed: a warrant, right or unit by its symbol (ABCDW, ABCDWS, ABCDR, ABCDU, ABC-WT...), only when
    the stock it belongs to is listed too, so a real ticker ending in W, R or U is not hidden."""
    rules = (("WARRANT", r"^[A-Z]{3,5}W$", 1), ("WARRANT", r"^[A-Z]{2,5}WS$", 2), ("RIGHT", r"^[A-Z]{3,5}R$", 1),
             ("UNIT", r"^[A-Z]{3,5}U$", 1), ("WARRANT", r"^[A-Z]{1,5}-WTS?$", 0), ("RIGHT", r"^[A-Z]{1,5}-RTS?$", 0),
             ("UNIT", r"^[A-Z]{1,5}-UNS?$", 0))
    n = 0
    for kind, pattern, cut in rules:
        if cut:
            n += db.execute("UPDATE tickers t SET kind = %s WHERE t.kind IS NULL AND t.ticker ~ %s AND EXISTS "
                            "(SELECT 1 FROM tickers b WHERE b.ticker = left(t.ticker, -%s))", (kind, pattern, cut))
        else:
            n += db.execute("UPDATE tickers SET kind = %s WHERE kind IS NULL AND ticker ~ %s", (kind, pattern))
    return n


def save_movers(db: Database, kind: str, rows: list[dict], source: str, as_of: dt.datetime) -> int:
    """One list (gainers, losers or active) replaced whole: the newest answer is the only one kept."""
    with db.connection() as conn:
        conn.execute("DELETE FROM market_movers WHERE kind = %s", (kind,))
        for i, r in enumerate(rows, 1):
            conn.execute("INSERT INTO market_movers (kind, rank, ticker, price, change, change_pct, volume, "
                         "trade_count, source, as_of) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                         (kind, i, str(r["symbol"]).upper(), r.get("price"), r.get("change"), r.get("percent_change"),
                          r.get("volume"), r.get("trade_count"), source, as_of))
        conn.commit()
    return len(rows)


def industry_due(db: Database, fresh_days: int, min_dollar_volume: Decimal, limit: int) -> list[str]:
    """Traded common stocks (and ADRs) whose industry is unknown or a week old, the most traded first."""
    rows = db.fetch_all("SELECT ticker FROM tickers WHERE kind IN ('CS', 'ADRC') AND dollar_volume_20d >= %s "
                        "AND (industry_at IS NULL OR industry_at < now() - make_interval(days => %s)) "
                        "ORDER BY industry_at NULLS FIRST, dollar_volume_20d DESC LIMIT %s",
                        (min_dollar_volume, fresh_days, limit))
    return [r["ticker"] for r in rows]


def save_industry(db: Database, ticker: str, industry: str | None) -> None:
    db.execute("UPDATE tickers SET industry = %s, industry_at = now() WHERE ticker = %s", (industry, ticker))


@dataclass(frozen=True)
class DayBar:
    """One ticker's session from the stored whole-market file."""
    open: Decimal
    close: Decimal
    volume: Decimal


def save_market_daily(db: Database, day: dt.date, grouped: dict[str, Bar], source: str, as_of: dt.datetime) -> int:
    rows = [(day, t, b.open, b.close, b.volume, source, as_of) for t, b in grouped.items() if b.close is not None]
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO market_daily (day, ticker, open, close, volume, source, as_of) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (day, ticker) DO UPDATE SET "
                            "open = EXCLUDED.open, close = EXCLUDED.close, volume = EXCLUDED.volume, "
                            "source = EXCLUDED.source, as_of = EXCLUDED.as_of", rows)
        conn.commit()
    return len(rows)


def market_days(db: Database) -> set[dt.date]:
    return {r["day"] for r in db.fetch_all("SELECT DISTINCT day FROM market_daily")}


def market_close(db: Database, ticker: str, day: dt.date) -> Decimal | None:
    """One ticker's close on one day from the whole market's daily file: every listed stock has it, not only the ones
    the daily pull tracks."""
    row = db.fetch_one("SELECT close FROM market_daily WHERE ticker = %s AND day = %s", (ticker, day))
    return row["close"] if row else None


def market_day(db: Database, day: dt.date) -> dict[str, DayBar]:
    rows = db.fetch_all("SELECT ticker, open, close, volume FROM market_daily WHERE day = %s", (day,))
    return {r["ticker"]: DayBar(r["open"] or r["close"], r["close"], r["volume"]) for r in rows}


def prune_market_daily(db: Database, before: dt.date) -> int:
    return db.execute("DELETE FROM market_daily WHERE day < %s", (before,))


def refresh_popularity(db: Database, sessions: int = 20) -> int:
    """Each ticker's average dollar volume (close x volume) over the last `sessions` stored sessions; a ticker that
    did not trade in them has none."""
    n = db.execute("WITH d AS (SELECT DISTINCT day FROM market_daily ORDER BY day DESC LIMIT %s), "
                   "v AS (SELECT ticker, round(avg(close * volume), 2) AS dv FROM market_daily "
                   "WHERE day IN (SELECT day FROM d) GROUP BY ticker) "
                   "UPDATE tickers t SET dollar_volume_20d = v.dv FROM v WHERE v.ticker = t.ticker", (sessions,))
    db.execute("UPDATE tickers SET dollar_volume_20d = NULL WHERE dollar_volume_20d IS NOT NULL AND ticker NOT IN "
               "(SELECT DISTINCT ticker FROM market_daily WHERE day IN (SELECT DISTINCT day FROM market_daily "
               "ORDER BY day DESC LIMIT %s))", (sessions,))
    return n


def cik_of(db: Database, ticker: str) -> str | None:
    row = db.fetch_one("SELECT cik FROM tickers WHERE ticker = %s", (ticker,))
    return row["cik"] if row else None


def eps_fetched_at(db: Database, ticker: str) -> dt.datetime | None:
    row = db.fetch_one("SELECT max(as_of) AS at FROM eps_history WHERE ticker = %s", (ticker,))
    return row["at"] if row else None


def save_eps(db: Database, ticker: str, quarters: list[tuple[dt.date, Decimal]],
             years: list[tuple[dt.date, Decimal, Decimal | None]], source: str, as_of: dt.datetime) -> int:
    rows = [("quarter", end, eps, None) for end, eps in quarters] + [("year", end, eps, rev) for end, eps, rev in years]
    with db.connection() as conn:
        for kind, end, eps, rev in rows:
            conn.execute("INSERT INTO eps_history (ticker, kind, period_end, eps, revenue, source, as_of) "
                         "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (ticker, kind, period_end) DO UPDATE SET "
                         "eps = EXCLUDED.eps, revenue = EXCLUDED.revenue, source = EXCLUDED.source, "
                         "as_of = EXCLUDED.as_of", (ticker, kind, end, eps, rev, source, as_of))
        conn.commit()
    return len(rows)


def eps_history(db: Database, ticker: str) -> list[dict]:
    return db.fetch_all("SELECT kind, period_end, eps, revenue, source, as_of FROM eps_history WHERE ticker = %s "
                        "ORDER BY period_end DESC", (ticker,))

