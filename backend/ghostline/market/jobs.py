"""The worker's data jobs: pull with the cache's counters, store with source and as_of, and say what happened. A
source without its key skips with a logged reason (the owner adds the key; nothing else changes)."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import httpx

from ghostline.db import Database
from ghostline.market import NotConfigured, RateLimited, store, utcnow
from ghostline.market.calendar import Calendar
from ghostline.market.limits import Limiter
from ghostline.market.sources import ALL, FMP, FRED, GDELT, Alpaca, Edgar, Finnhub, Massive, Tradier

logger = logging.getLogger("ghostline.jobs")

MACRO_SERIES = ("DGS10", "DGS2", "CPIAUCSL", "DEXINUS", "UNRATE", "FEDFUNDS")   # rates, CPI, USD/INR, jobs, policy
# GDELT world news tone for the brief's backdrop (no key): one topic per run of pull_tone, see there
TONE_QUERIES = ("\"stock market\"", "\"India economy\"", "\"Federal Reserve\"")
PUT_DTE = (30, 45)


class Jobs:
    def __init__(self, db: Database, http: httpx.Client | None = None, sources: dict[str, Any] | None = None) -> None:
        self.db = db
        self.limiter = Limiter(db)
        self.sources = sources or {name: cls(http) for name, cls in ALL.items()}

    def _call(self, source: str, endpoint: str, fn, *args, wait: bool = True) -> Any:
        """One counted call: paced through a full minute (wait), refused at the day's limit, recorded after, never
        raised past the job."""
        try:
            self.limiter.check(source, wait=wait)
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
        for ticker in store.scan_tickers(self.db):
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
        quotes = self._call("tradier", "quotes", tradier.quotes, tickers + store.tracked_option_symbols(self.db))
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
        tracked = set(store.tracked_tickers(self.db))
        for ticker in store.scan_tickers(self.db):
            if ticker in store.MARKET_TICKERS:
                continue
            cal = self._call("finnhub", "calendar/earnings", finnhub.earnings, ticker, today - dt.timedelta(days=90),
                             today + dt.timedelta(days=120))
            if cal:
                out["earnings"] += store.save_earnings(self.db, cal, "finnhub", utcnow())
            if ticker not in tracked:
                continue          # history, news and filings only for what the owner holds or watches
            hist = self._call("fmp", "earnings", fmp.earnings, ticker)       # FMP's 250 a day go to the owner's names
            if hist:
                out["earnings"] += store.save_earnings(self.db, hist, "fmp", utcnow())
            news = self._call("finnhub", "company-news", finnhub.news, ticker, today - dt.timedelta(days=14), today)
            if news:
                out["news"] += store.save_news(self.db, news, "finnhub", utcnow())
            filings = self._call("edgar", "submissions", edgar.filings, ticker)
            if filings:
                out["filings"] += store.save_filings(self.db, filings, "edgar", utcnow())
        return out

    def build_universe(self, day: dt.date | None = None, size: int = 50) -> dict:
        """One Massive grouped-daily pull of the whole market: the most traded names (by dollar volume) above $10
        that moved at least 2% that day become today's scan universe, beside the tracked tickers."""
        massive: Massive = self.sources["massive"]
        day = day or Calendar.load(self.db).previous_session(dt.date.today())
        grouped = self._call("massive", "grouped-daily", massive.grouped_daily, day)
        if not grouped:
            return {"universe": 0, "reason": "no grouped pull"}
        rows = []
        for ticker, bar in grouped.items():
            if not ticker.isalpha() or len(ticker) > 5 or bar.close < 10 or bar.open <= 0:
                continue
            change = float((bar.close / bar.open - 1) * 100)
            if abs(change) < 2:
                continue
            rows.append({"ticker": ticker, "reason": "movers", "dollar_volume": bar.close * bar.volume,
                         "change_pct": round(change, 4)})
        rows.sort(key=lambda r: -r["dollar_volume"])
        n = store.save_universe(self.db, dt.date.today(), rows[:size])
        return {"universe": n, "day": day.isoformat()}

    def pull_calendar(self, today: dt.date | None = None) -> dict:
        """The exchange calendar from 10 days back to about 13 months ahead, in one call."""
        today = today or dt.date.today()
        start, end = today - dt.timedelta(days=10), today + dt.timedelta(days=400)
        alpaca: Alpaca = self.sources["alpaca"]
        sessions = self._call("alpaca", "calendar", alpaca.calendar, start, end)
        if not sessions:
            return {"sessions": 0}
        return {"sessions": store.save_sessions(self.db, sessions, start, end, "alpaca", utcnow()),
                "through": sessions[-1].day.isoformat()}

    def pull_corporate_actions(self, today: dt.date | None = None) -> dict:
        """Splits and cash dividends for every tracked ticker, ex-dates from 40 days back (a dividend is paid weeks
        after it goes ex) to 40 days ahead, in one call."""
        tickers = store.tracked_tickers(self.db)
        if not tickers:
            return {"actions": 0}
        today = today or dt.date.today()
        alpaca: Alpaca = self.sources["alpaca"]
        found = self._call("alpaca", "corporate-actions", alpaca.corporate_actions, tickers,
                           today - dt.timedelta(days=40), today + dt.timedelta(days=40))
        return {"actions": store.save_actions(self.db, found, "alpaca", utcnow()) if found else 0}

    def pull_macro(self) -> dict:
        fred: FRED = self.sources["fred"]
        out = {"points": 0}
        since = dt.date.today() - dt.timedelta(days=5 * 365)
        for series in MACRO_SERIES:
            pts = self._call("fred", f"series/{series}", fred.series, series, since)
            if pts:
                out["points"] += store.save_macro(self.db, pts, "fred", utcnow())
        return out

    def pull_tone(self, today: dt.date | None = None) -> dict:
        """GDELT world news tone, at most one topic per run. GDELT's real limiter is much stricter than the "one request
        every 5 seconds" it publishes: on 2026-10-30 it refused our first request in 46 minutes, at 10:25:45 PT, and
        every request until 10:36; outside measurements saw 1 success in 7 at 6-second spacing, blocks of up to about
        25 minutes, and retries inside a block keeping it shut. So a run asks for the stalest topic only, never retries,
        and stops at a refusal; the worker runs it every 30 minutes overnight, before the brief. One answer carries a
        week of days, so a night GDELT refuses fills itself in on the next answer."""
        today = today or dt.datetime.now(dt.UTC).date()      # GDELT dates its hours in UTC
        rows = self.db.fetch_all("SELECT series, max(ts) AS newest FROM macro_series WHERE source = 'gdelt' "
                                 "GROUP BY series")
        newest = {r["series"]: r["newest"] for r in rows}
        stale = [q for q in TONE_QUERIES if (newest.get(f"gdelt:{q}") or dt.date.min) < today]
        if not stale:
            return {"asked": None, "points": 0, "stale": 0}
        q = min(stale, key=lambda s: newest.get(f"gdelt:{s}") or dt.date.min)     # ties: the first in TONE_QUERIES
        gdelt: GDELT = self.sources["gdelt"]
        pts = self._call("gdelt", "timelinetone", gdelt.tone, q, 7, wait=False)     # stop at a full minute too
        return {"asked": q, "points": store.save_macro(self.db, pts, "gdelt", utcnow()) if pts else 0,
                "stale": len(stale)}

    def pull_chains(self) -> dict:
        """Before the scan: for every stock the owner would happily own, the expiry 30 to 45 days out nearest 38
        days, its whole chain stored. The sell-puts module reads only stored chains."""
        tradier: Tradier = self.sources["tradier"]
        today = dt.date.today()
        out: dict[str, Any] = {"tickers": 0, "options": 0, "no_expiry": []}
        for ticker in store.put_candidates(self.db):
            dates = self._call("tradier", "expirations", tradier.expirations, ticker)
            if dates is None:
                break                          # not configured or limited: the next tickers would be refused too
            fits = [d for d in dates if PUT_DTE[0] <= (d - today).days <= PUT_DTE[1]]
            if not fits:
                out["no_expiry"].append(ticker)
                continue
            expiry = min(fits, key=lambda d: abs((d - today).days - 38))
            raw = self._call("tradier", "chain", tradier.chain, ticker, expiry)
            if raw:
                out["options"] += len(store.save_chain(self.db, raw, utcnow()))
                out["tickers"] += 1
        return out

    def pull_fundamentals(self) -> dict:
        fmp: FMP = self.sources["fmp"]
        out = {"tickers": 0}
        for ticker in store.tracked_tickers(self.db):
            profile = self._call("fmp", "profile", fmp.profile, ticker)
            ratios = self._call("fmp", "ratios-ttm", fmp.ratios, ticker)
            income = self._call("fmp", "income-statement", fmp.income_annual, ticker)
            if profile or ratios or income:
                payload = {"profile": profile or {}, "ratios": ratios or {}, "income_annual": income or []}
                store.save_fundamentals(self.db, ticker, payload, "fmp", utcnow())
                out["tickers"] += 1
        return out
