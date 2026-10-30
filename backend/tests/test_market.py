"""Market data: each source parsed from a recorded answer, every stored row with source and as_of, the free-tier
counters, jobs that skip without a key, the chart payload, and the routes."""
import datetime as dt
import json
import logging
from decimal import Decimal

import httpx
import pytest

from ghostline import indicators
from ghostline.config import Settings
from ghostline.db import Database
from ghostline.market import NotConfigured, RateLimited, store
from ghostline.market.jobs import Jobs
from ghostline.market.limits import LIMITS, Limiter
from ghostline.market.sources import FMP, FRED, Alpaca, Edgar, Finnhub, Massive, Tradier, configured
from tests.conftest import settings, sign_in

ANSWERS = {
    "data.alpaca.markets/v2/stocks/AAPL/bars": {"bars": [
        {"t": "2026-10-01T04:00:00Z", "o": 100, "h": 101.5, "l": 99, "c": 100.5, "v": 1000},
        {"t": "2026-10-02T04:00:00Z", "o": 100.5, "h": 103, "l": 100, "c": 102.25, "v": 1200}]},
    "data.alpaca.markets/v2/stocks/quotes/latest": {"quotes": {"AAPL": {"t": "2026-10-30T15:00:00.123Z", "bp": 101.1, "ap": 101.3}}},
    "financialmodelingprep.com/stable/earnings": [{"date": "2026-10-28", "epsEstimated": 1.5, "epsActual": 1.62}],
    "financialmodelingprep.com/stable/profile": [{"companyName": "Apple Inc.", "mktCap": 3000000000000}],
    "financialmodelingprep.com/stable/ratios-ttm": [{"peRatioTTM": 30.1}],
    "finnhub.io/api/v1/company-news": [{"datetime": 1761800000, "headline": "Apple does a thing", "url": "https://n/1"},
                                        {"datetime": 1761800001, "headline": "no url"}],
    "finnhub.io/api/v1/calendar/earnings": {"earningsCalendar": [{"date": "2027-01-28", "epsEstimate": 2.1}]},
    "www.sec.gov/files/company_tickers.json": {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}},
    "data.sec.gov/submissions/CIK0000320193.json": {"filings": {"recent": {
        "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"], "form": ["10-K", "SC 13G"],
        "filingDate": ["2026-10-20", "2026-10-21"], "primaryDocument": ["a10k.htm", "x.htm"]}}},
    "api.massive.com/v2/aggs/grouped/locale/us/market/stocks/2026-10-01": {"results": []},
    "api.stlouisfed.org/fred/series/observations": {"observations": [{"date": "2026-10-01", "value": "4.1"},
                                                                     {"date": "2026-10-02", "value": "."}]},
    "api.tradier.com/v1/markets/quotes": {"quotes": {"quote": {"symbol": "AAPL", "bid": 101.0, "ask": 101.2,
                                                                 "last": 101.1, "trade_date": 1761800000000}}},
}


def fake_http():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        key = request.url.host + request.url.path
        if key in ANSWERS:
            return httpx.Response(200, json=ANSWERS[key])
        return httpx.Response(404, json={"error": "no fixture for " + key})
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


@pytest.fixture
def keys(monkeypatch):
    for k in ("ALPACA", "ALPACA_SECRET", "MASSIVE", "FMP", "FINNHUB", "FRED", "TRADIER"):
        monkeypatch.setenv(f"GHOSTLINE_KEY_{k}", f"test-{k.lower()}")


def test_sources_parse_recorded_answers(keys):
    http, seen = fake_http()
    bars = Alpaca(http).bars("AAPL", dt.date(2026, 10, 1), dt.date(2026, 10, 2))
    assert [b.close for b in bars] == [Decimal("100.5"), Decimal("102.25")] and bars[0].volume == Decimal("1000")
    assert seen[-1].headers["APCA-API-KEY-ID"] == "test-alpaca"
    (q,) = Alpaca(http).quotes(["AAPL"])
    assert q.bid == Decimal("101.1") and q.delayed and q.ts.year == 2026
    assert FMP(http).earnings("AAPL")[0].actual == Decimal("1.62")
    assert FMP(http).profile("AAPL")["companyName"] == "Apple Inc."
    news = Finnhub(http).news("AAPL", dt.date(2026, 10, 1), dt.date(2026, 10, 30))
    assert [n.headline for n in news] == ["Apple does a thing"]      # the item without a url is dropped
    assert Finnhub(http).earnings("AAPL", dt.date(2026, 10, 1), dt.date(2027, 3, 1))[0].at == dt.date(2027, 1, 28)
    filings = Edgar(http).filings("AAPL")
    assert [f.form for f in filings] == ["10-K"] and filings[0].url.endswith("/000032019326000001/a10k.htm")
    pts = FRED(http).series("DGS10", dt.date(2026, 10, 1))
    assert pts[0].value == Decimal("4.1") and pts[1].value is None   # "." is FRED's missing value
    (tq,) = Tradier(http).quotes(["AAPL"])
    assert tq.last == Decimal("101.1") and seen[-1].headers["Authorization"] == "Bearer test-tradier"


