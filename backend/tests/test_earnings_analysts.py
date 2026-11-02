"""The earnings calendar and analysts' views (the owner, 2026-11-01: "I like that, we should be having it").

Finnhub's earnings calendar now keeps when in the day a company reports and its revenue estimate; the calendar route
lists the reports of what the owner holds, watches or has an open idea on, with the surprise and the move for past
reports and the usual move for coming ones. Analysts' views come from Finnhub's recommendation trends and FMP's grade
consensus and price targets, each stock at most once a week per source, FMP within a cap; a stock page opened for an
untracked stock asks on demand, in production only."""
import datetime as dt
from decimal import Decimal

import httpx
from fastapi.testclient import TestClient

from ghostline.db import Database
from ghostline.main import create_app
from ghostline.market import Bar, Earning, store
from ghostline.market.jobs import Jobs
from ghostline.market.sources import FMP, Finnhub
from ghostline.worker import schedule
from tests.conftest import settings
from tests.test_invest_ai import _h
from tests.test_market import keys  # noqa: F401 — the fixture

RECS = [{"symbol": "AAPL", "period": "2026-11-01", "strongBuy": 11, "buy": 19, "hold": 15, "sell": 3, "strongSell": 1},
        {"symbol": "AAPL", "period": "2026-10-01", "strongBuy": 10, "buy": 20, "hold": 14, "sell": 3, "strongSell": 1}]
GRADES = [{"symbol": "AAPL", "strongBuy": 1, "buy": 69, "hold": 33, "sell": 9, "strongSell": 0, "consensus": "Buy"}]
TARGETS = [{"symbol": "AAPL", "targetHigh": 400, "targetLow": 245, "targetConsensus": 340.88, "targetMedian": 355}]
CAL = {"earningsCalendar": [
    {"date": "2026-11-02", "hour": "amc", "epsEstimate": 2.0267, "epsActual": None, "quarter": 4, "year": 2026,
     "revenueEstimate": 115660327009, "revenueActual": None, "symbol": "AAPL"},
    {"date": "2027-01-27", "hour": "", "epsEstimate": 2.9377, "epsActual": None, "symbol": "AAPL"}]}


def _http(answers: dict, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        key = request.url.host + request.url.path
        return httpx.Response(200, json=answers[key]) if key in answers else httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(handler))


ANALYST_ANSWERS = {"finnhub.io/api/v1/stock/recommendation": RECS,
                   "financialmodelingprep.com/stable/grades-consensus": GRADES,
                   "financialmodelingprep.com/stable/price-target-consensus": TARGETS}


def test_finnhub_keeps_the_report_hour_and_revenue_and_fmp_its_consensus(keys):  # noqa: F811
    e = Finnhub(_http({"finnhub.io/api/v1/calendar/earnings": CAL})).earnings("AAPL", dt.date(2026, 7, 1),
                                                                              dt.date(2027, 2, 28))
    assert e[0] == Earning("AAPL", dt.date(2026, 11, 2), Decimal("2.0267"), None, "amc", Decimal("115660327009"), None)
    assert e[1].hour is None                                     # "" = not announced yet
    recs = Finnhub(_http(ANALYST_ANSWERS)).recommendations("AAPL")
    assert recs[0] == {"period": dt.date(2026, 11, 1), "strong_buy": 11, "buy": 19, "hold": 15, "sell": 3,
                       "strong_sell": 1}
    fmp = FMP(_http(ANALYST_ANSWERS))
    assert fmp.grades_consensus("AAPL")["consensus"] == "Buy"
    assert fmp.price_targets("AAPL")["target_consensus"] == Decimal("340.88")
    empty = FMP(_http({"financialmodelingprep.com/stable/grades-consensus": [],
                       "financialmodelingprep.com/stable/price-target-consensus": []}))
    assert empty.grades_consensus("SPY") == {} and empty.price_targets("SPY") == {}


