"""Intraday charts (the owner's go, 2026-11-01): 1D shows the latest session in 5-minute bars, 1W the last five in
30-minute bars, from Alpaca's IEX feed (live; the free plan refuses SIP for the latest 15 minutes). Fetched on demand
in production only, kept fresh while the market is open, regular hours only, and pruned after ten days."""
import datetime as dt
from decimal import Decimal

import httpx
import pytest

from ghostline.main import create_app
from ghostline.market import Bar, store
from ghostline.market.calendar import NEW_YORK, WEEKDAYS
from ghostline.market.jobs import Jobs
from ghostline.market.sources import Alpaca
from ghostline.routes import market as market_routes
from tests.conftest import settings
from tests.test_invest_ai import _h


def _last_weekday() -> dt.date:
    """A recent past weekday, so the test's bars stay inside the ten days intraday bars are kept."""
    d = dt.date.today() - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _at(day: dt.date, h: int, m: int = 0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(h, m), tzinfo=NEW_YORK)


def _five_min(day: dt.date) -> list[Bar]:
    """A session's 5-minute bars, plus one pre-market and one after-hours bar that the chart must leave out."""
    t0 = _at(day, 9, 30)
    bars = [Bar(t0 + dt.timedelta(minutes=5 * i), Decimal(100 + i), Decimal(101 + i), Decimal(99 + i),
                Decimal(100 + i), Decimal(500)) for i in range(78)]
    return [Bar(_at(day, 8), *([Decimal(90)] * 4), Decimal(5)), *bars, Bar(_at(day, 17), *([Decimal(95)] * 4), Decimal(5))]


@pytest.fixture(autouse=True)
def _forget_fetches():
    market_routes._fetched.clear()
    yield
    market_routes._fetched.clear()


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA", "test-alpaca")
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA_SECRET", "test-alpaca-secret")


def test_the_window_is_the_latest_session_that_has_opened():
    wed = dt.date(2026, 10, 28)
    assert market_routes.intraday_window(WEEKDAYS, _at(wed, 10), 1) == [wed]
    assert market_routes.intraday_window(WEEKDAYS, _at(wed, 9), 1) == [dt.date(2026, 10, 27)]      # before the bell
    sat = dt.date(2026, 10, 31)
    assert market_routes.intraday_window(WEEKDAYS, _at(sat, 12), 1) == [dt.date(2026, 10, 30)]
    week = market_routes.intraday_window(WEEKDAYS, _at(dt.date(2026, 11, 2), 8), 5)              # Monday, pre-market
    assert week == [dt.date(2026, 10, 26) + dt.timedelta(days=i) for i in range(5)]


def test_a_1d_chart_is_fetched_in_production_kept_fresh_and_regular_hours_only(fresh_db, monkeypatch, keys):
    day = _last_weekday()
    now = _at(day, 15)
    calls = []
    monkeypatch.setattr(market_routes, "_now", lambda: now)
    monkeypatch.setattr(Alpaca, "bars", lambda self, t, start, end, timeframe="1Day":
                        calls.append((t, start, end, timeframe)) or _five_min(day))
    from fastapi.testclient import TestClient
    with TestClient(create_app(settings(GHOSTLINE_ENV="prod"))) as c:
        h, db = _h(c), c.app.state.db
        store.save_bars(db, "ZZZZ", "1D", [Bar(dt.datetime.combine(day - dt.timedelta(days=1), dt.time(4), tzinfo=dt.UTC),
                                               *([Decimal("98.50")] * 4), Decimal(1))], "alpaca", now)
        body = c.get("/charts", params={"ticker": "zzzz", "timeframe": "1D"}, headers=h).json()
        assert [(t, tf) for t, _, _, tf in calls] == [("ZZZZ", "5Min")]
        assert calls[0][1] == _at(day, 9, 30) and calls[0][2] == now                    # never past now
        assert len(body["bars"]) == 78 and body["interval"] == "5Min" and body["feed"] == "iex"
        assert body["delayed"] is False and body["market_open"] is True and body["sessions"] == [day.isoformat()]
        assert body["previous_close"] == "98.5000" and body["bars"][0]["timeframe"] == "5Min"
        assert len(body["sma20"]) == 78 and body["sma20"][-1] is not None
        c.get("/charts", params={"ticker": "ZZZZ", "timeframe": "1D"}, headers=h)
        assert len(calls) == 1                                                            # fresh: no second call
        db.execute("UPDATE market_bars SET as_of = %s WHERE timeframe = '5Min'", (now - dt.timedelta(minutes=2),))
        market_routes._fetched.clear()
        c.get("/charts", params={"ticker": "ZZZZ", "timeframe": "1D"}, headers=h)
        assert len(calls) == 2                                                            # two minutes old, market open


