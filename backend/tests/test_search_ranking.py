"""Search as you type, Robinhood style (the owner, 2026-11-01: typing "n" must show Netflix, Nvidia, Nike...).

A stock matches by a ticker prefix or a prefix of any word in its name; what the owner holds or watches comes first,
then the exact ticker, then the most traded (the 20-session average dollar volume from Massive's whole-market daily
file), then the ticker. Warrants, rights and units are hidden unless asked for; ETFs are in."""
import datetime as dt
from decimal import Decimal

import httpx
import psycopg
import pytest

from ghostline.db import Database
from ghostline.market import Bar, store
from ghostline.market.jobs import Jobs
from ghostline.market.sources import Massive
from tests.conftest import settings
from tests.test_invest_ai import _h
from tests.test_market import keys  # noqa: F401 — the fixture

NOW = dt.datetime.now(dt.UTC)
# ticker, name, kind, 20-day dollar volume
DIRECTORY = [
    ("NVDA", "NVIDIA Corp", "CS", "30000000000"), ("NFLX", "Netflix Inc.", "CS", "5000000000"),
    ("NKE", "Nike Inc.", "CS", "1000000000"), ("BK", "Bank of New York Mellon Corp", "CS", "800000000"),
    ("NAAA", "Nano Obscure Holdings", "CS", "100000"), ("N", "Tiny N Corp", "CS", "50000"),
    ("NVDAW", "NVIDIA Warrant Test", "WARRANT", "90000000000"), ("NXTU", "Next Acquisition Units", "UNIT", None),
    ("QQQ", "Invesco QQQ Trust", "ETF", "20000000000"), ("CABN", "Cabinetworks Group", "CS", "9000000000"),
    ("A", "Agilent Technologies Inc.", "CS", "400000000"),
]


def _fill(db):
    store.save_ticker_reference(db, [(t, n, k, None) for t, n, k, _ in DIRECTORY], "massive", NOW)
    for t, _, _, dv in DIRECTORY:
        db.execute("UPDATE tickers SET dollar_volume_20d = %s WHERE ticker = %s", (dv, t))


def _search(client, q, **params):
    return [r["ticker"] for r in client.get("/market/search", params={"q": q, **params}, headers=_h(client)).json()
            ["results"]]


def test_n_puts_the_most_traded_n_names_first_and_hides_warrants(client):
    _fill(client.app.state.db)
    got = _search(client, "n")
    assert got[:2] == ["N", "NVDA"]                  # the exact ticker, then by dollar volume
    assert got[2:6] == ["NFLX", "NKE", "BK", "NAAA"]  # BK: a word of its name ("New") starts with n
    assert "NVDAW" not in got and "NXTU" not in got and "CABN" not in got
    assert "NVDAW" in _search(client, "n", all="true") and "NXTU" in _search(client, "n", all="true")


def test_a_word_of_the_name_finds_a_stock_whose_ticker_you_do_not_know(client):
    _fill(client.app.state.db)
    assert _search(client, "net") == ["NFLX"]                       # Netflix; "Cabinetworks" has "net" inside a word
    assert _search(client, "bank of") == ["BK"]
    assert _search(client, "invesco") == ["QQQ"]                    # ETFs are in
    assert _search(client, "agil") == ["A"]


def test_what_the_owner_holds_or_watches_comes_first(client):
    db = client.app.state.db
    _fill(db)
    db.execute("INSERT INTO watchlist (ticker) VALUES ('NAAA')")
    got = client.get("/market/search", params={"q": "n"}, headers=_h(client)).json()["results"]
    assert got[0]["ticker"] == "NAAA" and got[0]["watched"] is True and got[1]["ticker"] == "N"
    assert got[0]["kind"] == "CS" and got[0]["held"] is False


def test_a_brand_name_finds_the_company_that_owns_it(client):
    """The owner typed "google" and got nothing: the company is Alphabet Inc. (2026-11-02)."""
    db = client.app.state.db
    store.save_ticker_reference(db, [("GOOGL", "Alphabet Inc.", "CS", None), ("GOOG", "Alphabet Inc.", "CS", None),
                                     ("META", "Meta Platforms, Inc.", "CS", None)], "massive", NOW)
    db.execute("UPDATE tickers SET dollar_volume_20d = CASE ticker WHEN 'GOOGL' THEN 9 WHEN 'GOOG' THEN 8 ELSE 1 END")
    assert _search(client, "google") == ["GOOGL", "GOOG"]
    assert _search(client, "Google") == ["GOOGL", "GOOG"]          # any case
    assert _search(client, "goog") == ["GOOG", "GOOGL"]             # a ticker prefix still ranks the exact ticker first
    assert _search(client, "face") == ["META"] and _search(client, "whatsapp") == ["META"]
    assert _search(client, "coke") == []                            # an alias of a ticker the directory lacks
    assert _search(client, "go%") == [] and _search(client, "g_ogle") == []   # wildcards stay literal


def test_every_alias_is_lower_case_and_names_an_upper_case_ticker(client):
    db = client.app.state.db
    rows = db.fetch_all("SELECT alias, ticker FROM ticker_aliases")
    assert {("google", "GOOGL"), ("facebook", "META")} <= {(r["alias"], r["ticker"]) for r in rows}
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO ticker_aliases (alias, ticker) VALUES ('Google', 'GOOGL')")


