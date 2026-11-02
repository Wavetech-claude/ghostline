"""Search any stock (the owner, 2026-11-01: "make it similar to Robinhood, any stock search").

The SEC's company list (free, no key) fills a ticker directory every day in production; the search route finds a
stock by ticker or name, exact ticker first; and the chart of a stock nobody watches is fetched on demand, in
production only (staging pulls nothing, the owner's rule of 2026-10-30)."""
import datetime as dt
from decimal import Decimal

import httpx

from ghostline.main import create_app
from ghostline.market import Bar, store
from ghostline.market.jobs import Jobs
from ghostline.market.sources import Alpaca, Edgar
from ghostline.worker import schedule
from tests.conftest import settings
from tests.test_invest_ai import _h

SEC = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
       "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
       "2": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
       "3": {"cik_str": 1730168, "ticker": "AVGO", "title": "Broadcom Inc."},
       "4": {"cik_str": 2488, "ticker": "AMD", "title": "ADVANCED MICRO DEVICES INC"}}


def _sec_http():
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SEC)))


def test_the_sec_list_becomes_the_ticker_directory(fresh_db):
    from ghostline.db import Database
    db = Database(settings())
    db.open()
    try:
        assert Edgar(_sec_http()).directory()[:2] == [("AAPL", "Apple Inc.", "0000320193"),
                                                       ("NVDA", "NVIDIA CORP", "0001045810")]
        assert Jobs(db, _sec_http()).pull_tickers() == {"tickers": 5}
        assert Jobs(db, _sec_http()).pull_tickers() == {"tickers": 5}          # refreshed in place, never doubled
        assert db.fetch_one("SELECT count(*) AS n FROM tickers")["n"] == 5
    finally:
        db.close()


def test_the_ticker_directory_is_a_production_pull():
    assert "pull_tickers" in schedule.PULLS


def _filled(client):
    store.save_tickers(client.app.state.db, [(t["ticker"], t["title"], f"{t['cik_str']:010d}") for t in SEC.values()],
                       "sec", dt.datetime.now(dt.UTC))


def test_search_finds_by_ticker_then_prefix_then_name(client):
    h = _h(client)
    _filled(client)
    got = client.get("/market/search", params={"q": "am"}, headers=h).json()["results"]
    assert [r["ticker"] for r in got] == ["AMD", "AMZN"]                      # ticker prefixes; no volume yet, so by ticker
    got = client.get("/market/search", params={"q": "AMD"}, headers=h).json()["results"]
    assert got[0] == {"ticker": "AMD", "name": "ADVANCED MICRO DEVICES INC", "watched": False, "held": False,
                      "kind": None}
    got = client.get("/market/search", params={"q": "nvidia"}, headers=h).json()["results"]
    assert [r["ticker"] for r in got] == ["NVDA"]                             # by name, any case
    assert client.get("/market/search", params={"q": ""}, headers=h).status_code == 422
    assert client.get("/market/search", params={"q": "x" * 41}, headers=h).status_code == 422


def test_search_marks_what_the_owner_watches(client):
    h = _h(client)
    _filled(client)
    client.app.state.db.execute("INSERT INTO watchlist (ticker) VALUES ('NVDA')")
    (r,) = client.get("/market/search", params={"q": "NVDA"}, headers=h).json()["results"]
    assert r["watched"] is True


def _bars(n=3):
    day = dt.datetime.combine(dt.date.today() - dt.timedelta(days=n), dt.time(4), tzinfo=dt.UTC)
    return [Bar(day + dt.timedelta(days=i), Decimal(100 + i), Decimal(101 + i), Decimal(99 + i), Decimal(100 + i),
                Decimal(1000)) for i in range(n)]


def test_a_chart_nobody_watches_is_fetched_on_demand_in_production(fresh_db, monkeypatch):
    calls = []
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA", "test-alpaca")
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA_SECRET", "test-alpaca-secret")
    monkeypatch.setattr(Alpaca, "bars", lambda self, t, start, end, timeframe="1Day": calls.append(t) or _bars())
    from fastapi.testclient import TestClient
    with TestClient(create_app(settings(GHOSTLINE_ENV="prod"))) as c:
        h = _h(c)
        body = c.get("/charts", params={"ticker": "zzzz", "timeframe": "5Y"}, headers=h).json()
        assert calls == ["ZZZZ"] and len(body["bars"]) == 3 and body["source"] == "alpaca"
        c.get("/charts", params={"ticker": "ZZZZ", "timeframe": "5Y"}, headers=h)
        assert calls == ["ZZZZ"]                                                # fresh bars: no second pull


def test_staging_never_fetches_a_chart(client, monkeypatch):
    calls = []
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA", "test-alpaca")
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA_SECRET", "test-alpaca-secret")
    monkeypatch.setattr(Alpaca, "bars", lambda self, *a, **k: calls.append(a) or _bars())
    body = client.get("/charts", params={"ticker": "ZZZZ"}, headers=_h(client)).json()
    assert calls == [] and body["bars"] == []


def test_an_odd_symbol_is_never_fetched(fresh_db, monkeypatch):
    calls = []
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA", "test-alpaca")
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA_SECRET", "test-alpaca-secret")
    monkeypatch.setattr(Alpaca, "bars", lambda self, *a, **k: calls.append(a) or _bars())
    from fastapi.testclient import TestClient
    with TestClient(create_app(settings(GHOSTLINE_ENV="prod"))) as c:
        c.get("/charts", params={"ticker": "../../x"}, headers=_h(c))
    assert calls == []