def test_a_closed_session_fetched_after_its_close_is_not_fetched_again(fresh_db, monkeypatch, keys):
    day = _last_weekday()
    calls = []
    monkeypatch.setattr(market_routes, "_now", lambda: _at(day, 20))
    monkeypatch.setattr(Alpaca, "bars", lambda self, *a, **k: calls.append(a) or _five_min(day))
    from fastapi.testclient import TestClient
    with TestClient(create_app(settings(GHOSTLINE_ENV="prod"))) as c:
        h, db = _h(c), c.app.state.db
        c.get("/charts", params={"ticker": "ZZZZ", "timeframe": "1D"}, headers=h)
        db.execute("UPDATE market_bars SET as_of = %s WHERE timeframe = '5Min'", (_at(day, 16, 30),))
        market_routes._fetched.clear()
        body = c.get("/charts", params={"ticker": "ZZZZ", "timeframe": "1D"}, headers=h).json()
    assert len(calls) == 1 and body["market_open"] is False and len(body["bars"]) == 78


def test_a_1w_chart_asks_for_30_minute_bars_of_five_sessions(fresh_db, monkeypatch, keys):
    day = _last_weekday()
    calls = []
    monkeypatch.setattr(market_routes, "_now", lambda: _at(day, 12))
    monkeypatch.setattr(Alpaca, "bars", lambda self, t, start, end, timeframe="1Day": calls.append((start, timeframe)) or [])
    from fastapi.testclient import TestClient
    with TestClient(create_app(settings(GHOSTLINE_ENV="prod"))) as c:
        body = c.get("/charts", params={"ticker": "ZZZZ", "timeframe": "1W"}, headers=_h(c)).json()
    first = market_routes.intraday_window(WEEKDAYS, _at(day, 12), 5)[0]
    assert calls == [(_at(first, 9, 30), "30Min")] and body["bars"] == [] and len(body["sessions"]) == 5


def test_staging_never_fetches_an_intraday_chart(client, monkeypatch, keys):
    day = _last_weekday()
    calls = []
    monkeypatch.setattr(market_routes, "_now", lambda: _at(day, 15))
    monkeypatch.setattr(Alpaca, "bars", lambda self, *a, **k: calls.append(a) or _five_min(day))
    store.save_bars(client.app.state.db, "ZZZZ", "5Min", _five_min(day), "alpaca", _at(day, 14))
    body = client.get("/charts", params={"ticker": "ZZZZ", "timeframe": "1D"}, headers=_h(client)).json()
    assert calls == [] and len(body["bars"]) == 78                                       # production's copy, as stored


def test_old_intraday_bars_are_pruned_and_daily_bars_kept(fresh_db, monkeypatch, keys):
    from ghostline.db import Database
    db = Database(settings())
    db.open()
    try:
        old = dt.datetime.now(dt.UTC) - dt.timedelta(days=20)
        bar = Bar(old, *([Decimal(1)] * 4), Decimal(1))
        store.save_bars(db, "ZZZZ", "5Min", [bar], "alpaca", old)
        store.save_bars(db, "ZZZZ", "1D", [bar], "alpaca", old)
        monkeypatch.setattr(Alpaca, "bars", lambda self, *a, **k: [])
        Jobs(db).intraday_for("ZZZZ", "5Min", old, old)
        assert store.bars(db, "ZZZZ", "5Min") == [] and len(store.bars(db, "ZZZZ", "1D")) == 1
    finally:
        db.close()


def test_alpaca_asks_for_an_exact_moment_on_the_iex_feed(keys):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"bars": [{"t": "2026-10-30T13:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5,
                                                   "v": 10}]})
    a = Alpaca(httpx.Client(transport=httpx.MockTransport(handler)))
    got = a.bars("AAPL", _at(dt.date(2026, 10, 30), 9, 30), _at(dt.date(2026, 10, 30), 16), "5Min")
    assert seen[0]["start"] == "2026-10-30T13:30:00Z" and seen[0]["end"] == "2026-10-30T20:00:00Z"
    assert seen[0]["feed"] == "iex" and seen[0]["timeframe"] == "5Min" and got[0].close == Decimal("1.5")
    a.bars("AAPL", dt.date(2026, 10, 1), dt.date(2026, 10, 30))
    assert seen[1]["start"] == "2026-10-01" and seen[1]["timeframe"] == "1Day"


def test_an_unknown_timeframe_names_the_intraday_ones(client):
    r = client.get("/charts", params={"ticker": "AAPL", "timeframe": "2Y"}, headers=_h(client))
    assert r.status_code == 422 and "1D" in r.json()["detail"] and "5Y" in r.json()["detail"]