def test_a_source_without_its_key_is_not_configured(monkeypatch):
    monkeypatch.delenv("GHOSTLINE_KEY_FMP", raising=False)
    http, _ = fake_http()
    with pytest.raises(NotConfigured):
        FMP(http).earnings("AAPL")
    assert configured()["fmp"] is False and configured()["edgar"] is True and configured()["gdelt"] is True


def test_rows_are_stored_with_source_and_as_of(fresh_db):
    db = Database(settings())
    http, _ = fake_http()
    now = dt.datetime(2026, 10, 30, 9, 0, tzinfo=dt.UTC)
    import os
    os.environ["GHOSTLINE_KEY_ALPACA"] = "k"
    os.environ["GHOSTLINE_KEY_ALPACA_SECRET"] = "s"
    bars = Alpaca(http).bars("AAPL", dt.date(2026, 10, 1), dt.date(2026, 10, 2))
    assert store.save_bars(db, "AAPL", "1D", bars, "alpaca", now) == 2
    assert store.save_bars(db, "AAPL", "1D", bars, "alpaca", now) == 2      # idempotent
    rows = store.bars(db, "AAPL", "1D")
    assert len(rows) == 2 and rows[0]["source"] == "alpaca" and rows[0]["as_of"] == now
    q = Alpaca(http).quotes(["AAPL"])
    store.save_quotes(db, q, "alpaca", now)
    latest = store.latest_quotes(db, ["AAPL", "MSFT"])
    assert set(latest) == {"AAPL"} and latest["AAPL"]["delayed"] is True
    db.execute("INSERT INTO watchlist (ticker) VALUES ('MSFT')")
    assert store.tracked_tickers(db) == ["MSFT"]
    db.close()


def test_the_free_tier_counter_refuses_before_the_limit(fresh_db):
    db = Database(settings())
    lim = Limiter(db)
    for _ in range(5):
        lim.check("massive")
        lim.record("massive", "grouped", "ok", rows=1)
    with pytest.raises(RateLimited):
        lim.check("massive")
    lim.check("alpaca")     # another source is not affected
    status = {s["source"]: s for s in lim.status()}
    assert status["massive"]["today"] == 5 and status["massive"]["last_ok"]
    db.close()


def test_jobs_skip_unconfigured_sources_and_store_the_rest(fresh_db, monkeypatch, keys):
    monkeypatch.delenv("GHOSTLINE_KEY_TRADIER")
    db = Database(settings())
    http, _ = fake_http()
    db.execute("INSERT INTO watchlist (ticker) VALUES ('AAPL')")
    jobs = Jobs(db, http)
    assert jobs.pull_bars() == {"tickers": 1, "bars": 2}
    out = jobs.pull_quotes()
    assert out == {"quotes": 1, "source": "alpaca"}            # Tradier skipped (no key), Alpaca answered
    pulls = db.fetch_all("SELECT source, status FROM data_pulls ORDER BY id")
    assert ("tradier", "skipped") in {(p["source"], p["status"]) for p in pulls}
    ne = jobs.pull_news_earnings()
    assert ne == {"news": 1, "earnings": 2, "filings": 1}
    assert jobs.pull_macro()["points"] == 2 * 6
    assert jobs.pull_fundamentals() == {"tickers": 1}
    db.close()


