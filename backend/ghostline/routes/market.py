"""Charts, quotes, news and earnings, the earnings calendar, analysts' views, the watchlist, and the market status
(which sources are configured and when they last answered). Every price carries its source and time."""
from __future__ import annotations

import datetime as dt
import re
import time
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ghostline import indicators
from ghostline.auth import OwnerSub
from ghostline.market import store
from ghostline.market.calendar import NEW_YORK, Calendar
from ghostline.market.jobs import Jobs
from ghostline.market.limits import Limiter
from ghostline.market.sources import configured

router = APIRouter()
TIMEFRAMES = {"1M": 32, "3M": 95, "6M": 190, "1Y": 370, "5Y": 1830}          # daily bars: days back
# intraday: (bar length, sessions shown, seconds a stored copy stays fresh while the market is open)
INTRADAY = {"1D": ("5Min", 1, 60), "1W": ("30Min", 5, 300)}
_fetched: dict[tuple[str, str], float] = {}     # (ticker, bar length) → when this process last asked Alpaca
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
FRESH_DAYS = 4   # a weekend and a holiday: older than this, a chart the owner opens is fetched again


def _stale(bars: list[dict]) -> bool:
    return not bars or bars[-1]["ts"].date() < dt.date.today() - dt.timedelta(days=FRESH_DAYS)


def _fetch_on_demand(request: Request, ticker: str) -> None:
    """A stock the owner opened that no daily pull covers (searched, not watched): fetched now, in production only.
    Staging pulls nothing (the owner's rule, 2026-10-30); its data is production's nightly copy."""
    settings = request.app.state.settings
    if settings.is_production and SYMBOL.match(ticker):
        Jobs(request.app.state.db).bars_for(ticker)


@router.get("/market/search")
def search(request: Request, q: str = Query(min_length=1, max_length=40), sub: str = OwnerSub) -> dict:
    rows = store.search_tickers(request.app.state.db, q)
    return {"results": [{"ticker": r["ticker"], "name": r["name"], "watched": bool(r["watched"])} for r in rows]}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def intraday_window(cal: Calendar, now: dt.datetime, sessions: int) -> list[dt.date]:
    """The session days a 1D or 1W chart shows: the latest session that has opened (today once the bell rings, the
    one before until then), and the sessions before it."""
    today = now.astimezone(NEW_YORK).date()
    latest = today if cal.is_session(today) and cal.opens_at(today) <= now else cal.previous_session(today)
    days = [latest]
    while len(days) < sessions:
        days.insert(0, cal.previous_session(days[0]))
    return days


def _in_session(cal: Calendar, days: list[dt.date], ts: dt.datetime) -> bool:
    """A bar inside regular hours of one of the days (Alpaca also returns pre-market and after-hours bars)."""
    day = ts.astimezone(NEW_YORK).date()
    return day in days and cal.opens_at(day) <= ts < cal.closes_at(day)


def _intraday(request: Request, ticker: str, timeframe: str) -> dict:
    """A 1D chart (5-minute bars of the latest session) or a 1W chart (30-minute bars of the last five), from
    Alpaca's IEX feed: live, IEX's volume only. Fetched in production when the stored copy is missing, short of the
    window, or older than its freshness while the market is open; staging reads production's copy."""
    db = request.app.state.db
    interval, sessions, fresh_s = INTRADAY[timeframe]
    cal, now = Calendar.load(db), _now()
    days = intraday_window(cal, now, sessions)
    start, end = cal.opens_at(days[0]), cal.closes_at(days[-1])
    market_open = cal.opens_at(days[-1]) <= now < end
    stored = [b for b in store.bars(db, ticker, interval, since=start) if _in_session(cal, days, b["ts"])]
    newest = max((b["as_of"] for b in stored), default=None)
    wanted = (not stored or stored[0]["ts"].astimezone(NEW_YORK).date() > days[0]
              or (market_open and (newest is None or (now - newest).total_seconds() > fresh_s))
              or (not market_open and (newest is None or newest < end)))
    settings = request.app.state.settings
    asked = _fetched.get((ticker, interval))
    recently = asked is not None and time.monotonic() - asked <= fresh_s      # one ask per freshness window
    if wanted and settings.is_production and SYMBOL.match(ticker) and not recently:
        _fetched[(ticker, interval)] = time.monotonic()
        Jobs(db).intraday_for(ticker, interval, start, min(end, now))
        stored = [b for b in store.bars(db, ticker, interval, since=start) if _in_session(cal, days, b["ts"])]
    daily = store.bars(db, ticker, "1D")
    before = [b for b in daily if b["ts"].date() < days[-1]]
    ind = indicators.chart_payload(stored)
    levels = indicators.chart_payload(daily)["levels"] if daily else []
    return {"ticker": ticker, "timeframe": timeframe, "interval": interval, "feed": "iex", "delayed": False,
            "bars": store.as_json(stored),
            "source": stored[-1]["source"] if stored else None,
            "as_of": max(b["as_of"] for b in stored).isoformat() if stored else None,
            "previous_close": str(before[-1]["close"]) if before else None,
            "market_open": market_open, "sessions": [d.isoformat() for d in days],
            "sma20": ind["sma20"], "sma50": ind["sma50"], "sma200": ind["sma200"], "rsi14": ind["rsi14"],
            "macd": ind["macd"], "bollinger": ind["bollinger"], "levels": levels}


