"""The worker's data jobs: pull with the cache's counters, store with source and as_of, and say what happened. A
source without its key skips with a logged reason (the owner adds the key; nothing else changes)."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import httpx

from ghostline.db import Database
from ghostline.market import NotConfigured, RateLimited, store, utcnow
from ghostline.market.limits import Limiter
from ghostline.market.sources import ALL, FMP, FRED, Alpaca, Edgar, Finnhub, Tradier

logger = logging.getLogger("ghostline.jobs")

MACRO_SERIES = ("DGS10", "DGS2", "CPIAUCSL", "DEXINUS", "UNRATE", "FEDFUNDS")   # rates, CPI, USD/INR, jobs, policy


class Jobs:
    def __init__(self, db: Database, http: httpx.Client | None = None, sources: dict[str, Any] | None = None) -> None:
        self.db = db
        self.limiter = Limiter(db)
        self.sources = sources or {name: cls(http) for name, cls in ALL.items()}

    def _call(self, source: str, endpoint: str, fn, *args) -> Any:
        """One counted call: refused before the limit, recorded after, never raised past the job."""
        try:
            self.limiter.check(source)
            result = fn(*args)
            self.limiter.record(source, endpoint, "ok", rows=len(result) if hasattr(result, "__len__") else 1)
            return result
        except NotConfigured as exc:
            self.limiter.record(source, endpoint, "skipped", str(exc))
            logger.info("skipped: %s", exc)
        except RateLimited as exc:
            self.limiter.record(source, endpoint, "skipped", str(exc))
            logger.warning("rate limit: %s", exc)
        except Exception as exc:  # noqa: BLE001 — one source's failure never stops the others
            self.limiter.record(source, endpoint, "error", f"{type(exc).__name__}: {exc}")
            logger.warning("%s %s failed: %s: %s", source, endpoint, type(exc).__name__, exc)
        return None

    def pull_bars(self, days: int = 400) -> dict:
        """Daily bars for every tracked ticker from Alpaca (IEX, split-adjusted)."""
        alpaca: Alpaca = self.sources["alpaca"]
        out = {"tickers": 0, "bars": 0}
        end = dt.date.today()
        start = end - dt.timedelta(days=days)
        for ticker in store.tracked_tickers(self.db):
            bars = self._call("alpaca", "bars", alpaca.bars, ticker, start, end)
            if bars:
                out["bars"] += store.save_bars(self.db, ticker, "1D", bars, "alpaca", utcnow())
                out["tickers"] += 1
        return out

    def pull_quotes(self) -> dict:
        """Latest quotes: Tradier for the owner's account when configured, else Alpaca's IEX quotes."""
        tickers = store.tracked_tickers(self.db)
        if not tickers:
            return {"quotes": 0}
        tradier: Tradier = self.sources["tradier"]
        quotes = self._call("tradier", "quotes", tradier.quotes, tickers)
        source = "tradier"
        if not quotes:
            alpaca: Alpaca = self.sources["alpaca"]
            quotes = self._call("alpaca", "quotes", alpaca.quotes, tickers)
            source = "alpaca"
        return {"quotes": store.save_quotes(self.db, quotes, source, utcnow()) if quotes else 0, "source": source}

    def pull_news_earnings(self) -> dict:
        finnhub: Finnhub = self.sources["finnhub"]
        fmp: FMP = self.sources["fmp"]
        edgar: Edgar = self.sources["edgar"]
        today = dt.date.today()
        out = {"news": 0, "earnings": 0, "filings": 0}
        for ticker in store.tracked_tickers(self.db):
            news = self._call("finnhub", "company-news", finnhub.news, ticker, today - dt.timedelta(days=14), today)
            if news:
                out["news"] += store.save_news(self.db, news, "finnhub", utcnow())
            cal = self._call("finnhub", "calendar/earnings", finnhub.earnings, ticker, today - dt.timedelta(days=90),
                             today + dt.timedelta(days=120))
            if cal:
                out["earnings"] += store.save_earnings(self.db, cal, "finnhub", utcnow())
            hist = self._call("fmp", "earnings", fmp.earnings, ticker)
            if hist:
                out["earnings"] += store.save_earnings(self.db, hist, "fmp", utcnow())
            filings = self._call("edgar", "submissions", edgar.filings, ticker)
            if filings:
                out["filings"] += store.save_filings(self.db, filings, "edgar", utcnow())
        return out

    def pull_macro(self) -> dict:
        fred: FRED = self.sources["fred"]
        out = {"points": 0}
        since = dt.date.today() - dt.timedelta(days=5 * 365)
        for series in MACRO_SERIES:
            pts = self._call("fred", f"series/{series}", fred.series, series, since)
            if pts:
                out["points"] += store.save_macro(self.db, pts, "fred", utcnow())
        return out

    def pull_fundamentals(self) -> dict:
        fmp: FMP = self.sources["fmp"]
        out = {"tickers": 0}
        for ticker in store.tracked_tickers(self.db):
            profile = self._call("fmp", "profile", fmp.profile, ticker)
            ratios = self._call("fmp", "ratios-ttm", fmp.ratios, ticker)
            if profile or ratios:
                payload = {"profile": profile or {}, "ratios": ratios or {}}
                store.save_fundamentals(self.db, ticker, payload, "fmp", utcnow())
                out["tickers"] += 1
        return out
