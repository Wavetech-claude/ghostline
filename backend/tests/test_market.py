"""Market data: each source parsed from a recorded answer, every stored row with source and as_of, the free-tier
counters, jobs that skip without a key, the chart payload, and the routes."""
import datetime as dt
import json
from decimal import Decimal

import httpx
import pytest

from ghostline import indicators
from ghostline.config import Settings
from ghostline.db import Database
from ghostline.market import NotConfigured, RateLimited, store
from ghostline.market.jobs import Jobs
from ghostline.market.limits import Limiter
from ghostline.market.sources import FMP, FRED, Alpaca, Edgar, Finnhub, Tradier, configured
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
    assert ids == ["pull_macro", "pull_bars", "pull_news_earnings", "pull_fundamentals", "pull_quotes"]
    quotes = sch.get_job("pull_quotes")
    assert "day_of_week='mon-fri'" in str(quotes.trigger) and "hour='6-13'" in str(quotes.trigger)
    assert str(schedule.PT) == "America/Los_Angeles"
    db.close()


def test_settings_are_the_environment():
    assert Settings(DATABASE_URL="x").database_url == "x"
    assert json.loads('{"a": 1}')["a"] == 1
