"""Charts, quotes, news and earnings, the watchlist, and the market status (which sources are configured and when
they last answered). Every price carries its source and time."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from ghostline import indicators
from ghostline.auth import OwnerSub
from ghostline.market import store
from ghostline.market.calendar import Calendar
from ghostline.market.limits import Limiter
from ghostline.market.sources import configured

router = APIRouter()
TIMEFRAMES = {"1D": 2, "1W": 8, "1M": 32, "3M": 95, "6M": 190, "1Y": 370, "5Y": 1830}


@router.get("/charts")
def charts(request: Request, ticker: str, timeframe: str = Query(default="6M"), sub: str = OwnerSub) -> dict:
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"timeframe is one of {', '.join(TIMEFRAMES)}")
    db = request.app.state.db
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=TIMEFRAMES[timeframe])
    # indicators need history before the window: read the whole stored series, compute, then cut
    all_bars = store.bars(db, ticker.upper(), "1D")
    ind = indicators.chart_payload(all_bars)
    keep = [i for i, b in enumerate(all_bars) if b["ts"] >= since]
    cut = (lambda xs: [xs[i] for i in keep]) if keep else (lambda xs: [])
    out = {"ticker": ticker.upper(), "timeframe": timeframe, "bars": store.as_json(cut(all_bars)),
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
