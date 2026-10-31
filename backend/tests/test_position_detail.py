"""Day P&L (portfolio and one position), the position detail screen, and SPY's line beside each book's value."""
import datetime as dt
from decimal import Decimal

from ghostline import ledger, series
from ghostline.ledger import Trade
from ghostline.prices import NEW_YORK, session_day
from tests.conftest import sign_in
from tests.test_invest_ai import bar, flow, idea
from tests.test_practice import _sql

D = Decimal
NOW = dt.datetime(2026, 10, 30, 15, 0, tzinfo=dt.UTC)        # 11:00 in New York


def t(side, qty, fill, fees="0", at=NOW):
    return Trade(id=1, book_id=1, ticker="AAPL", asset="stock", side=side, quantity=D(qty), fill=D(fill), fees=D(fees), at=at)


def test_day_pnl_counts_held_shares_from_the_close_and_new_ones_from_their_fill():
    assert ledger.day_pnl(D(10), D(103), D(100), []) == D("30.00")                        # held through the session
    assert ledger.day_pnl(D(15), D(103), D(100), [t("buy", 5, 102, "1")]) == D("34.00")   # 30 + 5 × 1 − 1 fee
    assert ledger.day_pnl(D(8), D(103), D(100), [t("sell", 2, 104)]) == D("32.00")        # 8 × 3 kept, 2 × 4 sold
    assert ledger.day_pnl(D(0), None, D(100), [t("sell", 10, 99)]) == D("-10.00")         # sold out in the session
    assert ledger.day_pnl(D(5), D(103), None, [t("buy", 5, 101)]) == D("10.00")           # bought today: no close needed
    assert ledger.day_pnl(D(10), D(103), None, []) is None                                 # held, no previous close
    assert ledger.day_pnl(D(10), None, D(100), []) is None                                 # no price


def test_a_quote_belongs_to_its_new_york_session():
    assert session_day(dt.datetime(2026, 10, 31, 3, 30, tzinfo=dt.UTC)) == dt.date(2026, 10, 30)   # 23:30 New York
    assert session_day(NOW) == dt.date(2026, 10, 30)


def _ready(client):
    """AAPL: closes of 90 and 100 on the two sessions before today, a quote of 103 now; 10 shares held since before
    yesterday, 5 more bought this session."""
    h = {"Authorization": f"Bearer {sign_in(client)}"}
    today = dt.datetime.now(NEW_YORK).date()
    bar("AAPL", today - dt.timedelta(days=3), 90)
    bar("AAPL", today - dt.timedelta(days=1), 100)
    _sql("INSERT INTO quotes (ticker, bid, ask, last, ts, source, as_of, delayed) "
         "VALUES ('AAPL', 102.9, 103.1, 103, now(), 'alpaca', now(), true)")
    flow(client, h, "practice", 5000, today - dt.timedelta(days=10))
    long_ago = (dt.datetime.now(dt.UTC) - dt.timedelta(days=4)).isoformat()
    # "this session": a minute ago, but never before New York's midnight. Run in the first minute of a New York day,
    # a minute ago was yesterday there, and the five shares stopped counting as bought this session (2026-10-30)
    just_now = max(dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
                   dt.datetime.combine(today, dt.time(0, 0, 1), tzinfo=NEW_YORK)).isoformat()
    iid = idea("AAPL", lane="swing", stop=95, target=120)
    for body in ({"quantity": "10", "fill": "80", "at": long_ago, "idea_id": iid},
                 {"quantity": "5", "fill": "102", "at": just_now, "fees": "1"}):
        r = client.post("/trades", json={"book": "practice", "ticker": "AAPL", "side": "buy", **body}, headers=h)
        assert r.status_code == 201, r.text
    return h, iid


def test_the_portfolio_shows_day_pnl_per_position_and_per_book(client):
    h, _ = _ready(client)
    book = next(b for b in client.get("/portfolio", headers=h).json()["books"] if b["book"] == "practice")
    (p,) = book["positions"]
    assert p["day_pnl"] == "34.00" and book["day_pnl"] == "34.00" and book["day_pnl_partial"] is False
    real = next(b for b in client.get("/portfolio", headers=h).json()["books"] if b["book"] == "real")
    assert real["day_pnl"] is None and real["day_pnl_partial"] is False                  # nothing held


def test_a_position_without_a_previous_close_says_its_day_is_unknown(client):
    h, _ = _ready(client)
    _sql("DELETE FROM market_bars WHERE ticker = 'AAPL'")
    book = next(b for b in client.get("/portfolio", headers=h).json()["books"] if b["book"] == "practice")
    assert book["positions"][0]["day_pnl"] is None and book["day_pnl"] is None and book["day_pnl_partial"] is True


