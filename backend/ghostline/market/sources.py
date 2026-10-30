"""The free sources, each a thin, testable client: a key from the environment (GHOSTLINE_KEY_<SOURCE>), one httpx
client (tests inject a MockTransport), answers turned into the dataclasses of ghostline.market. No source computes
anything, and none is called without a key: NotConfigured stops the job with a logged reason.

Keys travel in headers wherever the source accepts one; FRED takes only a query parameter. A failed call raises
SourceError, whose text never holds the query: httpx's own error names the whole address, key included, and that text
is stored in data_pulls and logged (FMP's key reached staging's data_pulls that way on 2026-10-30).

Endpoints (read 2026-10, each a public free tier):
  Alpaca   https://data.alpaca.markets/v2  bars, latest quotes (IEX feed)   keys: APCA-API-KEY-ID + APCA-API-SECRET-KEY
  Massive  https://api.massive.com/v2       grouped daily bars (the whole market in one call), 5/min
  FMP      https://financialmodelingprep.com/stable  profile, ratios, income, earnings history, 250/day
           (free plan, checked 2026-10-30: earnings history at most 5 quarters, and not for every symbol)
  Finnhub  https://finnhub.io/api/v1        company news, earnings calendar, quote
  EDGAR    https://data.sec.gov             submissions (filings), no key, a User-Agent with contact
  FRED     https://api.stlouisfed.org/fred  series observations
  Tradier  https://api.tradier.com/v1       quotes, option chains (the owner's account)
  GDELT    https://api.gdeltproject.org/api/v2/doc/doc  news tone, no key
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from decimal import Decimal
from typing import Any

import httpx

from ghostline.market import Bar, Earning, Filing, MacroPoint, NewsItem, NotConfigured, Quote, SourceError, dec

UA = "Ghostline/0.2 (personal research tool; contact: owner)"

# httpx logs every request's whole address at INFO, and FRED's key travels in its query: the worker logs at INFO and
# its log goes to Loki, so httpx speaks only from WARNING up in any process that calls a source.
logging.getLogger("httpx").setLevel(logging.WARNING)


def key_for(name: str) -> str | None:
    return os.environ.get(f"GHOSTLINE_KEY_{name.upper()}") or None


def _masked(text: str, limit: int = 160) -> str:
    """A source's own error text, short and on one line, with every key we hold replaced (a source may echo one)."""
    for name, value in os.environ.items():
        if name.startswith("GHOSTLINE_KEY_") and len(value) >= 8:
            text = text.replace(value, "***")
    return " ".join(text.split())[:limit]


