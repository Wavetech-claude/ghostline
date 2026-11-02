"""Discover (the owner, 2026-11-01): what moved, where, and in which theme.

- Movers: today's top gainers, losers and most active come live from Alpaca (top 50 of the whole market). Any other
  slice (a week, a month, only ETFs, one industry) is computed from the whole market's daily file (market_daily,
  Massive's grouped daily) between the last stored session and the one 1, 5 or 21 sessions before it.
- Every list leaves out warrants, rights and units, stocks under the minimum price ($5 unless the owner turns it off)
  and, from the daily file, anything trading under $1M a day on average, which is where the junk lives.
- Sectors: the eleven Select Sector SPDR funds (XLK technology ... XLC communication), the usual stand-in for a sector.
  FMP's free sector and industry snapshots cover NASDAQ only (checked 2026-11-01), so they are not used.
- Industries: Finnhub's industry of each stock trading $5M a day or more; an industry's move is the median move of its
  stocks, so one runaway stock cannot carry it.
- Themes: curated here, as data: theme funds plus the Finnhub industries that hold the theme's companies. Curated
  means chosen by hand on 2026-11-01, not a list any source publishes; change it here.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from ghostline.db import Database
from ghostline.market.store import HIDDEN_KINDS

PERIODS = {"day": 1, "week": 5, "month": 21}           # sessions back
FUND_KINDS = ("ETF", "ETN", "ETV", "ETS", "FUND")
STOCK_KINDS = ("CS", "ADRC")
MIN_DOLLAR_VOLUME = Decimal("1000000")                  # the daily file's floor: a stock trading under $1M a day
INDUSTRY_MIN_DOLLAR_VOLUME = Decimal("5000000")
LIVE_MAX_AGE = dt.timedelta(days=4)                     # Alpaca's lists stay the last session's over a weekend

SECTORS = (
    ("Technology", "XLK"), ("Financials", "XLF"), ("Health Care", "XLV"), ("Energy", "XLE"),
    ("Consumer Discretionary", "XLY"), ("Consumer Staples", "XLP"), ("Industrials", "XLI"), ("Materials", "XLB"),
    ("Utilities", "XLU"), ("Real Estate", "XLRE"), ("Communication Services", "XLC"),
)

# Curated 2026-11-01. Industry names are Finnhub's, each checked on a real company that day (RKLB and LMT Aerospace &
# Defense, NVDA Semiconductors, VMC and PWR Construction, JPM Banking, MRNA Biotechnology, XOM Energy, CRWD and PANW
# Technology, NEM Metals & Mining, PLD Real Estate, TSLA Automobiles). A theme without a fitting industry lists funds
# only (solar companies such as FSLR are filed under Semiconductors, so clean energy has no industry).
THEMES: tuple[dict[str, Any], ...] = (
    {"id": "space", "name": "Space", "etfs": ("UFO", "ARKX"), "industries": ("Aerospace & Defense",),
     "note": "Space funds; the industry also holds defense companies."},
    {"id": "semiconductors", "name": "Semiconductors", "etfs": ("SMH", "SOXX"), "industries": ("Semiconductors",)},
    {"id": "construction", "name": "Construction and homebuilders", "etfs": ("ITB", "XHB", "PKB"),
     "industries": ("Construction",)},
    {"id": "clean-energy", "name": "Clean energy and solar", "etfs": ("ICLN", "TAN"), "industries": ()},
    {"id": "banks", "name": "Banks", "etfs": ("KBE", "KRE"), "industries": ("Banking",)},
    {"id": "biotech", "name": "Biotech", "etfs": ("XBI", "IBB"), "industries": ("Biotechnology",)},
    {"id": "oil-gas", "name": "Oil and gas", "etfs": ("XLE", "XOP"), "industries": ("Energy",)},
    {"id": "software", "name": "Software and cybersecurity", "etfs": ("IGV", "CIBR"), "industries": ("Technology",)},
    {"id": "gold", "name": "Gold and miners", "etfs": ("GLD", "GDX"), "industries": ("Metals & Mining",)},
    {"id": "real-estate", "name": "Real estate", "etfs": ("VNQ",), "industries": ("Real Estate",)},
    {"id": "autos", "name": "Electric vehicles and autos", "etfs": ("DRIV", "CARZ"), "industries": ("Automobiles",)},
)


def sessions(db: Database, back: int) -> tuple[dt.date, dt.date] | None:
    """The last stored session and the one `back` sessions before it; None while the file is too short."""
    rows = db.fetch_all("SELECT DISTINCT day FROM market_daily ORDER BY day DESC LIMIT %s", (back + 1,))
    return (rows[0]["day"], rows[back]["day"]) if len(rows) > back else None


def _pct(now: Any, then: Any) -> str | None:
    if now is None or not then:
        return None
    return str(((Decimal(now) / Decimal(then) - 1) * 100).quantize(Decimal("0.01")))


def movers(db: Database, period: str = "day", direction: str = "gainers", kind: str = "all",
           industry: str | None = None, min_price: Decimal = Decimal(5), limit: int = 25) -> dict[str, Any]:
    if period == "day" and kind == "all" and not industry:
        live = _live(db, direction, min_price, limit)
        if live is not None:
            return live
    span = sessions(db, PERIODS[period])
    if span is None:
        return {"period": period, "direction": direction, "source": "massive", "through": None, "since": None,
                "rows": []}
    last, base = span
    kinds = {"stocks": list(STOCK_KINDS), "etfs": list(FUND_KINDS)}.get(kind)
    order = {"gainers": "change_pct DESC", "losers": "change_pct ASC", "active": "dollar_volume DESC"}[direction]
    rows = db.fetch_all(
        "WITH w AS (SELECT ticker, sum(close * volume) AS dollar_volume FROM market_daily "
        "WHERE day > %s AND day <= %s GROUP BY ticker) "
        "SELECT n.ticker, t.name, t.kind, t.industry, n.close AS price, "
        "round((n.close / b.close - 1) * 100, 2) AS change_pct, w.dollar_volume "
        "FROM market_daily n JOIN market_daily b ON b.ticker = n.ticker AND b.day = %s "
        "JOIN tickers t ON t.ticker = n.ticker JOIN w ON w.ticker = n.ticker "
        "WHERE n.day = %s AND b.close > 0 AND n.close >= %s AND t.dollar_volume_20d >= %s "
        "AND (t.kind IS NULL OR NOT (t.kind = ANY(%s))) "
        "AND (%s::text[] IS NULL OR t.kind = ANY(%s::text[])) AND (%s::text IS NULL OR t.industry = %s) "
        f"ORDER BY {order}, n.ticker LIMIT %s",  # noqa: S608 — order is one of three fixed strings
        (base, last, base, last, min_price, MIN_DOLLAR_VOLUME, list(HIDDEN_KINDS), kinds, kinds, industry, industry,
         limit))
    return {"period": period, "direction": direction, "source": "massive", "through": last.isoformat(),
            "since": base.isoformat(),
            "rows": [{"ticker": r["ticker"], "name": r["name"], "kind": r["kind"], "industry": r["industry"],
                      "price": str(r["price"]), "change_pct": str(r["change_pct"]),
                      "dollar_volume": str(round(r["dollar_volume"] or 0)), "volume": None} for r in rows]}


def _live(db: Database, direction: str, min_price: Decimal, limit: int) -> dict[str, Any] | None:
    """Alpaca's stored list, when it is today's; the most-active list has no price, so the last close stands in."""
    rows = db.fetch_all(
        "SELECT m.*, t.name, t.kind, t.industry, (SELECT close FROM market_daily d WHERE d.ticker = m.ticker "
        "ORDER BY day DESC LIMIT 1) AS last_close FROM market_movers m LEFT JOIN tickers t ON t.ticker = m.ticker "
        "WHERE m.kind = %s ORDER BY m.rank", (direction,))
    if not rows or rows[0]["as_of"] < dt.datetime.now(dt.UTC) - LIVE_MAX_AGE:
        return None
    out = []
    for r in rows:
        price = r["price"] if r["price"] is not None else r["last_close"]
        if r["kind"] in HIDDEN_KINDS or _warrant_like(r["ticker"], r["kind"]):
            continue
        if price is not None and price < min_price:
            continue
        out.append({"ticker": r["ticker"], "name": r["name"], "kind": r["kind"], "industry": r["industry"],
                    "price": None if price is None else str(price),
                    "change_pct": None if r["change_pct"] is None else str(r["change_pct"]),
                    "dollar_volume": None, "volume": None if r["volume"] is None else str(r["volume"])})
        if len(out) >= limit:
            break
    return {"period": "day", "direction": direction, "source": "alpaca", "through": rows[0]["as_of"].isoformat(),
            "since": None, "rows": out}


def _warrant_like(ticker: str, kind: str | None) -> bool:
    """A ticker no list typed whose symbol reads as a warrant, right or unit (Alpaca's lists carry many)."""
    return kind is None and len(ticker) >= 5 and (ticker.endswith(("W", "R", "U")) or ticker.endswith("WS"))


def _fund_moves(db: Database, tickers: list[str], period: str) -> dict[str, str | None]:
    span = sessions(db, PERIODS[period])
    if span is None or not tickers:
        return {}
    last, base = span
    rows = db.fetch_all("SELECT n.ticker, n.close AS now, b.close AS then FROM market_daily n JOIN market_daily b "
                        "ON b.ticker = n.ticker AND b.day = %s WHERE n.day = %s AND n.ticker = ANY(%s)",
                        (base, last, tickers))
    return {r["ticker"]: _pct(r["now"], r["then"]) for r in rows}


def sectors(db: Database, period: str = "day") -> dict[str, Any]:
    moves = _fund_moves(db, [e for _, e in SECTORS], period)
    span = sessions(db, PERIODS[period])
    rows = [{"name": n, "etf": e, "change_pct": moves.get(e)} for n, e in SECTORS]
    rows.sort(key=lambda r: Decimal(r["change_pct"]) if r["change_pct"] is not None else Decimal("-1e9"), reverse=True)
    return {"period": period, "through": span[0].isoformat() if span else None, "rows": rows}


def industries(db: Database, period: str = "day", min_stocks: int = 3) -> dict[str, Any]:
    span = sessions(db, PERIODS[period])
    if span is None:
        return {"period": period, "through": None, "rows": [], "tagged": 0}
    last, base = span
    rows = db.fetch_all(
        "SELECT t.industry AS name, count(*) AS stocks, "
        "round((percentile_cont(0.5) WITHIN GROUP (ORDER BY (n.close / b.close - 1) * 100))::numeric, 2) "
        "AS change_pct, "
        "(array_agg(n.ticker ORDER BY n.close / b.close DESC))[1] AS best "
        "FROM market_daily n JOIN market_daily b ON b.ticker = n.ticker AND b.day = %s "
        "JOIN tickers t ON t.ticker = n.ticker "
        "WHERE n.day = %s AND b.close > 0 AND t.industry IS NOT NULL AND t.dollar_volume_20d >= %s "
        "GROUP BY t.industry HAVING count(*) >= %s ORDER BY change_pct DESC",
        (base, last, INDUSTRY_MIN_DOLLAR_VOLUME, min_stocks))
    tagged = db.fetch_one("SELECT count(*) AS n FROM tickers WHERE industry IS NOT NULL")["n"]
    return {"period": period, "through": last.isoformat(), "tagged": tagged,
            "rows": [{"name": r["name"], "stocks": r["stocks"], "change_pct": str(r["change_pct"]), "best": r["best"]}
                     for r in rows]}


def themes(db: Database, period: str = "week", per_theme: int = 5) -> dict[str, Any]:
    funds = sorted({e for t in THEMES for e in t["etfs"]})
    moves = _fund_moves(db, funds, period)
    names = {r["ticker"]: r["name"] for r in db.fetch_all("SELECT ticker, name FROM tickers WHERE ticker = ANY(%s)",
                                                          (funds,))}
    out = []
    for t in THEMES:
        leaders: list[dict] = []
        for industry in t["industries"]:
            leaders += movers(db, period, "gainers", "stocks", industry, limit=per_theme)["rows"]
        leaders.sort(key=lambda r: Decimal(r["change_pct"]), reverse=True)
        out.append({"id": t["id"], "name": t["name"], "note": t.get("note"),
                    "etfs": [{"ticker": e, "name": names.get(e), "change_pct": moves.get(e)} for e in t["etfs"]],
                    "industries": list(t["industries"]), "leaders": leaders[:per_theme]})
    span = sessions(db, PERIODS[period])
    return {"period": period, "through": span[0].isoformat() if span else None, "themes": out}


def industry_names(db: Database) -> list[dict[str, Any]]:
    rows = db.fetch_all("SELECT industry AS name, count(*) AS stocks FROM tickers WHERE industry IS NOT NULL "
                        "GROUP BY industry ORDER BY industry")
    return [{"name": r["name"], "stocks": r["stocks"]} for r in rows]
