"""Option chains from Alpaca while Tradier waits (the owner's go, 2026-11-01). The contract list gives expiry, strike
and open interest; the free indicative snapshots give each contract's quote, but no IV or delta, so those are
computed with Black–Scholes. Tradier stays first when its key is set; Alpaca is asked in production only."""
import datetime as dt
from decimal import Decimal

import httpx
import pytest

from ghostline.db import Database
from ghostline.main import create_app
from ghostline.market import Bar, greeks, store
from ghostline.market.jobs import Jobs
from ghostline.market.sources import Alpaca
from ghostline.modules import options_math, sell_puts
from tests.conftest import settings
from tests.test_invest_ai import _h


def _occ(ticker: str, expiry: dt.date, kind: str, strike: int) -> str:
    return f"{ticker}{expiry:%y%m%d}{kind}{strike * 1000:08d}"


@pytest.fixture
def alpaca_only(monkeypatch):
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA", "test-alpaca")
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA_SECRET", "test-alpaca-secret")
    monkeypatch.delenv("GHOSTLINE_KEY_TRADIER", raising=False)


def test_implied_volatility_reverses_black_scholes():
    for kind, value in (("put", options_math.put_price), ("call", options_math.call_price)):
        price = value(100, 95, 40 / 365, 0.04, 0.32)
        assert abs(options_math.implied_vol(price, 100, 95, 40 / 365, 0.04, kind) - 0.32) < 1e-4
    assert options_math.implied_vol(0.01, 100, 150, 40 / 365, 0.04, "put") is None      # under the intrinsic value
    assert options_math.implied_vol(1.0, 100, 95, 0, 0.04, "put") is None                 # expired


def test_greeks_are_filled_only_where_the_source_gave_none():
    today = dt.date(2026, 11, 1)
    expiry = today + dt.timedelta(days=38)
    price = options_math.put_price(100, 90, 38 / 365, sell_puts.RATE, 0.4)
    rows = [{"symbol": _occ("X", expiry, "P", 90), "bid": round(price - 0.05, 2), "ask": round(price + 0.05, 2)},
            {"symbol": _occ("X", expiry, "P", 95), "bid": 1, "ask": 1.2, "greeks": {"delta": -0.3, "mid_iv": 0.5}},
            {"symbol": "not-an-option", "bid": 1},
            {"symbol": _occ("X", expiry, "P", 85), "bid": None, "ask": None}]
    greeks.fill(rows, 100.0, today)
    assert abs(rows[0]["greeks"]["mid_iv"] - 0.4) < 0.01 and -0.30 < rows[0]["greeks"]["delta"] < -0.15
    assert rows[0]["greeks"]["computed"] is True and rows[1]["greeks"] == {"delta": -0.3, "mid_iv": 0.5}
    assert "greeks" not in rows[2] and "greeks" not in rows[3]
    assert greeks.fill([{"symbol": rows[0]["symbol"], "bid": 1}], None, today)[0].get("greeks") is None


def _alpaca_http(expiries: list[dt.date], seen: list):
    """Contracts (two pages) and indicative snapshots, in the shapes Alpaca answered on 2026-11-01."""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        p = request.url.params
        if request.url.path.endswith("/v2/options/contracts"):
            start = dt.date.fromisoformat(p["expiration_date_gte"])
            end = dt.date.fromisoformat(p["expiration_date_lte"])
            rows = [{"symbol": _occ(p["underlying_symbols"], d, "P", k), "expiration_date": d.isoformat(),
                     "strike_price": str(k), "type": "put", "open_interest": str(oi), "open_interest_date": "2026-10-30"}
                    for d in expiries if start <= d <= end for k, oi in ((90, 900), (95, 120))]
            if "page_token" not in p:
                return httpx.Response(200, json={"option_contracts": rows[:1], "next_page_token": "t2"})
            return httpx.Response(200, json={"option_contracts": rows[1:], "next_page_token": None})
        if "/v1beta1/options/snapshots/" in request.url.path:
            d = dt.date.fromisoformat(p["expiration_date"])
            ticker = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"snapshots": {
                _occ(ticker, d, "P", 90): {"latestQuote": {"bp": 1.5, "ap": 1.6, "t": "2026-10-30T19:59:59Z"},
                                           "latestTrade": {"p": 1.55, "t": "2026-10-30T19:58:00Z"},
                                           "dailyBar": {"v": 210}},
                _occ(ticker, d, "P", 95): {"latestQuote": {"bp": 0, "ap": 3.1, "t": "2026-10-30T19:59:59Z"}}},
                "next_page_token": None})
        if request.url.path.endswith("/v1beta1/options/snapshots"):
            return httpx.Response(200, json={"snapshots": {s: {"latestQuote": {"bp": 2.0, "ap": 2.2,
                                                                                "t": "2026-10-30T19:59:59Z"}}
                                                           for s in p["symbols"].split(",")}})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_alpaca_merges_contracts_and_snapshots_into_one_chain(alpaca_only):
    expiry = dt.date(2026, 12, 4)
    seen: list = []
    rows = Alpaca(_alpaca_http([expiry], seen)).option_chain("AAPL", expiry)
    assert [(r["symbol"][-8:], r["bid"], r["ask"], r["open_interest"]) for r in rows] == [
        ("00090000", 1.5, 1.6, "900"), ("00095000", None, 3.1, "120")]                    # a bid of 0 is no bid
    assert rows[0]["last"] == 1.55 and rows[0]["volume"] == 210
    assert [r.url.params.get("page_token") for r in seen if "contracts" in r.url.path] == [None, "t2"]   # every page
    snap = [r for r in seen if "snapshots" in r.url.path][0]
    assert snap.url.params["feed"] == "indicative" and snap.headers["APCA-API-KEY-ID"] == "test-alpaca"


