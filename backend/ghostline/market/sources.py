"""The free sources, each a thin, testable client: a key from the environment (GHOSTLINE_KEY_<SOURCE>), one httpx
client (tests inject a MockTransport), answers turned into the dataclasses of ghostline.market. No source computes
anything, and none is called without a key: NotConfigured stops the job with a logged reason.

Keys travel in headers wherever the source accepts one; FRED takes only a query parameter. A failed call raises
SourceError, whose text never holds the query: httpx's own error names the whole address, key included, and that text
is stored in data_pulls and logged (FMP's key reached staging's data_pulls that way on 2026-10-30).

Endpoints (read 2026-10, each a public free tier):
  Alpaca   https://data.alpaca.markets/v2  bars, latest quotes (IEX feed)   keys: APCA-API-KEY-ID + APCA-API-SECRET-KEY
           https://paper-api.alpaca.markets/v2/calendar  the exchange calendar (the key is a paper key)
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
from zoneinfo import ZoneInfo

import httpx

from ghostline.market import (
    Bar,
    CorporateAction,
    Earning,
    Filing,
    MacroPoint,
    MarketSession,
    NewsItem,
    NotConfigured,
    Quote,
    SourceError,
    dec,
)

UA = "Ghostline/0.2 (personal research tool; contact: owner)"
REPORT_HOURS = ("bmo", "amc", "dmh")             # Finnhub: before the open, after the close, during the session
TRADING = "https://paper-api.alpaca.markets/v2"     # Alpaca's trading API, where the calendar lives
TickerRef = tuple[str, str, str | None, str | None]   # Massive's ticker list: ticker, name, type, CIK
ALPACA_SCREENER = "https://data.alpaca.markets/v1beta1/screener/stocks"   # movers and most active
MASSIVE_V3 = "https://api.massive.com/v3"           # Massive's reference data (ticker names and types)
ALPACA_OPTIONS = "https://data.alpaca.markets/v1beta1/options"   # option snapshots, feed "indicative" (free)
ALPACA_V1 = "https://data.alpaca.markets/v1"         # Alpaca's corporate actions
ACTION_KINDS = (("forward_split", "forward_splits"), ("reverse_split", "reverse_splits"),
                ("cash_dividend", "cash_dividends"), ("name_change", "name_changes"), ("stock_merger", "stock_mergers"),
                ("cash_merger", "cash_mergers"), ("stock_and_cash_merger", "stock_and_cash_mergers"))


def _corporate_action(kind: str, a: dict) -> CorporateAction | None:
    """One of Alpaca's actions, or None when it lacks what applying it needs. Field names read live 2026-11-01: splits
    and dividends carry symbol / ex_date; a name change old_symbol / new_symbol / process_date; a merger
    acquiree_symbol / acquirer_symbol / effective_date with acquiree_rate and acquirer_rate (stock), rate (cash) or
    cash_rate (stock and cash). The holder's side is the old symbol: Alpaca's symbols filter also returns actions where
    a tracked ticker is the acquirer, and those change nothing for a holder of the acquirer."""
    if not a.get("id"):
        return None
    if kind == "name_change":
        old, new, day = a.get("old_symbol"), a.get("new_symbol"), _date(a.get("process_date"))
        if not (old and new and day) or old == new:
            return None
        return CorporateAction(str(a["id"]), kind, str(old).upper(), day, None, None, Decimal(1), Decimal(1), None,
                               str(new).upper())
    if kind.endswith("_merger"):
        old, day = a.get("acquiree_symbol"), _date(a.get("effective_date") or a.get("process_date"))
        if not (old and day):
            return None
        new = a.get("acquirer_symbol")
        cash = a.get("rate") if kind == "cash_merger" else a.get("cash_rate")
        return CorporateAction(str(a["id"]), kind, str(old).upper(), day, None, _date(a.get("payable_date")),
                               dec(a.get("acquiree_rate")), dec(a.get("acquirer_rate")), dec(cash),
                               str(new).upper() if new else None)
    ex = _date(a.get("ex_date"))
    if not (ex and a.get("symbol")):
        return None
    return CorporateAction(str(a["id"]), kind, str(a["symbol"]).upper(), ex, _date(a.get("record_date")),
                           _date(a.get("payable_date")), dec(a.get("old_rate")), dec(a.get("new_rate")),
                           dec(a.get("rate")))
MAX_PAGES = 20
NEW_YORK = ZoneInfo("America/New_York")

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


def _rfc3339(d: dt.date | dt.datetime) -> str:
    """A day as YYYY-MM-DD; a moment in UTC with a Z, the form Alpaca documents."""
    if isinstance(d, dt.datetime):
        return d.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return d.isoformat()


def _alpaca_option(symbol: str, snap: dict, contract: dict) -> dict:
    """An Alpaca contract and its snapshot as one chain row. A bid or ask of 0 means none (Alpaca writes 0)."""
    q, t, day = snap.get("latestQuote") or {}, snap.get("latestTrade") or {}, snap.get("dailyBar") or {}
    return {"symbol": symbol, "bid": q.get("bp") or None, "ask": q.get("ap") or None, "last": t.get("p"),
            "volume": day.get("v"), "open_interest": contract.get("open_interest"),
            "open_interest_date": contract.get("open_interest_date"), "greeks": {}}


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

    def bars(self, ticker: str, start: dt.date | dt.datetime, end: dt.date | dt.datetime,
             timeframe: str = "1Day") -> list[Bar]:
        """Bars on the IEX feed, split-adjusted. A day, or an exact moment for intraday bars (5Min, 30Min). IEX, not
        SIP: the free plan refuses SIP for the latest 15 minutes ("subscription does not permit querying recent SIP
        data", checked 2026-11-01), while IEX answers live; its volume is IEX's share of the market only."""
        j = self.get(f"/stocks/{ticker}/bars", params={"timeframe": timeframe, "start": _rfc3339(start),
                                                       "end": _rfc3339(end), "limit": 10000, "adjustment": "split",
                                                       "feed": "iex"}, headers=self._headers())
        out = []
        for b in j.get("bars") or []:
            ts = _stamp(b.get("t"))
            if ts:
                out.append(Bar(ts, dec(b["o"]), dec(b["h"]), dec(b["l"]), dec(b["c"]), dec(b.get("v")) or Decimal(0)))
        return out

    def calendar(self, start: dt.date, end: dt.date) -> list[MarketSession]:
        """Every session from start to end: open and close in New York time (early closes included) and the day a sale
        made in it settles. A weekday missing from the answer is a closed day."""
        j = self.fetch(TRADING + "/calendar", params={"start": start.isoformat(), "end": end.isoformat()},
                       headers=self._headers())
        out = []
        for d in j or []:
            day, settles = _date(d.get("date")), _date(d.get("settlement_date"))
            try:
                opens, closes = dt.time.fromisoformat(d["open"]), dt.time.fromisoformat(d["close"])
            except (KeyError, TypeError, ValueError):
                continue
            if day and settles:
                out.append(MarketSession(day, dt.datetime.combine(day, opens, tzinfo=NEW_YORK),
                                         dt.datetime.combine(day, closes, tzinfo=NEW_YORK), settles))
        return out

    def corporate_actions(self, tickers: list[str], start: dt.date, end: dt.date) -> list[CorporateAction]:
        """Splits, cash dividends, name changes and mergers for the tickers, dated from start to end, every page (at
        most 20). Read live 2026-10-30: NVDA's 10-for-1 split of 2024-06-10 came back as new_rate 10, old_rate 1."""
        out: list[CorporateAction] = []
        token = None
        for _ in range(MAX_PAGES):
            params = {"symbols": ",".join(tickers), "types": ",".join(k for k, _ in ACTION_KINDS),
                      "start": start.isoformat(), "end": end.isoformat(), "limit": 1000}
            if token:
                params["page_token"] = token
            j = self.fetch(ALPACA_V1 + "/corporate-actions", params=params, headers=self._headers())
            groups = j.get("corporate_actions") or {}
            for kind, key in ACTION_KINDS:
                for a in groups.get(key) or []:
                    action = _corporate_action(kind, a)
                    if action:
                        out.append(action)
            token = j.get("next_page_token")
            if not token:
                break
        return out

    # ── options, while Tradier waits (checked live 2026-11-01) ─────────────────────────────────────────────────
    # The contract list (trading API) gives expiry, strike, type and open interest dated the day before; the free
    # "indicative" snapshots give each contract's latest quote, trade and daily bar, but no Greeks or IV: those are
    # computed (market/greeks.py). Indicative prices are Alpaca's estimate, not the exchanges' own quote.

    def option_contracts(self, ticker: str, start: dt.date, end: dt.date, kind: str | None = None) -> list[dict]:
        """Every active contract of `ticker` expiring from start to end, every page."""
        out: list[dict] = []
        token = None
        for _ in range(MAX_PAGES):
            params = {"underlying_symbols": ticker, "expiration_date_gte": start.isoformat(),
                      "expiration_date_lte": end.isoformat(), "status": "active", "limit": 10000}
            if kind:
                params["type"] = kind
            if token:
                params["page_token"] = token
            j = self.fetch(TRADING + "/options/contracts", params=params, headers=self._headers())
            out += j.get("option_contracts") or []
            token = j.get("next_page_token")
            if not token:
                break
        return out

    def option_snapshots(self, ticker: str, expiry: dt.date, kind: str | None = None) -> dict[str, dict]:
        """The indicative snapshot of every contract of one expiry, every page: OCC symbol → snapshot."""
        out: dict[str, dict] = {}
        token = None
        for _ in range(MAX_PAGES):
            params = {"feed": "indicative", "expiration_date": expiry.isoformat(), "limit": 1000}
            if kind:
                params["type"] = kind
            if token:
                params["page_token"] = token
            j = self.fetch(f"{ALPACA_OPTIONS}/snapshots/{ticker}", params=params, headers=self._headers())
            out.update(j.get("snapshots") or {})
            token = j.get("next_page_token")
            if not token:
                break
        return out

    def option_chain(self, ticker: str, expiry: dt.date) -> list[dict]:
        """One expiry's chain in the shape a Tradier chain has (symbol, bid, ask, last, volume, open_interest,
        greeks), so the same parser stores either; greeks stay empty until market/greeks.py fills them."""
        contracts = {c["symbol"]: c for c in self.option_contracts(ticker, expiry, expiry)}
        snaps = self.option_snapshots(ticker, expiry)
        return [_alpaca_option(sym, snaps.get(sym) or {}, contracts.get(sym) or {})
                for sym in sorted(set(contracts) | set(snaps))]

    def option_expirations(self, ticker: str, start: dt.date, end: dt.date) -> list[dt.date]:
        """The expiries from start to end, from the put contracts (every expiry lists puts)."""
        return sorted({d for c in self.option_contracts(ticker, start, end, "put")
                       if (d := _date(c.get("expiration_date")))})

    def option_quotes(self, symbols: list[str]) -> list[Quote]:
        """Latest indicative quotes for option contracts by OCC symbol (a practice position's price)."""
        out: list[Quote] = []
        for i in range(0, len(symbols), 100):
            j = self.fetch(f"{ALPACA_OPTIONS}/snapshots", headers=self._headers(),
                           params={"symbols": ",".join(symbols[i:i + 100]), "feed": "indicative"})
            for sym, s in (j.get("snapshots") or {}).items():
                q, t = s.get("latestQuote") or {}, s.get("latestTrade") or {}
                ts = _stamp(q.get("t")) or _stamp(t.get("t"))
                if ts:
                    out.append(Quote(sym, dec(q.get("bp")), dec(q.get("ap")), dec(t.get("p")), ts, delayed=True))
        return out

    def movers(self, top: int = 50) -> dict:
        """The whole market's top gainers and losers right now (symbol, price, change, percent_change), live in the
        session; outside it, the last session's."""
        j = self.fetch(ALPACA_SCREENER + "/movers", params={"top": top}, headers=self._headers())
        return {"gainers": j.get("gainers") or [], "losers": j.get("losers") or [],
                "as_of": _stamp(j.get("last_updated"))}

    def most_actives(self, top: int = 50) -> dict:
        """The most traded stocks by volume right now (symbol, volume, trade_count)."""
        j = self.fetch(ALPACA_SCREENER + "/most-actives", params={"top": top, "by": "volume"}, headers=self._headers())
        return {"active": j.get("most_actives") or [], "as_of": _stamp(j.get("last_updated"))}

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


    def ticker_page(self, cursor: str | None = None) -> tuple[list[TickerRef], str | None]:
        """One page (up to 1,000) of active US stock tickers: (ticker, name, type, cik), and the next page's cursor.
        Types: CS common stock, ETF, ADRC, PFD, WARRANT, RIGHT, UNIT, FUND, ETN and a few more."""
        params = {"market": "stocks", "active": "true", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        j = self.fetch(MASSIVE_V3 + "/reference/tickers", params=params,
                       headers={"Authorization": f"Bearer {self.key()}"})
        rows = [(str(r["ticker"]).upper(), str(r.get("name") or r["ticker"])[:300], r.get("type"), r.get("cik"))
                for r in (j.get("results") or []) if r.get("ticker")]
        nxt = httpx.URL(j["next_url"]).params.get("cursor") if j.get("next_url") else None
        return rows, nxt


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

    def grades_consensus(self, ticker: str) -> dict:
        """How many analysts grade the stock strong buy to strong sell, and FMP's one-word consensus; {} when FMP has
        none (a fund, a small company)."""
        j = self.get("/grades-consensus", params={"symbol": ticker}, headers=self._headers())
        r = (j[0] if isinstance(j, list) and j else j) or None
        if not isinstance(r, dict) or not r:
            return {}
        return {"strong_buy": r.get("strongBuy"), "buy": r.get("buy"), "hold": r.get("hold"), "sell": r.get("sell"),
                "strong_sell": r.get("strongSell"), "consensus": r.get("consensus")}

    def price_targets(self, ticker: str) -> dict:
        """Analysts' price targets: the highest, the lowest, the consensus and the median; {} when FMP has none."""
        j = self.get("/price-target-consensus", params={"symbol": ticker}, headers=self._headers())
        r = (j[0] if isinstance(j, list) and j else j) or None
        if not isinstance(r, dict) or not r:
            return {}
        return {"target_high": dec(r.get("targetHigh")), "target_low": dec(r.get("targetLow")),
                "target_consensus": dec(r.get("targetConsensus")), "target_median": dec(r.get("targetMedian"))}

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
        """Report dates with the EPS estimate and actual, when in the day the company reports ("hour": bmo, amc or
        dmh; empty when not announced) and the revenue estimate and actual."""
        j = self.get("/calendar/earnings", params={"symbol": ticker, "from": since.isoformat(),
                                                   "to": until.isoformat()}, headers=self._headers())
        return [Earning(ticker, d, dec(e.get("epsEstimate")), dec(e.get("epsActual")),
                        e.get("hour") if e.get("hour") in REPORT_HOURS else None,
                        dec(e.get("revenueEstimate")), dec(e.get("revenueActual")))
                for e in (j.get("earningsCalendar") or []) if (d := _date(e.get("date")))]

    def recommendations(self, ticker: str) -> list[dict]:
        """Analysts' recommendation trends, one row per month: how many say strong buy, buy, hold, sell and strong
        sell (free plan). Finnhub's price targets need a paid plan (403), so they come from FMP."""
        j = self.get("/stock/recommendation", params={"symbol": ticker}, headers=self._headers())
        return [{"period": d, "strong_buy": int(r.get("strongBuy") or 0), "buy": int(r.get("buy") or 0),
                 "hold": int(r.get("hold") or 0), "sell": int(r.get("sell") or 0),
                 "strong_sell": int(r.get("strongSell") or 0)}
                for r in (j if isinstance(j, list) else []) if isinstance(r, dict) and (d := _date(r.get("period")))]

    def industry(self, ticker: str) -> str | None:
        """The company's industry from its profile (free plan): Technology, Semiconductors, Banking, Construction...;
        None for a fund or a company Finnhub does not cover."""
        p = self.get("/stock/profile2", params={"symbol": ticker}, headers=self._headers())
        name = (p or {}).get("finnhubIndustry") if isinstance(p, dict) else None
        return str(name)[:80] if name and name != "N/A" else None

    def quote(self, ticker: str) -> Quote | None:
        q = self.get("/quote", params={"symbol": ticker}, headers=self._headers())
        ts = _stamp(q.get("t"))
        return Quote(ticker, None, None, dec(q.get("c")), ts, delayed=True) if ts and q.get("c") else None


class Edgar(Source):
    name = "edgar"
    base_url = "https://data.sec.gov"
    needs_key = False

    def directory(self) -> list[tuple[str, str, str]]:
        """Every listed company the SEC knows: (ticker, name, CIK with 10 digits), in the file's order."""
        out = []
        for row in self.fetch("https://www.sec.gov/files/company_tickers.json").values():
            ticker, name = str(row.get("ticker") or "").upper(), str(row.get("title") or "").strip()
            if ticker and name and row.get("cik_str") is not None:
                out.append((ticker, name, f"{int(row['cik_str']):010d}"))
        return out

    def _concept(self, cik: str, tags: tuple[str, ...], unit: str) -> list[dict]:
        """One XBRL concept's reported values, from the first tag the company files (a missing tag answers 404)."""
        for tag in tags:
            try:
                j = self.get(f"/api/xbrl/companyconcept/CIK{cik}/us-gaap/{tag}.json")
            except SourceError:
                continue
            rows = (j.get("units") or {}).get(unit) or []
            if rows:
                return rows
        return []

    def eps_history(self, cik: str) -> tuple[list[tuple[dt.date, Decimal]],
                                              list[tuple[dt.date, Decimal, Decimal | None]]]:
        """Reported diluted EPS (basic when diluted is not filed): 3-month quarters and fiscal years, newest first, each
        period's latest filing (a restatement wins); a year carries its revenue when it is filed."""
        def periods(rows: list[dict], lo: int, hi: int) -> dict[dt.date, Decimal]:
            out: dict[dt.date, tuple[str, Decimal]] = {}
            for r in rows:
                start, end = _date(r.get("start")), _date(r.get("end"))
                if not (start and end and r.get("val") is not None and lo <= (end - start).days <= hi):
                    continue
                filed = str(r.get("filed") or "")
                if end not in out or filed >= out[end][0]:
                    out[end] = (filed, Decimal(str(r["val"])))
            return {k: v for k, (_, v) in out.items()}

        eps = self._concept(cik, ("EarningsPerShareDiluted", "EarningsPerShareBasic"), "USD/shares")
        revenue_tags = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues")
        revenue = periods(self._concept(cik, revenue_tags, "USD"), 350, 380)
        quarters = sorted(periods(eps, 80, 100).items(), reverse=True)
        years = [(end, v, revenue.get(end)) for end, v in sorted(periods(eps, 350, 380).items(), reverse=True)]
        return quarters, years

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
