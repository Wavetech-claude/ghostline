"""Discover (the owner, 2026-11-01): top gainers, losers and most active by day, week and month, sliced by stocks, ETFs
and industry; sectors through their Select Sector funds; industries by the median move of their stocks; curated themes.
Today's unfiltered lists are Alpaca's live ones; every other slice comes from the whole market's daily file."""
import datetime as dt
from decimal import Decimal

import httpx

from ghostline import discover
from ghostline.db import Database
from ghostline.market import Bar, store
from ghostline.market.jobs import Jobs
from ghostline.market.sources import Alpaca, Finnhub
from ghostline.worker import schedule
from tests.conftest import settings
from tests.test_invest_ai import _h
from tests.test_market import keys  # noqa: F401 — the fixture

NOW = dt.datetime.now(dt.UTC)
# 22 sessions, oldest first
DAYS = [dt.date(2026, 10, 1) + dt.timedelta(days=i) for i in range(40) if
        (dt.date(2026, 10, 1) + dt.timedelta(days=i)).weekday() < 5][:22]
# ticker: (name, kind, industry, closes oldest -> newest as (start, week_ago, yesterday, today))
UNIVERSE = {
    "UPUP": ("Up Up Corp", "CS", "Technology", (50, 60, 90, 100)),        # month +100%, week +66.7%, day +11.1%
    "DOWN": ("Down Corp", "CS", "Technology", (100, 90, 80, 60)),         # month -40%, day -25%
    "FLAT": ("Flat Corp", "CS", "Banking", (20, 20, 20, 20)),
    "BANK": ("Some Bank", "CS", "Banking", (20, 21, 22, 24)),
    "BNK2": ("Other Bank", "CS", "Banking", (30, 30, 30, 33)),
    "PENY": ("Penny Corp", "CS", "Technology", (1, 1, 2, 4)),             # +300% but under $5
    "WARRW": ("Warrant Thing", "WARRANT", None, (5, 6, 8, 20)),
    "XLK": ("Technology Select Sector SPDR", "ETF", None, (200, 205, 210, 220)),
    "XLF": ("Financial Select Sector SPDR", "ETF", None, (40, 40, 39, 38)),
    "SMH": ("VanEck Semiconductor ETF", "ETF", None, (100, 110, 115, 120)),
    "THIN": ("Thinly Traded", "CS", "Technology", (10, 10, 10, 30)),     # under the $1M a day floor
}


def _closes(points):
    """22 closes: the start, then flat to a week ago, then to yesterday, then today."""
    start, week, yday, today = points
    return [start] + [week] * 16 + [yday] * 4 + [today]


def _fill(db):
    store.save_ticker_reference(db, [(t, n, k, None) for t, (n, k, _, _) in UNIVERSE.items()], "massive", NOW)
    for t, (_, _, industry, _) in UNIVERSE.items():
        db.execute("UPDATE tickers SET industry = %s, industry_at = now() WHERE ticker = %s", (industry, t))
    for i, day in enumerate(DAYS):
        grouped = {}
        for t, (_, _, _, points) in UNIVERSE.items():
            c = Decimal(_closes(points)[i])
            volume = Decimal(10) if t == "THIN" else Decimal(1_000_000)
            grouped[t] = Bar(NOW, c, c, c, c, volume)
        store.save_market_daily(db, day, grouped, "massive", NOW)
    store.refresh_popularity(db)


def test_week_and_month_movers_come_from_the_daily_file_with_every_filter(fresh_db):
    db = Database(settings())
    _fill(db)
    week = discover.movers(db, "week", "gainers")
    assert [r["ticker"] for r in week["rows"]][:4] == ["UPUP", "BANK", "BNK2", "SMH"]
    assert week["source"] == "massive" and week["through"] == DAYS[-1].isoformat() and week["since"] == DAYS[-6].isoformat()
    tickers = {r["ticker"] for r in week["rows"]}
    assert "PENY" not in tickers and "WARRW" not in tickers and "THIN" not in tickers   # price, kind, liquidity
    assert "PENY" in {r["ticker"] for r in discover.movers(db, "week", "gainers", min_price=Decimal(0))["rows"]}
    month = discover.movers(db, "month", "losers")
    assert month["rows"][0]["ticker"] == "DOWN" and month["rows"][0]["change_pct"] == "-40.00"
    assert [r["ticker"] for r in discover.movers(db, "week", "gainers", "etfs")["rows"]] == ["SMH", "XLK", "XLF"]
    assert [r["ticker"] for r in discover.movers(db, "week", "gainers", industry="Banking")["rows"]] == \
        ["BANK", "BNK2", "FLAT"]
    active = discover.movers(db, "day", "active", "stocks")
    assert active["rows"][0]["ticker"] == "UPUP"                       # the most dollars traded that day
    db.close()