def test_odd_characters_in_the_text_are_literal(client):
    _fill(client.app.state.db)
    for q in ("n%", "n_", "(", "[a", "\\", ".*"):
        assert client.get("/market/search", params={"q": q}, headers=_h(client)).status_code == 200


def _grouped(n_tickers: dict[str, tuple[str, str]]):
    """Massive's grouped daily answer for every day: {ticker: (close, volume)}."""
    def handler(request: httpx.Request) -> httpx.Response:
        day = request.url.path.rsplit("/", 1)[-1]
        ts = int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.UTC).timestamp() * 1000)
        return httpx.Response(200, json={"results": [{"T": t, "o": float(c), "h": float(c), "l": float(c), "c": float(c),
                                                      "v": float(v), "t": ts} for t, (c, v) in n_tickers.items()]})
    return handler


def test_the_daily_file_fills_a_month_once_and_feeds_popularity_and_the_universe(fresh_db, keys):  # noqa: F811
    db = Database(settings())
    store.save_ticker_reference(db, [("NVDA", "NVIDIA Corp", "CS", None), ("NFLX", "Netflix Inc.", "CS", None)],
                                "massive", NOW)
    calls: list = []

    def handler(request):
        calls.append(request.url.path)
        return _grouped({"NVDA": ("100", "1000"), "NFLX": ("50", "10")})(request)
    jobs = Jobs(db, httpx.Client(transport=httpx.MockTransport(handler)))
    jobs.limiter.sleep = lambda s: None
    db.execute("DELETE FROM data_pulls")
    out = jobs.pull_market_daily(today=dt.date(2026, 10, 30), sessions=3)
    assert out["sessions"] == 3 and out["rows"] == 6 and len(calls) == 3
    assert {r["ticker"]: r["dollar_volume_20d"] for r in db.fetch_all("SELECT * FROM tickers")} == \
        {"NVDA": Decimal("100000.00"), "NFLX": Decimal("500.00")}
    again = jobs.pull_market_daily(today=dt.date(2026, 10, 30), sessions=3)
    assert again["sessions"] == 0 and len(calls) == 3                # each session fetched once
    calls.clear()
    jobs.build_universe(day=dt.date(2026, 10, 29))
    assert calls == []                                               # the universe reads the stored day
    db.close()


def test_the_daily_file_keeps_a_hundred_days(fresh_db):
    db = Database(settings())
    old = {"X": Bar(NOW, Decimal(1), Decimal(1), Decimal(1), Decimal(1), Decimal(1))}
    store.save_market_daily(db, dt.date(2026, 1, 2), old, "massive", NOW)
    store.save_market_daily(db, dt.date(2026, 10, 1), old, "massive", NOW)
    assert store.prune_market_daily(db, dt.date(2026, 10, 30) - dt.timedelta(days=100)) == 1
    assert store.market_days(db) == {dt.date(2026, 10, 1)}
    db.close()


def test_the_ticker_list_types_every_ticker_across_pages_and_keeps_the_secs_names(fresh_db, keys):  # noqa: F811
    db = Database(settings())
    store.save_tickers(db, [("NVDA", "NVIDIA CORP", "0001045810"), ("ACME", "ACME CORP", None),
                            ("ACMEW", "ACME CORP WARRANT", None), ("ACMER", "ACME RIGHTS", None),
                            ("SNOW", "Snowflake Inc.", None)], "sec", NOW)
    pages = {None: {"results": [{"ticker": "NVDA", "name": "NVIDIA Corp", "type": "CS"},
                                {"ticker": "SPY", "name": "SPDR S&P 500 ETF Trust", "type": "ETF"}],
                    "next_url": "https://api.massive.com/v3/reference/tickers?cursor=abc"},
             "abc": {"results": [{"ticker": "SNOWW", "name": "Not A Warrant", "type": None}]}}
    seen = []

    def handler(request):
        seen.append(request.url.params.get("cursor"))
        return httpx.Response(200, json=pages[request.url.params.get("cursor")])
    rows, nxt = Massive(httpx.Client(transport=httpx.MockTransport(handler))).ticker_page()
    assert rows[1] == ("SPY", "SPDR S&P 500 ETF Trust", "ETF", None) and nxt == "abc"
    jobs = Jobs(db, httpx.Client(transport=httpx.MockTransport(handler)))
    jobs.limiter.sleep = lambda s: None
    out = jobs.pull_ticker_reference()
    assert out["pages"] == 2 and out["complete"] is True
    got = {r["ticker"]: (r["name"], r["kind"]) for r in db.fetch_all("SELECT * FROM tickers")}
    assert got["NVDA"] == ("NVIDIA CORP", "CS")                   # the SEC's name kept, Massive's type added
    assert got["SPY"] == ("SPDR S&P 500 ETF Trust", "ETF")        # an ETF the SEC list lacks
    assert got["ACMEW"][1] == "WARRANT" and got["ACMER"][1] == "RIGHT"   # typed by symbol: ACME is listed
    assert got["SNOWW"][1] == "WARRANT"                            # untyped, and SNOW is listed: read as its warrant
    assert got["SNOW"][1] is None                                  # a real ticker is never typed by its last letter
    db.close()