def _date(s: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:  # noqa: BLE001
        return None


def _stamp(s: Any) -> dt.datetime | None:
    if s is None:
        return None
    if isinstance(s, int | float):
        return dt.datetime.fromtimestamp(float(s) / (1000 if float(s) > 1e11 else 1), tz=dt.UTC)
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


class Source:
    name = "base"
    base_url = ""
    needs_key = True

    def __init__(self, http: httpx.Client | None = None) -> None:
        self.http = http or httpx.Client(timeout=30, headers={"User-Agent": UA})

    def key(self) -> str:
        k = key_for(self.name)
        if self.needs_key and not k:
            raise NotConfigured(f"{self.name}: GHOSTLINE_KEY_{self.name.upper()} is not set")
        return k or ""

    def get(self, path: str, params: dict | None = None, headers: dict | None = None) -> Any:
        return self.fetch(self.base_url + path, params, headers)

    def fetch(self, url: str, params: dict | None = None, headers: dict | None = None) -> Any:
        """One GET. A failure raises SourceError with the path, the status and the source's reason, never the query;
        `from None` drops httpx's exception, whose text and traceback hold the whole address."""
        where = f"{self.name} {httpx.URL(url).path}"
        try:
            r = self.http.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise SourceError(f"{where}: {type(exc).__name__}") from None
        if r.is_error:
            raise SourceError(f"{where}: {r.status_code} {r.reason_phrase}: {_masked(r.text)}")
        return r.json()


class Alpaca(Source):
    name = "alpaca"
    base_url = "https://data.alpaca.markets/v2"

    def _headers(self) -> dict:
        k = self.key()
        secret = os.environ.get("GHOSTLINE_KEY_ALPACA_SECRET") or ""
        if not secret:
            raise NotConfigured("alpaca: GHOSTLINE_KEY_ALPACA_SECRET is not set")
        return {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": secret}

    def bars(self, ticker: str, start: dt.date, end: dt.date, timeframe: str = "1Day") -> list[Bar]:
        j = self.get(f"/stocks/{ticker}/bars", params={"timeframe": timeframe, "start": start.isoformat(),
                                                       "end": end.isoformat(), "limit": 10000, "adjustment": "split",
                                                       "feed": "iex"}, headers=self._headers())
        out = []
        for b in j.get("bars") or []:
            ts = _stamp(b.get("t"))
            if ts:
                out.append(Bar(ts, dec(b["o"]), dec(b["h"]), dec(b["l"]), dec(b["c"]), dec(b.get("v")) or Decimal(0)))
        return out

    def quotes(self, tickers: list[str]) -> list[Quote]:
        j = self.get("/stocks/quotes/latest", params={"symbols": ",".join(tickers), "feed": "iex"},
                     headers=self._headers())
        out = []
        for sym, q in (j.get("quotes") or {}).items():
            ts = _stamp(q.get("t"))
            if ts:
                out.append(Quote(sym, dec(q.get("bp")), dec(q.get("ap")), None, ts, delayed=True))
        return out


class Massive(Source):
    name = "massive"
    base_url = "https://api.massive.com/v2"

    def grouped_daily(self, day: dt.date) -> dict[str, Bar]:
        j = self.get(f"/aggs/grouped/locale/us/market/stocks/{day.isoformat()}", params={"adjusted": "true"},
                     headers={"Authorization": f"Bearer {self.key()}"})
        out = {}
        for r in j.get("results") or []:
            ts = _stamp(r.get("t"))
            if ts and r.get("T"):
                out[r["T"]] = Bar(ts, dec(r["o"]), dec(r["h"]), dec(r["l"]), dec(r["c"]), dec(r.get("v")) or Decimal(0))
        return out


class FMP(Source):
    name = "fmp"
    base_url = "https://financialmodelingprep.com/stable"

    def _headers(self) -> dict:
        return {"apikey": self.key()}

    def profile(self, ticker: str) -> dict:
        j = self.get("/profile", params={"symbol": ticker}, headers=self._headers())
        return (j[0] if isinstance(j, list) and j else j) or {}

    def ratios(self, ticker: str) -> dict:
        j = self.get("/ratios-ttm", params={"symbol": ticker}, headers=self._headers())
        return (j[0] if isinstance(j, list) and j else j) or {}

    def income_annual(self, ticker: str, limit: int = 5) -> list[dict]:
        """Annual EPS and revenue, newest first (CANSLIM's A)."""
        j = self.get("/income-statement", params={"symbol": ticker, "period": "annual", "limit": limit},
                     headers=self._headers())
        return [{"date": r.get("date"), "eps": r.get("eps"), "revenue": r.get("revenue")} for r in (j or [])
                if isinstance(r, dict)]

    def earnings(self, ticker: str) -> list[Earning]:
        """The last reports, newest first. The free plan refuses a limit above 5 (402), and some symbols outright."""
        j = self.get("/earnings", params={"symbol": ticker, "limit": 5}, headers=self._headers())
        return [Earning(ticker, d, dec(e.get("epsEstimated")), dec(e.get("epsActual")))
                for e in (j or []) if (d := _date(e.get("date")))]


class Finnhub(Source):
    name = "finnhub"
    base_url = "https://finnhub.io/api/v1"

    def _headers(self) -> dict:
        return {"X-Finnhub-Token": self.key()}

    def news(self, ticker: str, since: dt.date, until: dt.date) -> list[NewsItem]:
        j = self.get("/company-news", params={"symbol": ticker, "from": since.isoformat(), "to": until.isoformat()},
                     headers=self._headers())
        out = []
        for n in j or []:
            ts = _stamp(n.get("datetime"))
            if ts and n.get("headline") and n.get("url"):
                out.append(NewsItem(ticker, n["headline"][:500], n["url"], ts))
        return out

    def earnings(self, ticker: str, since: dt.date, until: dt.date) -> list[Earning]:
        j = self.get("/calendar/earnings", params={"symbol": ticker, "from": since.isoformat(),
                                                   "to": until.isoformat()}, headers=self._headers())
        return [Earning(ticker, d, dec(e.get("epsEstimate")), dec(e.get("epsActual")))
                for e in (j.get("earningsCalendar") or []) if (d := _date(e.get("date")))]

    def quote(self, ticker: str) -> Quote | None:
        q = self.get("/quote", params={"symbol": ticker}, headers=self._headers())
        ts = _stamp(q.get("t"))
        return Quote(ticker, None, None, dec(q.get("c")), ts, delayed=True) if ts and q.get("c") else None


class Edgar(Source):
    name = "edgar"
    base_url = "https://data.sec.gov"
    needs_key = False

    def cik(self, ticker: str) -> str | None:
        for row in self.fetch("https://www.sec.gov/files/company_tickers.json").values():
            if str(row.get("ticker", "")).upper() == ticker.upper():
                return f"{int(row['cik_str']):010d}"
        return None

    def filings(self, ticker: str, forms: tuple[str, ...] = ("10-K", "10-Q", "8-K", "4")) -> list[Filing]:
        cik = self.cik(ticker)
        if not cik:
            return []
        j = self.get(f"/submissions/CIK{cik}.json")
        recent = (j.get("filings") or {}).get("recent") or {}
        out = []
        for acc, form, filed, doc in zip(recent.get("accessionNumber", []), recent.get("form", []),
                                         recent.get("filingDate", []), recent.get("primaryDocument", []), strict=False):
            if form in forms and (d := _date(filed)):
                folder = acc.replace('-', '')
                url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/{doc}" if doc else None
                out.append(Filing(acc, ticker.upper(), form, d, url))
        return out


class FRED(Source):
    name = "fred"
    base_url = "https://api.stlouisfed.org/fred"

    def series(self, series_id: str, since: dt.date) -> list[MacroPoint]:
        # FRED accepts its key only as a query parameter: Source.fetch keeps it out of every error text
        j = self.get("/series/observations", params={"series_id": series_id, "api_key": self.key(),
                                                     "file_type": "json", "observation_start": since.isoformat()})
        return [MacroPoint(series_id, d, dec(o.get("value")) if o.get("value") != "." else None)
                for o in (j.get("observations") or []) if (d := _date(o.get("date")))]


class Tradier(Source):
    name = "tradier"
    base_url = "https://api.tradier.com/v1"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key()}", "Accept": "application/json"}

    def quotes(self, tickers: list[str]) -> list[Quote]:
        j = self.get("/markets/quotes", params={"symbols": ",".join(tickers), "greeks": "false"},
                     headers=self._headers())
        raw = ((j.get("quotes") or {}).get("quote")) or []
        if isinstance(raw, dict):
            raw = [raw]
        out = []
        for q in raw:
            ts = _stamp(q.get("trade_date")) or dt.datetime.now(dt.UTC)
            out.append(Quote(q.get("symbol"), dec(q.get("bid")), dec(q.get("ask")), dec(q.get("last")), ts,
                             delayed=True))
        return out

    def expirations(self, ticker: str) -> list[dt.date]:
        j = self.get("/markets/options/expirations", params={"symbol": ticker, "includeAllRoots": "true"},
                     headers=self._headers())
        return [d for s in ((j.get("expirations") or {}).get("date") or []) if (d := _date(s))]

    def chain(self, ticker: str, expiration: dt.date) -> list[dict]:
        j = self.get("/markets/options/chains", params={"symbol": ticker, "expiration": expiration.isoformat(),
                                                        "greeks": "true"}, headers=self._headers())
        raw = ((j.get("options") or {}).get("option")) or []
        return raw if isinstance(raw, list) else [raw]


class GDELT(Source):
    name = "gdelt"
    base_url = "https://api.gdeltproject.org/api/v2/doc/doc"
    needs_key = False

    def tone(self, query: str, days: int = 7) -> list[MacroPoint]:
        """Average news tone per day. GDELT answers hourly points dated like 20261023T230000Z (checked against the
        live service 2026-10-30); the hours of a day are averaged into one value."""
        j = self.get("", params={"query": query, "mode": "timelinetone", "format": "json", "timespan": f"{days}d"})
        series = (j.get("timeline") or [{}])[0].get("data") or []
        by_day: dict[dt.date, list[Decimal]] = {}
        for p in series:
            try:
                day = dt.datetime.strptime(str(p.get("date")), "%Y%m%dT%H%M%SZ").date()
            except ValueError:
                continue
            v = dec(p.get("value"))
            if v is not None:
                by_day.setdefault(day, []).append(v)
        return [MacroPoint(f"gdelt:{query}", d, (sum(vs, Decimal(0)) / len(vs)).quantize(Decimal("0.0001")))
                for d, vs in sorted(by_day.items())]


ALL: dict[str, type[Source]] = {s.name: s for s in (Alpaca, Massive, FMP, Finnhub, Edgar, FRED, Tradier, GDELT)}


def configured() -> dict[str, bool]:
    out = {}
    for name, cls in ALL.items():
        if not cls.needs_key:
            out[name] = True
        elif name == "alpaca":
            out[name] = bool(key_for("alpaca") and os.environ.get("GHOSTLINE_KEY_ALPACA_SECRET"))
        else:
            out[name] = bool(key_for(name))
    return out