@router.get("/charts")
def charts(request: Request, ticker: str, timeframe: str = Query(default="6M"), sub: str = OwnerSub) -> dict:
    if timeframe in INTRADAY:
        return _intraday(request, ticker.upper(), timeframe)
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"timeframe is one of {', '.join([*INTRADAY, *TIMEFRAMES])}")
    db = request.app.state.db
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=TIMEFRAMES[timeframe])
    # indicators need history before the window: read the whole stored series, compute, then cut
    all_bars = store.bars(db, ticker.upper(), "1D")
    if _stale(all_bars):
        _fetch_on_demand(request, ticker.upper())
        all_bars = store.bars(db, ticker.upper(), "1D")
    ind = indicators.chart_payload(all_bars)
    keep = [i for i, b in enumerate(all_bars) if b["ts"] >= since]
    cut = (lambda xs: [xs[i] for i in keep]) if keep else (lambda xs: [])
    out = {"ticker": ticker.upper(), "timeframe": timeframe, "interval": "1Day", "feed": "iex",
           "bars": store.as_json(cut(all_bars)),
           "source": all_bars[-1]["source"] if all_bars else None,
           "as_of": all_bars[-1]["as_of"].isoformat() if all_bars else None,
           "sma20": cut(ind["sma20"]), "sma50": cut(ind["sma50"]), "sma200": cut(ind["sma200"]),
           "rsi14": cut(ind["rsi14"]),
           "macd": {k: cut(v) for k, v in ind["macd"].items()},
           "bollinger": {k: cut(v) for k, v in ind["bollinger"].items()},
           "levels": ind["levels"]}
    return out


@router.get("/quotes")
def quotes(request: Request, tickers: str, sub: str = OwnerSub) -> dict:
    wanted = [t.strip().upper() for t in tickers.split(",") if t.strip()][:50]
    rows = store.latest_quotes(request.app.state.db, wanted)
    return {"quotes": {t: store.jsonable(r) for t, r in rows.items()},
            "missing": [t for t in wanted if t not in rows]}