def test_todays_unfiltered_lists_are_alpacas_live_ones_without_warrants_or_pennies(fresh_db):
    db = Database(settings())
    _fill(db)
    store.save_movers(db, "gainers", [{"symbol": "ONEGW", "price": 9, "change": 1, "percent_change": 120},
                                      {"symbol": "TINY", "price": 1.6, "change": 0.8, "percent_change": 108},
                                      {"symbol": "UPUP", "price": 101, "change": 10, "percent_change": 11.1}],
                      "alpaca", NOW)
    live = discover.movers(db, "day", "gainers")
    assert live["source"] == "alpaca" and [r["ticker"] for r in live["rows"]] == ["UPUP"]
    assert live["rows"][0]["name"] == "Up Up Corp"
    assert discover.movers(db, "day", "gainers", "etfs")["source"] == "massive"     # a slice: the daily file
    db.execute("UPDATE market_movers SET as_of = now() - interval '5 days'")
    assert discover.movers(db, "day", "gainers")["source"] == "massive"               # a stale list is not today's
    db.close()


def test_sectors_industries_and_themes(fresh_db):
    db = Database(settings())
    _fill(db)
    sec = discover.sectors(db, "week")
    assert sec["rows"][0] == {"name": "Technology", "etf": "XLK", "change_pct": "7.32"}
    assert sec["rows"][-1]["change_pct"] is None                    # a sector fund the file lacks sorts last
    ind = discover.industries(db, "month")
    banking = next(r for r in ind["rows"] if r["name"] == "Banking")
    assert banking == {"name": "Banking", "stocks": 3, "change_pct": "10.00", "best": "BANK"}   # median of 0, 10, 20
    assert ind["tagged"] == 7
    th = {t["id"]: t for t in discover.themes(db, "week")["themes"]}
    assert th["semiconductors"]["etfs"][0] == {"ticker": "SMH", "name": "VanEck Semiconductor ETF", "change_pct": "9.09"}
    assert th["banks"]["leaders"][0]["ticker"] == "BANK" and th["clean-energy"]["leaders"] == []
    db.close()


def test_the_routes_answer_and_check_their_inputs(client):
    _fill(client.app.state.db)
    h = _h(client)
    assert client.get("/discover/movers", params={"period": "week", "kind": "stocks"}, headers=h).json()["rows"]
    assert client.get("/discover/movers", params={"period": "year"}, headers=h).status_code == 422
    assert client.get("/discover/sectors", headers=h).json()["rows"][0]["etf"]
    assert client.get("/discover/industries", params={"period": "week"}, headers=h).status_code == 200
    assert {"name": "Banking", "stocks": 3} in client.get("/discover/industry-names", headers=h).json()["industries"]
    assert len(client.get("/discover/themes", headers=h).json()["themes"]) == len(discover.THEMES)


MOVERS = {"gainers": [{"change": 0.85, "percent_change": 108.15, "price": 1.63, "symbol": "ONEG"}],
          "losers": [{"change": -2, "percent_change": -40, "price": 3, "symbol": "ABOS"}],
          "market_type": "stocks", "last_updated": "2026-10-30T23:59:00.0608453Z"}
ACTIVES = {"most_actives": [{"symbol": "INTC", "trade_count": 614504, "volume": 476217129}],
           "last_updated": "2026-10-30T23:59:00Z"}


def test_the_movers_pull_replaces_the_stored_lists(fresh_db, keys):  # noqa: F811
    db = Database(settings())
    answers = {"/v1beta1/screener/stocks/movers": MOVERS, "/v1beta1/screener/stocks/most-actives": ACTIVES}
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=answers[r.url.path])))
    assert Alpaca(http).movers()["gainers"][0]["symbol"] == "ONEG"
    assert Jobs(db, http).pull_movers() == {"gainers": 1, "losers": 1, "active": 1}
    assert Jobs(db, http).pull_movers() == {"gainers": 1, "losers": 1, "active": 1}
    rows = db.fetch_all("SELECT kind, ticker, change_pct, volume FROM market_movers ORDER BY kind")
    assert [(r["kind"], r["ticker"]) for r in rows] == [("active", "INTC"), ("gainers", "ONEG"), ("losers", "ABOS")]
    db.close()


def test_industries_are_asked_for_traded_stocks_only_once_a_week(fresh_db, keys):  # noqa: F811
    db = Database(settings())
    _fill(db)
    db.execute("UPDATE tickers SET industry = NULL, industry_at = NULL")
    asked = []

    def handler(r):
        asked.append(r.url.params["symbol"])
        return httpx.Response(200, json={"finnhubIndustry": "N/A" if r.url.params["symbol"] == "FLAT" else "Tech"})
    jobs = Jobs(db, httpx.Client(transport=httpx.MockTransport(handler)))
    out = jobs.pull_industries()
    # common stocks trading $5M a day or more: not the ETFs, the warrant, the thin stock or the $1-4 one (about $1.4M)
    assert sorted(asked) == ["BANK", "BNK2", "DOWN", "FLAT", "UPUP"] and out == {"asked": 5, "tagged": 4}
    assert db.fetch_one("SELECT industry, industry_at FROM tickers WHERE ticker = 'FLAT'")["industry"] is None
    asked.clear()
    assert jobs.pull_industries() == {"asked": 0, "tagged": 0}          # all fresh for a week, FLAT included
    assert Finnhub(httpx.Client(transport=httpx.MockTransport(handler))).industry("FLAT") is None
    db.close()


def test_the_discover_pulls_are_production_pulls():
    assert {"pull_movers", "pull_industries"} <= set(schedule.PULLS)