def test_the_position_detail_carries_thesis_call_news_and_the_next_report(client):
    h, iid = _ready(client)
    _sql("INSERT INTO memos (idea_id, ticker, call, bull, base, bear, invalidation, grounded) "
         "VALUES (%s, 'AAPL', 'wait', 'up', 'flat', 'down', 'a close under 95', true)", (iid,))
    _sql("INSERT INTO news (ticker, headline, url, published_at, source, as_of) "
         "VALUES ('AAPL', 'Apple does a thing', 'https://n/1', now(), 'finnhub', now())")
    ny_today = dt.datetime.now(NEW_YORK).date()
    _sql("INSERT INTO earnings (ticker, at, estimate, source, as_of) VALUES "
         "('AAPL', %s, 1, 'fmp', now()), ('AAPL', %s, 2.1, 'finnhub', now())",
         (ny_today - dt.timedelta(days=60), ny_today + dt.timedelta(days=30)))
    r = client.get("/positions/practice/aapl", headers=h)
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["quantity"], p["price"], p["price_source"], p["value"]) == ("15", "103.0000", "alpaca", "1545.00")
    assert (p["day_pnl"], p["unrealized_pnl"], p["realized_pnl"], p["total_pnl"]) == ("34.00", "234.00", "0.00", "234.00")
    assert p["thesis"]["idea_id"] == iid and p["thesis"]["stop"] == "95.0000" and p["thesis"]["target"] == "120.0000"
    assert p["thesis"]["reason"] == "AAPL: a test idea"
    assert p["memo"]["call"] == "wait" and p["memo"]["invalidation"] == "a close under 95"
    assert [n["headline"] for n in p["news"]] == ["Apple does a thing"]
    assert p["next_earnings"]["at"] == (ny_today + dt.timedelta(days=30)).isoformat()
    assert client.get("/positions/real/AAPL", headers=h).status_code == 404               # never traded there
    assert client.get("/positions/nope/AAPL", headers=h).status_code == 404
    assert client.get("/positions/practice/AAPL").status_code == 401


def test_a_closed_position_keeps_its_realized_pnl(client):
    h, _ = _ready(client)
    r = client.post("/trades", json={"book": "practice", "ticker": "AAPL", "side": "sell", "quantity": "15", "fill": "110",
                                     "at": dt.datetime.now(dt.UTC).isoformat()}, headers=h)
    assert r.status_code == 201
    p = client.get("/positions/practice/AAPL", headers=h).json()
    assert (p["quantity"], p["value"], p["unrealized_pnl"], p["realized_pnl"]) == ("0", "0.00", "0.00", "339.00")
    assert p["total_pnl"] == "339.00" and p["day_pnl"] == "139.00"      # 10 × 10 from the close, 5 × 8 − 1 from the fill


def test_each_book_series_carries_spys_return_over_the_same_days(client):
    h = {"Authorization": f"Bearer {sign_in(client)}"}
    for d, c in ((dt.date(2026, 9, 1), 400), (dt.date(2026, 9, 2), 410), (dt.date(2026, 9, 3), 420)):
        bar("SPY", d, c)
    flow(client, h, "practice", 1000, dt.date(2026, 9, 1))
    pts = series.equity_series(client.app.state.db, "practice", end=dt.date(2026, 9, 4))["points"]
    assert [(p["date"], p["spy_pct"]) for p in pts] == [
        ("2026-09-01", "0.00"), ("2026-09-02", "2.50"), ("2026-09-03", "5.00"), ("2026-09-04", "5.00")]


def test_a_trade_in_the_history_opens_its_memo_by_id(client):
    h = {"Authorization": f"Bearer {sign_in(client)}"}
    _sql("INSERT INTO memos (ticker, call, bull, grounded, struck) VALUES ('AAPL', 'buy', 'up', false, %s::jsonb)",
         ('["12.5"]',))
    mid = client.get("/deep-dive/AAPL", headers=h).json()["memos"][0]["id"]
    m = client.get(f"/memos/{mid}", headers=h).json()
    assert (m["id"], m["ticker"], m["call"], m["struck"]) == (mid, "AAPL", "buy", ["12.5"])
    assert client.get(f"/memos/{mid + 1}", headers=h).status_code == 404
    assert client.get(f"/memos/{mid}").status_code == 401


def test_a_sale_by_hand_keeps_the_idea_the_shares_were_bought_on(client):
    h, iid = _ready(client)
    r = client.post("/trades", json={"book": "practice", "ticker": "AAPL", "side": "sell", "quantity": "4", "fill": "110",
                                     "at": dt.datetime.now(dt.UTC).isoformat()}, headers=h)
    assert r.status_code == 201 and r.json()["idea_id"] is None          # the sale itself names no idea
    (c,) = client.get("/history?book=practice", headers=h).json()["closed"]
    assert c["idea_id"] == iid and c["memo_id"] is None