def test_without_tradier_the_chain_pull_uses_alpaca_with_computed_greeks(fresh_db, alpaca_only):
    today = dt.date.today()
    expiries = [today + dt.timedelta(days=d) for d in (33, 40, 60)]
    seen: list = []
    db = Database(settings())
    db.open()
    try:
        db.execute("INSERT INTO watchlist (ticker) VALUES ('AAPL')")
        store.save_bars(db, "AAPL", "1D", [Bar(dt.datetime.now(dt.UTC), *([Decimal(100)] * 4), Decimal(1))], "alpaca",
                        dt.datetime.now(dt.UTC))
        out = Jobs(db, _alpaca_http(expiries, seen)).pull_chains()
        assert out == {"tickers": 1, "options": 2, "no_expiry": [], "source": "alpaca"}
        snaps = [r for r in seen if "/snapshots/" in r.url.path]
        assert [r.url.params["expiration_date"] for r in snaps] == [expiries[1].isoformat()]   # 40 days beats 33
        put = _occ("AAPL", expiries[1], "P", 90)
        row = db.fetch_one("SELECT * FROM option_chains WHERE option_symbol = %s", (put,))
        assert row["source"] == "alpaca" and row["open_interest"] == Decimal("900") and row["bid"] == Decimal("1.5")
        assert row["iv"] is not None and -0.5 < row["delta"] < 0                      # computed from the mid price
        assert store.latest_quotes(db, [put])[put]["source"] == "alpaca"
        assert db.fetch_one("SELECT count(*) AS n FROM data_pulls WHERE source = 'tradier'")["n"] == 0
    finally:
        db.close()


def test_option_positions_are_priced_from_alpaca_without_tradier(fresh_db, alpaca_only):
    expiry = dt.date.today() + dt.timedelta(days=40)
    put = _occ("AAPL", expiry, "P", 90)
    seen: list = []
    db = Database(settings())
    db.open()
    try:
        db.execute("INSERT INTO watchlist (ticker) VALUES ('AAPL')")
        db.execute("INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at, option_symbol) "
                   "SELECT id, 'AAPL', 'option', 'sell_to_open', 1, 1.5, now(), %s FROM books WHERE kind = 'practice'",
                   (put,))
        out = Jobs(db, _alpaca_http([expiry], seen)).pull_quotes()
        assert out["source"] == "alpaca"
        assert store.latest_quotes(db, [put])[put]["bid"] == Decimal("2.0")
    finally:
        db.close()


def test_the_chain_route_asks_alpaca_in_production_and_says_the_prices_are_indicative(fresh_db, alpaca_only):
    expiry = dt.date.today() + dt.timedelta(days=40)
    seen: list = []
    with __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(
            create_app(settings(GHOSTLINE_ENV="prod"))) as c:
        c.app.state.alpaca = Alpaca(_alpaca_http([expiry], seen))
        h = _h(c)
        e = c.get("/options/expirations", params={"ticker": "aapl"}, headers=h).json()
        assert e["configured"] and e["source"] == "alpaca" and e["expirations"] == [expiry.isoformat()]
        ch = c.get("/options/chain", params={"ticker": "AAPL", "expiry": expiry.isoformat()}, headers=h).json()
        assert ch["source"] == "alpaca" and ch["indicative"] is True and "indicative" in ch["note"]
        assert [o["strike"] for o in ch["options"]] == ["90", "95"] and ch["options"][0]["open_interest"] == "900"


def test_staging_reads_the_stored_chain_and_never_asks_alpaca(client, alpaca_only):
    expiry = dt.date.today() + dt.timedelta(days=40)
    seen: list = []
    client.app.state.alpaca = Alpaca(_alpaca_http([expiry], seen))
    put = _occ("AAPL", expiry, "P", 90)
    store.save_chain(client.app.state.db, [{"symbol": put, "bid": 1.5, "ask": 1.6, "open_interest": 900,
                                            "greeks": {"delta": -0.25, "mid_iv": 0.3}}],
                     dt.datetime.now(dt.UTC), "alpaca")
    h = _h(client)
    e = client.get("/options/expirations", params={"ticker": "AAPL"}, headers=h).json()
    ch = client.get("/options/chain", params={"ticker": "AAPL", "expiry": expiry.isoformat()}, headers=h).json()
    assert seen == [] and e["expirations"] == [expiry.isoformat()] and e["source"] == "stored"
    assert ch["configured"] and ch["source"] == "alpaca" and ch["options"][0]["delta"] == "-0.250000"