def test_indicators_from_known_series():
    closes = [float(x) for x in range(1, 61)]
    assert indicators.sma(closes, 5)[:5] == [None, None, None, None, 3.0]
    assert indicators.ema(closes, 3)[2] == 2.0 and round(indicators.ema(closes, 3)[3], 4) == 3.0
    r = indicators.rsi(closes, 14)
    assert r[13] is None and r[14] == 100.0                  # only gains
    m = indicators.macd(closes)
    assert m["macd"][25] is not None and m["signal"][32] is None and m["signal"][33] is not None
    assert len(m["histogram"]) == 60
    bb = indicators.bollinger(closes, 20)
    assert bb["middle"][19] == 10.5 and bb["upper"][19] > bb["middle"][19] > bb["lower"][19]
    highs = [10, 11, 12, 13, 12, 11, 10, 11, 12, 13.1, 12, 11, 10, 9, 10, 11, 12, 13, 12, 11, 10]
    lows = [h - 1 for h in highs]
    lv = indicators.levels([float(h) for h in highs], [float(x) for x in lows], [float(h) - 0.5 for h in highs])
    assert any(level.kind == "resistance" and abs(level.price - 13.03) < 0.1 for level in lv)


def test_chart_and_market_routes(client, keys):
    h = {"Authorization": f"Bearer {sign_in(client)}"}
    db: Database = client.app.state.db
    now = dt.datetime.now(dt.UTC)
    from ghostline.market import Bar
    bars = [Bar(now - dt.timedelta(days=30 - i), Decimal(100 + i), Decimal(101 + i), Decimal(99 + i), Decimal(100 + i),
                Decimal(1000)) for i in range(30)]
    store.save_bars(db, "AAPL", "1D", bars, "alpaca", now)
    r = client.get("/charts?ticker=aapl&timeframe=1M", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "AAPL" and len(body["bars"]) == 30 and body["source"] == "alpaca" and body["as_of"]
    assert body["sma20"][-1] is not None and body["sma20"][0] is None and body["rsi14"][-1] == 100.0
    assert client.get("/charts?ticker=AAPL&timeframe=2Y", headers=h).status_code == 422
    assert client.get("/charts?ticker=NOPE&timeframe=1M", headers=h).json()["bars"] == []
    assert client.post("/watchlist", json={"ticker": "msft", "note": "cloud"}, headers=h).status_code == 201
    w = client.get("/watchlist", headers=h).json()["watchlist"]
    assert w[0]["ticker"] == "MSFT" and w[0]["quote"] is None
    assert client.get("/quotes?tickers=AAPL,MSFT", headers=h).json()["missing"] == ["AAPL", "MSFT"]
    s = client.get("/market/status", headers=h).json()
    assert s["configured"]["edgar"] is True and s["tracked"] == ["MSFT"]
    assert client.get("/news-earnings?ticker=AAPL", headers=h).json() == {"ticker": "AAPL", "news": [], "earnings": [], "filings": []}
    assert client.delete("/watchlist/MSFT", headers=h).json()["removed"] == 1
    assert client.get("/charts?ticker=AAPL").status_code == 401


def test_the_worker_registers_the_plans_schedule(fresh_db):
    from apscheduler.schedulers.background import BackgroundScheduler

    from ghostline.worker import schedule
    db = Database(settings())
    sch = BackgroundScheduler(timezone="America/Los_Angeles")
    ids = schedule.register(sch, db)
    assert ids == ["pull_macro", "pull_tone", "build_universe", "pull_bars", "pull_news_earnings", "pull_fundamentals", "pull_chains", "scan_ideas", "pull_quotes", "fill_orders", "close_session", "claude_poll", "morning_brief", "ghost_sync", "invest_place", "ai_entries", "options_entries", "ai_exits", "alerts_check", "learning_run", "weekly_eval", "weekly_digest"]
    quotes = sch.get_job("pull_quotes")
    assert "day_of_week='mon-fri'" in str(quotes.trigger) and "hour='6-13'" in str(quotes.trigger)
    assert str(schedule.PT) == "America/Los_Angeles"
    db.close()


def test_settings_are_the_environment():
    assert Settings(DATABASE_URL="x").database_url == "x"
    assert json.loads('{"a": 1}')["a"] == 1


# ── spec gaps: chains before the scan, GDELT tone, the move around each report ─────────────────────────────
# The shape GDELT answered on 2026-10-30 (hourly points, the first four of the real answer)
GDELT_ANSWER = {"query_details": {"title": "\"stock market\"", "date_resolution": "hour"}, "timeline": [
    {"series": "Average Tone", "data": [{"date": "20261023T230000Z", "value": -1.1377},
                                        {"date": "20261024T000000Z", "value": -1.8871},
                                        {"date": "20261024T010000Z", "value": 0},
                                        {"date": "20261024T020000Z", "value": -1.3348}]}]}


def test_gdelt_tone_averages_the_hours_of_each_day():
    from ghostline.market.sources import GDELT

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "timelinetone" and request.url.params["timespan"] == "7d"
        return httpx.Response(200, json=GDELT_ANSWER)
    pts = GDELT(httpx.Client(transport=httpx.MockTransport(handler))).tone('"stock market"')
    assert [(p.series, p.ts, p.value) for p in pts] == [
        ('gdelt:"stock market"', dt.date(2026, 10, 23), Decimal("-1.1377")),
        ('gdelt:"stock market"', dt.date(2026, 10, 24), Decimal("-1.0740"))]


def _occ(ticker: str, expiry: dt.date, kind: str, strike: int) -> str:
    return f"{ticker}{expiry:%y%m%d}{kind}{strike * 1000:08d}"


def test_the_chain_pull_stores_the_expiry_nearest_38_days_for_stocks_the_owner_would_own(fresh_db, keys):
    today = dt.date.today()
    expiries = [today + dt.timedelta(days=d) for d in (10, 33, 40, 60)]
    put = _occ("AAPL", expiries[2], "P", 95)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/options/expirations"):
            if request.url.params["symbol"] == "MSFT":
                return httpx.Response(200, json={"expirations": {"date": [expiries[0].isoformat()]}})
            return httpx.Response(200, json={"expirations": {"date": [d.isoformat() for d in expiries]}})
        if path.endswith("/options/chains"):
            return httpx.Response(200, json={"options": {"option": [
                {"symbol": put, "bid": 1.2, "ask": 1.3, "last": 1.25, "volume": 10, "open_interest": 900,
                 "greeks": {"delta": -0.21, "mid_iv": 0.31}},
                {"symbol": "not-an-option"}]}})
        return httpx.Response(404)
    db = Database(settings())
    db.execute("INSERT INTO watchlist (ticker) VALUES ('AAPL'), ('MSFT'), ('SPY')")
    out = Jobs(db, httpx.Client(transport=httpx.MockTransport(handler))).pull_chains()
    assert out == {"tickers": 1, "options": 1, "no_expiry": ["MSFT"]}          # SPY is a market ticker, not a candidate
    chain_calls = [r for r in seen if r.url.path.endswith("/chains")]
    assert [r.url.params["expiration"] for r in chain_calls] == [expiries[2].isoformat()]   # 40 days beats 33
    row = db.fetch_one("SELECT strike, kind, bid, ask, open_interest, iv, delta FROM option_chains WHERE option_symbol = %s",
                       (put,))
    assert (row["strike"], row["kind"], row["open_interest"], row["delta"]) == (Decimal("95"), "put", Decimal("900"), Decimal("-0.21"))
    assert store.latest_quotes(db, [put])[put]["bid"] == Decimal("1.2")     # the chain doubles as the option's quote
    db.close()


def test_the_chain_pull_stops_when_tradier_is_not_configured(fresh_db, monkeypatch, keys):
    monkeypatch.delenv("GHOSTLINE_KEY_TRADIER")
    db = Database(settings())
    db.execute("INSERT INTO watchlist (ticker) VALUES ('AAPL'), ('MSFT')")
    http, seen = fake_http()
    assert Jobs(db, http).pull_chains() == {"tickers": 0, "options": 0, "no_expiry": []}
    assert seen == []                                                        # no call made without a key
    assert [p["status"] for p in db.fetch_all("SELECT status FROM data_pulls WHERE source = 'tradier'")] == ["skipped"]
    db.close()


def _gdelt(answers):
    """A GDELT that answers from a list (a status code or the recorded answer), and the queries it was asked."""
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.gdeltproject.org":
            return httpx.Response(404)
        asked.append(request.url.params["query"])
        a = answers.pop(0)
        if a == 429:
            return httpx.Response(429, text="Please limit requests to one every 5 seconds or contact ...")
        return httpx.Response(200, json=GDELT_ANSWER)
    return httpx.Client(transport=httpx.MockTransport(handler)), asked


def test_the_tone_pull_asks_for_one_stale_topic_per_run(fresh_db):
    db = Database(settings())
    http, asked = _gdelt([200, 200, 200])
    jobs = Jobs(db, http)
    day = dt.date(2026, 10, 24)                      # the recorded answer's newest day
    for _ in range(3):
        db.execute("DELETE FROM data_pulls")         # the per-minute cap is tested below
        assert jobs.pull_tone(today=day)["points"] == 2
    assert asked == ['"stock market"', '"India economy"', '"Federal Reserve"']
    assert jobs.pull_tone(today=day) == {"asked": None, "points": 0, "stale": 0}       # all fresh: no request
    assert len(asked) == 3
    assert jobs.pull_tone(today=day + dt.timedelta(days=1))["asked"] == '"stock market"'   # next day starts over
    db.close()


def test_a_refused_tone_pull_stops_and_never_retries_inside_the_minute(fresh_db):
    db = Database(settings())
    http, asked = _gdelt([429])
    jobs = Jobs(db, http)
    assert jobs.pull_tone(today=dt.date(2026, 10, 24)) == {"asked": '"stock market"', "points": 0, "stale": 3}
    assert jobs.pull_tone(today=dt.date(2026, 10, 24))["points"] == 0                   # refused by our own cap
    assert asked == ['"stock market"']                                                  # one request reached GDELT
    pulls = [(p["status"]) for p in db.fetch_all("SELECT status FROM data_pulls WHERE source = 'gdelt' ORDER BY id")]
    assert pulls == ["error", "skipped"]
    db.close()


def test_the_macro_pull_no_longer_calls_gdelt(fresh_db, keys):
    db = Database(settings())
    http, asked = _gdelt([])
    jobs = Jobs(db, http)
    jobs.pull_macro()
    assert asked == []
    db.close()


def test_staging_and_production_ask_gdelt_at_different_minutes(fresh_db):
    from apscheduler.schedulers.background import BackgroundScheduler

    from ghostline.worker import schedule
    db = Database(settings())
    minutes = {}
    for env in ("staging", "prod"):
        sch = BackgroundScheduler(timezone="America/Los_Angeles")
        schedule.register(sch, db, settings=settings(GHOSTLINE_ENV=env))
        minutes[env] = str(sch.get_job("pull_tone").trigger)
    assert "minute='15,45'" in minutes["staging"] and "minute='0,30'" in minutes["prod"]
    assert "hour='0-4'" in minutes["prod"]                         # every run lands before the 05:30 brief
    db.close()


def test_each_past_report_shows_how_the_stock_moved_around_it(client):
    from ghostline.market import Bar
    h = {"Authorization": f"Bearer {sign_in(client)}"}
    db: Database = client.app.state.db
    now = dt.datetime(2026, 10, 30, 9, 0, tzinfo=dt.UTC)
    closes = {dt.date(2026, 10, 27): "100", dt.date(2026, 10, 28): "104", dt.date(2026, 10, 29): "95.5"}
    store.save_bars(db, "AAPL", "1D", [Bar(dt.datetime.combine(d, dt.time(4), tzinfo=dt.UTC), Decimal(c), Decimal(c),
                                           Decimal(c), Decimal(c), Decimal(1)) for d, c in closes.items()], "alpaca", now)
    for day in ("2026-10-28", "2026-10-29", "2027-01-28"):
        db.execute("INSERT INTO earnings (ticker, at, estimate, source, as_of) VALUES ('AAPL', %s, 1, 'fmp', now())", (day,))
    got = {e["at"]: e["move_pct"] for e in client.get("/news-earnings?ticker=AAPL", headers=h).json()["earnings"]}
    assert got == {"2026-10-28": "-4.50",          # 100 (the 27th) to 95.50 (the 29th)
                   "2026-10-29": None,             # no close after it yet
                   "2027-01-28": None}             # not reported yet


SECRET_KEYS = {"ALPACA": "alpaca-id-0123456789", "ALPACA_SECRET": "alpaca-secret-0123456789",
               "MASSIVE": "massive-key-0123456789", "FMP": "fmp-key-0123456789abc", "FINNHUB": "finnhub-key-0123456789",
               "FRED": "fred-key-0123456789abcd", "TRADIER": "tradier-key-0123456789"}


def test_a_failed_call_never_stores_or_logs_a_key(fresh_db, monkeypatch, caplog):
    """FMP's key reached staging's data_pulls on 2026-10-30: httpx's error text names the whole address, and FMP,
    Finnhub, Massive and FRED took their keys in the query. Here every source fails, and its answer even echoes the keys."""
    for k, v in SECRET_KEYS.items():
        monkeypatch.setenv(f"GHOSTLINE_KEY_{k}", v)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, text="refused " + " ".join(SECRET_KEYS.values()))
    db = Database(settings())
    jobs = Jobs(db, httpx.Client(transport=httpx.MockTransport(handler)))
    src, day = jobs.sources, dt.date(2026, 10, 29)
    calls = [("massive", src["massive"].grouped_daily, day), ("fmp", src["fmp"].earnings, "AAPL"),
             ("fmp", src["fmp"].profile, "AAPL"), ("finnhub", src["finnhub"].news, "AAPL", day, day),
             ("fred", src["fred"].series, "DGS10", day), ("alpaca", src["alpaca"].bars, "AAPL", day, day),
             ("tradier", src["tradier"].quotes, ["AAPL"]), ("edgar", src["edgar"].filings, "AAPL")]
    with caplog.at_level(logging.INFO):
        for source, fn, *args in calls:
            assert jobs._call(source, "probe", fn, *args) is None
    details = [r["detail"] for r in db.fetch_all("SELECT detail FROM data_pulls WHERE status = 'error' ORDER BY id")]
    assert len(details) == len(calls) and all("402 Payment Required" in d for d in details)
    assert details[1].startswith("SourceError: fmp /stable/earnings: 402")      # what failed, where, and why
    written = " ".join(details) + caplog.text
    assert [k for k, v in SECRET_KEYS.items() if v in written] == []
    assert "?" not in " ".join(details)                                         # no query string at all
    db.close()