def test_a_later_answer_without_the_hour_keeps_the_stored_one(fresh_db):
    db = Database(settings())
    now = dt.datetime.now(dt.UTC)
    store.save_earnings(db, [Earning("AAPL", dt.date(2026, 11, 2), Decimal("2"), None, "amc", Decimal("1e11"))],
                        "finnhub", now)
    store.save_earnings(db, [Earning("AAPL", dt.date(2026, 11, 2), Decimal("2"), Decimal("2.1"))], "fmp", now)
    row = db.fetch_one("SELECT * FROM earnings WHERE ticker = 'AAPL'")
    assert row["hour"] == "amc" and row["actual"] == Decimal("2.1000") and row["revenue_estimate"] == Decimal("1e11")
    db.close()


def test_analysts_are_pulled_once_a_week_and_fmp_within_its_cap(fresh_db, keys):  # noqa: F811
    db = Database(settings())
    for t in ("AAPL", "MSFT", "NVDA", "SPY"):
        db.execute("INSERT INTO watchlist (ticker) VALUES (%s)", (t,))
    seen: list = []
    out = Jobs(db, _http(ANALYST_ANSWERS, seen)).pull_analysts(fmp_cap=4)
    assert out == {"tickers": 3, "finnhub": 3, "fmp": 2, "fresh": 0}            # SPY is a market ETF: never asked
    fmp_symbols = [r.url.params["symbol"] for r in seen if r.url.host == "financialmodelingprep.com"]
    assert len(fmp_symbols) == 4 and "SPY" not in fmp_symbols                 # 2 stocks x 2 calls = the cap
    assert db.fetch_one("SELECT count(*) AS n FROM analyst_trends")["n"] == 6
    seen.clear()
    again = Jobs(db, _http(ANALYST_ANSWERS, seen)).pull_analysts(fmp_cap=4)
    assert again["finnhub"] == 0 and again["fmp"] == 1                         # only the stock the cap left out
    db.execute("UPDATE analyst_pulls SET at = now() - interval '8 days' WHERE ticker = 'AAPL'")
    seen.clear()
    third = Jobs(db, _http(ANALYST_ANSWERS, seen)).pull_analysts(fmp_cap=4)
    assert third["finnhub"] == 1 and {r.url.params["symbol"] for r in seen} == {"AAPL"}
    db.close()


def test_a_failed_answer_is_asked_again_and_an_empty_one_waits_a_week(fresh_db, keys):  # noqa: F811
    db = Database(settings())
    db.execute("INSERT INTO watchlist (ticker) VALUES ('ZZZZ')")
    Jobs(db, _http({})).pull_analysts()                                        # 404 everywhere = failed
    assert db.fetch_one("SELECT count(*) AS n FROM analyst_pulls")["n"] == 0
    Jobs(db, _http({"finnhub.io/api/v1/stock/recommendation": [],
                    "financialmodelingprep.com/stable/grades-consensus": [],
                    "financialmodelingprep.com/stable/price-target-consensus": []})).pull_analysts()
    assert {r["source"]: r["rows"] for r in db.fetch_all("SELECT * FROM analyst_pulls")} == {"finnhub": 0, "fmp": 0}
    assert Jobs(db, _http({})).pull_analysts()["fresh"] == 1
    db.close()


def test_the_analyst_pull_is_a_production_pull():
    assert "pull_analysts" in schedule.PULLS


def _earnings_and_bars(db, ticker: str, report_days: list[dt.date], closes: dict[dt.date, str]):
    now = dt.datetime.now(dt.UTC)
    store.save_earnings(db, [Earning(ticker, d, Decimal("1.00"), Decimal("1.10") if d < dt.date.today() else None,
                                     "bmo") for d in report_days], "finnhub", now)
    store.save_bars(db, ticker, "1D", [Bar(dt.datetime.combine(d, dt.time(4), tzinfo=dt.UTC), Decimal(c), Decimal(c),
                                           Decimal(c), Decimal(c), Decimal(1000)) for d, c in closes.items()],
                    "alpaca", now)