@router.get("/news-earnings")
def news_earnings(request: Request, ticker: str, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    t = ticker.upper()
    earnings = store.as_json(store.earnings_for(db, t))
    bars = store.bars(db, t, "1D", limit=2000)
    closes = [(b["ts"].date(), b["close"]) for b in bars]
    for e in earnings:
        e["move_pct"] = _move_around(closes, dt.date.fromisoformat(str(e["at"])[:10]))
    return {"ticker": t, "news": store.as_json(store.news_for(db, t)), "earnings": earnings,
            "filings": store.as_json(store.filings_for(db, t))}


@router.get("/earnings/calendar")
def earnings_calendar(request: Request, days: int = Query(default=30, ge=1, le=90),
                      past: int = Query(default=7, ge=0, le=30), sub: str = OwnerSub) -> dict:
    """Reports of the stocks the owner holds, watches or has an open idea on: the last `past` days (with the actual
    against the estimate and how the stock moved) and the next `days` (with the estimate and how far the stock
    usually moved around its last four reports). Rows are in date order; the app groups them by day."""
    db = request.app.state.db
    today = dt.date.today()
    held, watched, ideas = store.held_tickers(db), store.watched_tickers(db), set(store.idea_tickers(db))
    tickers = sorted((held | watched | ideas) - set(store.MARKET_TICKERS))
    rows = store.earnings_between(db, tickers, today - dt.timedelta(days=past), today + dt.timedelta(days=days))
    names = store.ticker_names(db, tickers)
    shown = sorted({r["ticker"] for r in rows})
    closes = {t: [(b["ts"].date(), b["close"]) for b in store.bars(db, t, "1D", limit=2000)] for t in shown}
    history = store.past_earnings(db, shown, today)
    out = []
    for r in rows:
        t, day = r["ticker"], r["at"]
        why = [w for w, s in (("held", held), ("watched", watched), ("idea", ideas)) if t in s]
        row = {"ticker": t, "name": names.get(t), "at": day.isoformat(), "hour": r["hour"],
               "estimate": _s(r["estimate"]), "actual": _s(r["actual"]),
               "revenue_estimate": _s(r["revenue_estimate"]), "revenue_actual": _s(r["revenue_actual"]),
               "surprise_pct": _surprise(r["estimate"], r["actual"]), "why": why, "move_pct": None,
               "typical_move_pct": None}
        if day < today:
            row["move_pct"] = _move_around(closes[t], day)
        else:
            moves = [m for d in history.get(t, []) if (m := _move_around(closes[t], d)) is not None]
            if moves:
                avg = sum(abs(Decimal(m)) for m in moves) / len(moves)
                row["typical_move_pct"] = str(avg.quantize(Decimal("0.01")))
        out.append(row)
    since, until = today - dt.timedelta(days=past), today + dt.timedelta(days=days)
    return {"from": since.isoformat(), "until": until.isoformat(), "earnings": out}


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def _surprise(estimate: Any, actual: Any) -> str | None:
    """How far the actual EPS beat (positive) or missed the estimate, in percent of the estimate's size."""
    if estimate is None or actual is None or Decimal(estimate) == 0:
        return None
    pct = (Decimal(actual) - Decimal(estimate)) / abs(Decimal(estimate)) * 100
    return str(pct.quantize(Decimal("0.01")))


@router.get("/analysts")
def analysts(request: Request, ticker: str, sub: str = OwnerSub) -> dict:
    """Analysts' views of one stock: Finnhub's recommendation trends (the last four months) and FMP's grade consensus
    and price targets. Missing or a week old for a stock the owner opened: asked now, in production only."""
    db = request.app.state.db
    t = ticker.upper()
    if request.app.state.settings.is_production and SYMBOL.match(t) and t not in store.MARKET_TICKERS:
        Jobs(db).analysts_now(t)
    got = store.analysts_for(db, t)
    c = got["consensus"]
    return {"ticker": t, "trends": store.as_json(got["trends"]),
            "consensus": None if not c else {k: _s(c[k]) if k.startswith("target") else c[k]
                                            for k in ("strong_buy", "buy", "hold", "sell", "strong_sell", "consensus",
                                                      "target_high", "target_low", "target_consensus",
                                                      "target_median")} | {"source": c["source"],
                                                                           "as_of": c["as_of"].isoformat()}}


def _move_around(closes: list[tuple[dt.date, Any]], day: dt.date) -> str | None:
    """The two-session move around a report: the last close before the report date to the first close after it
    (covers a report before the open or after the close, since the timing is not stored). None without both closes."""
    before = [c for d, c in closes if d < day]
    after = [c for d, c in closes if d > day]
    if not before or not after or not before[-1]:
        return None
    move = (Decimal(after[0]) / Decimal(before[-1]) - 1) * 100
    return str(move.quantize(Decimal("0.01")))


class WatchIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


@router.get("/watchlist")
def watchlist(request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    rows = db.fetch_all("SELECT ticker, note, added_at FROM watchlist ORDER BY added_at")
    q = store.latest_quotes(db, [r["ticker"] for r in rows])
    return {"watchlist": [{**store.jsonable(r), "quote": store.jsonable(q[r["ticker"]]) if r["ticker"] in q else None}
                          for r in rows]}


@router.post("/watchlist", status_code=201)
def watch(body: WatchIn, request: Request, sub: str = OwnerSub) -> dict:
    request.app.state.db.execute("INSERT INTO watchlist (ticker, note) VALUES (%s, %s) ON CONFLICT (ticker) DO UPDATE "
                                 "SET note = COALESCE(EXCLUDED.note, watchlist.note)", (body.ticker, body.note))
    return {"ok": True, "ticker": body.ticker}


@router.delete("/watchlist/{ticker}")
def unwatch(ticker: str, request: Request, sub: str = OwnerSub) -> dict:
    n = request.app.state.db.execute("DELETE FROM watchlist WHERE ticker = %s", (ticker.upper(),))
    return {"ok": True, "removed": n}


@router.get("/market/status")
def market_status(request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    cal = Calendar.load(db)
    return {"configured": configured(), "pulls": Limiter(db).status(), "tracked": store.tracked_tickers(db),
            "calendar_through": cal.last.isoformat() if cal.last else None}