def test_keys_travel_in_headers_except_freds(keys):
    http, seen = fake_http()
    day = dt.date(2026, 10, 1)
    FMP(http).profile("AAPL")
    FMP(http).earnings("AAPL")
    Finnhub(http).news("AAPL", day, day)
    Massive(http).grouped_daily(day)
    assert [k for r in seen for k in ("test-fmp", "test-finnhub", "test-massive") if k in str(r.url)] == []
    assert seen[0].headers["apikey"] == "test-fmp" and seen[1].url.params["limit"] == "5"   # the free plan's most
    assert seen[2].headers["X-Finnhub-Token"] == "test-finnhub"
    assert seen[3].headers["Authorization"] == "Bearer test-massive"
    FRED(http).series("DGS10", day)
    assert seen[4].url.params["api_key"] == "test-fred"        # FRED takes no header; Source.fetch keeps it out of errors


def test_fmp_history_is_asked_only_for_the_owners_names(fresh_db, keys):
    db = Database(settings())
    http, seen = fake_http()
    db.execute("INSERT INTO watchlist (ticker) VALUES ('AAPL')")
    store.save_universe(db, dt.date.today(), [{"ticker": "MSFT", "reason": "movers"}])
    Jobs(db, http).pull_news_earnings()
    asked = {(r.url.host, r.url.path.rsplit("/", 1)[-1], r.url.params.get("symbol")) for r in seen}
    assert ("finnhub.io", "earnings", "MSFT") in asked and ("finnhub.io", "earnings", "AAPL") in asked
    assert {sym for host, _, sym in asked if host == "financialmodelingprep.com"} == {"AAPL"}
    db.close()


