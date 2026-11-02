"""The worker's data jobs: pull with the cache's counters, store with source and as_of, and say what happened. A
source without its key skips with a logged reason (the owner adds the key; nothing else changes)."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import httpx

from ghostline.db import Database
from ghostline.market import NotConfigured, RateLimited, greeks, store, utcnow
from ghostline.market.calendar import Calendar
from ghostline.market.limits import Limiter
from ghostline.market.sources import (
    ALL,
    FMP,
    FRED,
    GDELT,
    Alpaca,
    Edgar,
    Finnhub,
    Massive,
    Tradier,
    _alpaca_option,
    configured,
)

logger = logging.getLogger("ghostline.jobs")

MACRO_SERIES = ("DGS10", "DGS2", "CPIAUCSL", "DEXINUS", "UNRATE", "FEDFUNDS")   # rates, CPI, USD/INR, jobs, policy
# GDELT world news tone for the brief's backdrop (no key): one topic per run of pull_tone, see there
TONE_QUERIES = ("\"stock market\"", "\"India economy\"", "\"Federal Reserve\"")
PUT_DTE = (30, 45)
MARKET_DAILY_SESSIONS = 23     # the whole-market file kept filled: 20 sessions for popularity, a month for movers
MARKET_DAILY_KEEP_DAYS = 100   # calendar days of the whole-market file kept (about 70 sessions)
ANALYST_FRESH_DAYS = 7         # analysts' views move slowly: each stock is asked at most once a week per source
ANALYST_FMP_CAP = 60           # FMP calls one morning run may spend on analysts (2 a stock) of the day's 250
ANALYST_FMP_RESERVE = 200      # a stock page opened on demand asks FMP only below this many calls today


INTRADAY_KEEP_DAYS = 10     # a 1-week chart needs 5 sessions; ten days covers a holiday week


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

    def bars_for(self, ticker: str, days: int = 1830) -> int:
        """Five years of daily bars for one ticker, now (a stock the owner opened that no pull covers yet)."""
        end = dt.date.today()
        bars = self._call("alpaca", "bars", self.sources["alpaca"].bars, ticker, end - dt.timedelta(days=days), end,
                          wait=False)
        return store.save_bars(self.db, ticker, "1D", bars, "alpaca", utcnow()) if bars else 0

    def intraday_for(self, ticker: str, interval: str, start: dt.datetime, end: dt.datetime) -> int:
        """One ticker's intraday bars (5Min or 30Min) for a chart the owner opened, now. Intraday bars older than
        INTRADAY_KEEP_DAYS are removed for the ticker on the way, so the table never grows with every chart opened."""
        bars = self._call("alpaca", "bars-" + interval, self.sources["alpaca"].bars, ticker, start, end, interval,
                          wait=False)
        saved = store.save_bars(self.db, ticker, interval, bars, "alpaca", utcnow()) if bars else 0
        store.prune_intraday(self.db, ticker, INTRADAY_KEEP_DAYS)
        return saved

    def pull_tickers(self) -> dict:
        """The ticker directory from the SEC's company list (one request, no key), for search and EDGAR's facts."""
        rows = self._call("edgar", "company_tickers", self.sources["edgar"].directory)
        return {"tickers": store.save_tickers(self.db, rows, "sec", utcnow()) if rows else 0}

    def pull_eps_history(self, fresh_days: int = 7) -> dict:
        """Reported EPS from EDGAR for every scanned stock with a CIK, at most once a week each (filings are
        quarterly), so CANSLIM can judge stocks FMP's free plan does not cover."""
        out = {"tickers": 0, "rows": 0, "fresh": 0}
        now = utcnow()
        for ticker in store.scan_tickers(self.db):
            cik = store.cik_of(self.db, ticker)
            if not cik:
                continue
            fetched = store.eps_fetched_at(self.db, ticker)
            if fetched and now - fetched < dt.timedelta(days=fresh_days):
                out["fresh"] += 1
                continue
            got = self._call("edgar", "companyconcept", self.sources["edgar"].eps_history, cik)
            if got and (got[0] or got[1]):
                out["rows"] += store.save_eps(self.db, ticker, got[0], got[1], "sec", now)
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
        saved = 0
        if not quotes:
            alpaca: Alpaca = self.sources["alpaca"]
            quotes = self._call("alpaca", "quotes", alpaca.quotes, tickers)
            source = "alpaca"
            options = store.tracked_option_symbols(self.db)
            if options:                        # without Tradier, the option positions' prices come from Alpaca too
                got = self._call("alpaca", "option-quotes", alpaca.option_quotes, options)
                saved += store.save_quotes(self.db, got, "alpaca", utcnow()) if got else 0
        saved += store.save_quotes(self.db, quotes, source, utcnow()) if quotes else 0
        return {"quotes": saved, "source": source}

    def pull_news_earnings(self) -> dict:
        finnhub: Finnhub = self.sources["finnhub"]
        fmp: FMP = self.sources["fmp"]
        edgar: Edgar = self.sources["edgar"]
        today = dt.date.today()
        out = {"news": 0, "earnings": 0, "filings": 0}
        tracked = set(store.tracked_tickers(self.db))
        # report dates also for open ideas, which may outlive the day's universe (the earnings calendar lists them)
        for ticker in sorted(set(store.scan_tickers(self.db)) | set(store.idea_tickers(self.db))):
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

    def analyst_tickers(self) -> list[str]:
        """Whose analysts' views are kept fresh: what the owner holds or watches, and open ideas (not the market
        ETFs)."""
        found = set(store.tracked_tickers(self.db)) | set(store.idea_tickers(self.db))
        return sorted(found - set(store.MARKET_TICKERS))

    def pull_analysts(self, fresh_days: int = ANALYST_FRESH_DAYS, fmp_cap: int = ANALYST_FMP_CAP) -> dict:
        """Analysts' views, at most once a week per stock and source: Finnhub's recommendation trends (free, 60 a
        minute) and FMP's grade consensus and price targets (two calls a stock from FMP's 250 a day, at most `fmp_cap`
        calls a run so news, earnings history and fundamentals keep theirs)."""
        out = {"tickers": 0, "finnhub": 0, "fmp": 0, "fresh": 0}
        fmp_calls = 0
        for ticker in self.analyst_tickers():
            did = False
            if self._stale(ticker, "finnhub", fresh_days):
                did |= self._finnhub_trends(ticker, wait=True)
                out["finnhub"] += 1
            if self._stale(ticker, "fmp", fresh_days) and fmp_calls + 2 <= fmp_cap:
                did |= self._fmp_consensus(ticker, wait=True)
                fmp_calls += 2
                out["fmp"] += 1
            out["tickers" if did else "fresh"] += 1
        return out

    def analysts_now(self, ticker: str, fresh_days: int = ANALYST_FRESH_DAYS) -> bool:
        """A stock the owner opened whose views are missing or a week old: asked now, never waiting out a minute.
        FMP is asked only while the day's calls stay under ANALYST_FMP_RESERVE, so a browsing session cannot eat the
        calls the morning pulls need."""
        asked = False
        if self._stale(ticker, "finnhub", fresh_days):
            asked |= self._finnhub_trends(ticker, wait=False)
        if self._stale(ticker, "fmp", fresh_days) and self.limiter.used("fmp", 86400) < ANALYST_FMP_RESERVE:
            asked |= self._fmp_consensus(ticker, wait=False)
        return asked

    def _stale(self, ticker: str, source: str, fresh_days: int) -> bool:
        at = store.analyst_pulled_at(self.db, ticker, source)
        return at is None or utcnow() - at >= dt.timedelta(days=fresh_days)

    def _finnhub_trends(self, ticker: str, wait: bool) -> bool:
        rows = self._call("finnhub", "stock/recommendation", self.sources["finnhub"].recommendations, ticker, wait=wait)
        if rows is None:
            return False                    # not configured, limited or failed: asked again on the next run
        store.save_analyst_trends(self.db, ticker, rows, "finnhub", utcnow())
        store.mark_analyst_pull(self.db, ticker, "finnhub", len(rows))
        return True

    def _fmp_consensus(self, ticker: str, wait: bool) -> bool:
        fmp: FMP = self.sources["fmp"]
        grades = self._call("fmp", "grades-consensus", fmp.grades_consensus, ticker, wait=wait)
        targets = self._call("fmp", "price-target-consensus", fmp.price_targets, ticker, wait=wait)
        if grades is None and targets is None:
            return False                    # both failed or were refused: asked again on the next run
        if grades or targets:
            store.save_analyst_consensus(self.db, ticker, grades, targets, "fmp", utcnow())
        store.mark_analyst_pull(self.db, ticker, "fmp", int(bool(grades)) + int(bool(targets)))
        return True

    def pull_market_daily(self, today: dt.date | None = None, sessions: int = MARKET_DAILY_SESSIONS) -> dict:
        """Massive's whole-market daily file (close and volume of every ticker, one call a session) for the last
        `sessions` sessions, each fetched once, newest first, paced at Massive's 5 calls a minute. The first run fills
        about a month; later runs add the newest session. Then old sessions are pruned and search's popularity (the
        20-session average dollar volume) is refreshed."""
        today = today or dt.date.today()
        cal = Calendar.load(self.db)
        wanted, day = [], today
        while len(wanted) < sessions:
            day = cal.previous_session(day)
            wanted.append(day)
        have = store.market_days(self.db)
        massive: Massive = self.sources["massive"]
        out: dict[str, Any] = {"sessions": 0, "rows": 0, "stopped": None}
        for day in wanted:
            if day in have:
                continue
            grouped = self._call("massive", "grouped-daily", massive.grouped_daily, day)
            if grouped is None:
                out["stopped"] = day.isoformat()     # refused or failed: the next run carries on from here
                break
            out["rows"] += store.save_market_daily(self.db, day, grouped, "massive", utcnow())
            out["sessions"] += 1
        store.prune_market_daily(self.db, today - dt.timedelta(days=MARKET_DAILY_KEEP_DAYS))
        out["popular"] = store.refresh_popularity(self.db)
        return out

    def pull_ticker_reference(self) -> dict:
        """Massive's list of active US tickers (about 13 pages, paced): each ticker's type, so search can leave out
        warrants, rights and units and keep ETFs, and the names of tickers the SEC's list lacks (most ETFs). Then the
        tickers no list typed are typed by their symbol."""
        massive: Massive = self.sources["massive"]
        out = {"tickers": 0, "pages": 0, "complete": False}
        cursor = None
        for _ in range(40):                         # 40,000 tickers at most: the list has about 13,000
            page = self._call("massive", "reference/tickers", massive.ticker_page, cursor)
            if page is None:
                break
            rows, cursor = page
            out["tickers"] += store.save_ticker_reference(self.db, rows, "massive", utcnow())
            out["pages"] += 1
            if not cursor:
                out["complete"] = True
                break
        out["typed_by_symbol"] = store.mark_suffix_kinds(self.db)
        return out

    def build_universe(self, day: dt.date | None = None, size: int = 50) -> dict:
        """The whole market's day (from the stored daily file, else one Massive call): the most traded names (by
        dollar volume) above $10 that moved at least 2% that day become today's scan universe, beside the tracked
        tickers."""
        massive: Massive = self.sources["massive"]
        day = day or Calendar.load(self.db).previous_session(dt.date.today())
        grouped = store.market_day(self.db, day) or self._call("massive", "grouped-daily", massive.grouped_daily, day)
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
        """Splits, cash dividends, name changes and mergers for every tracked ticker, dated from 40 days back (a
        dividend is paid weeks after it goes ex) to 40 days ahead, in one call."""
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
        days, its whole chain stored. The sell-puts module reads only stored chains. Tradier (the owner's account)
        when its key is set; until then Alpaca's free indicative feed, with IV and delta computed."""
        if not configured().get("tradier") and configured().get("alpaca"):
            return self._pull_chains_alpaca()
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

    def _pull_chains_alpaca(self) -> dict:
        alpaca: Alpaca = self.sources["alpaca"]
        today = dt.date.today()
        out: dict[str, Any] = {"tickers": 0, "options": 0, "no_expiry": [], "source": "alpaca"}
        for ticker in store.put_candidates(self.db):
            puts = self._call("alpaca", "option-contracts", alpaca.option_contracts, ticker,
                              today + dt.timedelta(days=PUT_DTE[0]), today + dt.timedelta(days=PUT_DTE[1]), "put")
            if puts is None:
                break                          # limited or failing: the next tickers would be refused too
            fits = sorted({d for c in puts if (d := dt.date.fromisoformat(str(c.get("expiration_date"))[:10]))})
            if not fits:
                out["no_expiry"].append(ticker)
                continue
            expiry = min(fits, key=lambda d: abs((d - today).days - 38))
            snaps = self._call("alpaca", "option-snapshots", alpaca.option_snapshots, ticker, expiry, "put")
            if not snaps:
                continue
            oi = {c["symbol"]: c for c in puts if str(c.get("expiration_date"))[:10] == expiry.isoformat()}
            raw = [_alpaca_option(sym, s, oi.get(sym) or {}) for sym, s in sorted(snaps.items())]
            greeks.fill(raw, store.spot_of(self.db, ticker), today)
            out["options"] += len(store.save_chain(self.db, raw, utcnow(), "alpaca"))
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