def test_the_calendar_lists_held_watched_and_idea_stocks_with_moves(client):
    db = client.app.state.db
    h = _h(client)
    today = dt.date.today()
    d = lambda n: today + dt.timedelta(days=n)  # noqa: E731
    db.execute("INSERT INTO watchlist (ticker) VALUES ('AAPL')")
    store.save_tickers(db, [("AAPL", "Apple Inc.", None)], "sec", dt.datetime.now(dt.UTC))
    # two past reports around which AAPL moved +10% and -5%, one three days ago (+4%), one coming in five days
    _earnings_and_bars(db, "AAPL", [d(-200), d(-110), d(-3), d(5)],
                       {d(-201): "100", d(-199): "110", d(-111): "100", d(-109): "95", d(-4): "100", d(-2): "104"})
    _earnings_and_bars(db, "MSFT", [d(4)], {})                                 # nobody holds or watches MSFT
    got = client.get("/earnings/calendar", headers=h).json()
    rows = got["earnings"]
    assert [(r["ticker"], r["at"]) for r in rows] == [("AAPL", d(-3).isoformat()), ("AAPL", d(5).isoformat())]
    past, coming = rows
    assert past["move_pct"] == "4.00" and past["surprise_pct"] == "10.00" and past["why"] == ["watched"]
    assert past["name"] == "Apple Inc." and past["hour"] == "bmo"
    # the usual move: the average size of the last four past reports' moves (+4, -5, +10) = 6.33
    assert coming["typical_move_pct"] == "6.33" and coming["move_pct"] is None and coming["actual"] is None
    assert client.get("/earnings/calendar", params={"days": 91}, headers=h).status_code == 422


def test_analysts_route_serves_what_is_stored_and_staging_never_asks(client, monkeypatch):
    db = client.app.state.db
    h = _h(client)
    store.save_analyst_trends(db, "AAPL", [{"period": dt.date(2026, 11, 1), "strong_buy": 11, "buy": 19, "hold": 15,
                                            "sell": 3, "strong_sell": 1}], "finnhub", dt.datetime.now(dt.UTC))
    asked = []
    monkeypatch.setattr(Jobs, "analysts_now", lambda self, t: asked.append(t))
    got = client.get("/analysts", params={"ticker": "aapl"}, headers=h).json()
    assert asked == [] and got["consensus"] is None and got["trends"][0]["strong_buy"] == 11


def test_a_stock_page_asks_for_analysts_on_demand_in_production(fresh_db, monkeypatch, keys):  # noqa: F811
    seen: list = []
    monkeypatch.setattr(Finnhub, "recommendations", lambda self, t: seen.append(("finnhub", t)) or RECS_PARSED)
    monkeypatch.setattr(FMP, "grades_consensus", lambda self, t: seen.append(("grades", t)) or {"consensus": "Buy",
                                                                                                  "buy": 69})
    monkeypatch.setattr(FMP, "price_targets", lambda self, t: seen.append(("targets", t)) or
                        {"target_consensus": Decimal("340.88")})
    with TestClient(create_app(settings(GHOSTLINE_ENV="prod"))) as c:
        h = _h(c)
        got = c.get("/analysts", params={"ticker": "AAPL"}, headers=h).json()
        assert seen == [("finnhub", "AAPL"), ("grades", "AAPL"), ("targets", "AAPL")]
        assert got["consensus"]["consensus"] == "Buy" and got["consensus"]["target_consensus"] == "340.8800"
        c.get("/analysts", params={"ticker": "AAPL"}, headers=h)
        assert len(seen) == 3                                                   # fresh for a week: asked once
        c.get("/analysts", params={"ticker": "SPY"}, headers=h)
        assert len(seen) == 3                                                   # a market ETF: never asked
        # FMP's reserve: with 200 calls spent today, a page no longer asks FMP
        db = c.app.state.db
        for _ in range(200):
            db.execute("INSERT INTO data_pulls (source, endpoint, status) VALUES ('fmp', 'x', 'ok')")
        c.get("/analysts", params={"ticker": "MSFT"}, headers=h)
        assert seen[3:] == [("finnhub", "MSFT")]


RECS_PARSED = [{"period": dt.date(2026, 11, 1), "strong_buy": 11, "buy": 19, "hold": 15, "sell": 3, "strong_sell": 1}]