def test_each_environment_takes_its_share_and_the_two_stay_inside_the_limit(fresh_db):
    db = Database(settings())
    prod, staging = Limiter(db, "prod"), Limiter(db, "staging")
    assert prod.limits("fmp") == (None, 200) and staging.limits("fmp") == (None, 50)
    assert prod.limits("finnhub") == (48, None) and staging.limits("finnhub") == (12, None)
    for source, pair in LIMITS.items():
        for i, limit in enumerate(pair):
            if limit and limit > 1:         # a limit of 1 cannot be split (GDELT: different minutes instead)
                assert prod.limits(source)[i] + staging.limits(source)[i] <= limit, source
    assert Limiter(db, "test").limits("fmp") == (None, 250)
    db.close()


def test_a_job_waits_out_a_full_minute_but_never_a_full_day(fresh_db):
    db = Database(settings())
    slept = []

    def sleep(seconds: float) -> None:      # time passes: every counted call moves a minute into the past
        slept.append(seconds)
        db.execute("UPDATE data_pulls SET at = at - interval '61 seconds'")
    lim = Limiter(db, "staging", sleep=sleep)
    for _ in range(12):
        lim.record("finnhub", "calendar/earnings", "ok", rows=1)
    with pytest.raises(RateLimited):
        lim.check("finnhub")                # a request from the app never waits
    lim.check("finnhub", wait=True)         # a job waits until the oldest call leaves the minute
    assert len(slept) == 1 and 0 < slept[0] <= 61
    for _ in range(50):
        lim.record("fmp", "profile", "ok", rows=1)
    with pytest.raises(RateLimited, match="a day"):
        lim.check("fmp", wait=True)
    assert len(slept) == 1                  # a day's limit is never waited for
    db.close()
