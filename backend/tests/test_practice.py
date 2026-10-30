"""The practice account: fills at the real bid and ask, next-day settlement, the risk rules on every opening order,
cash-secured puts with their collateral, assignment or expiry from the stored price, and the chain with collateral."""
import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from ghostline import occ, practice, risk
from ghostline.market.calendar import NEW_YORK, Calendar
from ghostline.market.sources import Tradier
from ghostline.practice import Practice, fill_price, next_business_day, settles_at
from tests.conftest import TEST_DB, sign_in

PT = ZoneInfo("America/Los_Angeles")
D = Decimal


# ── pure rules ─────────────────────────────────────────────────────────────────────────────────────────────
def test_market_orders_pay_the_ask_and_get_the_bid():
    assert fill_price("buy", "market", D("99.9"), D("100.1"), None, None).price == D("100.1")
    assert fill_price("sell", "market", D("99.9"), D("100.1"), None, None).price == D("99.9")
    assert fill_price("sell_to_open", "market", D("0.5"), D("0.6"), None, None).price == D("0.5")
    assert fill_price("buy_to_close", "market", D("0.5"), D("0.6"), None, None).price == D("0.6")
    assert fill_price("buy", "market", D("99.9"), None, None, None).price is None       # no ask, no fill


def test_limit_orders_fill_only_when_the_market_is_at_least_as_good():
    assert fill_price("buy", "limit", D(99), D(100), D(100), None).price == D(100)       # ask at the limit: fills
    assert fill_price("buy", "limit", D(99), D(100), D("99.5"), None).price is None      # ask above the limit
    assert fill_price("buy", "limit", D(98), D("98.5"), D(100), None).price == D("98.5")  # better than the limit
    assert fill_price("sell", "limit", D(99), D(100), D(99), None).price == D(99)
    assert fill_price("sell", "limit", D(99), D(100), D("99.5"), None).price is None


def test_stop_orders_trigger_when_crossed_and_stay_triggered():
    f = fill_price("buy", "stop", D(99), D(100), None, D(105))
    assert f.price is None and not f.triggered
    f = fill_price("buy", "stop", D(105), D("105.5"), None, D(105))
    assert f.price == D("105.5") and f.triggered
    f = fill_price("sell", "stop", D("94.9"), D(95), None, D(95))
    assert f.price == D("94.9") and f.triggered
    # stop-limit: crossed, but the limit is not met yet; the trigger is remembered
    f = fill_price("sell", "stop_limit", D(94), D("94.5"), D("94.5"), D(95))
    assert f.price is None and f.triggered
    f = fill_price("sell", "stop_limit", D("94.6"), D(95), D("94.5"), D(95), triggered=True)
    assert f.price == D("94.6")


def test_settlement_is_the_next_business_days_open_pacific():
    assert next_business_day(dt.date(2026, 10, 30)) == dt.date(2026, 11, 2)     # Friday → Monday
    assert next_business_day(dt.date(2026, 10, 28)) == dt.date(2026, 10, 29)
    friday = dt.datetime(2026, 10, 30, 17, 0, tzinfo=dt.UTC)                     # 10:00 PT Friday
    assert settles_at(friday) == dt.datetime(2026, 11, 2, 6, 30, tzinfo=PT)
    late = dt.datetime(2026, 10, 31, 2, 0, tzinfo=dt.UTC)                        # still Friday 19:00 PT
    assert settles_at(late) == dt.datetime(2026, 11, 2, 6, 30, tzinfo=PT)


def test_occ_symbols_parse_and_build():
    o = occ.parse("aapl261121P00150000")
    assert (o.underlying, o.expiry, o.kind, o.strike) == ("AAPL", dt.date(2026, 11, 21), "put", D("150"))
    assert o.collateral_per_contract == D("15000")
    assert occ.parse("X261121P00010000").strike == D("10")
    assert occ.parse("SPY261218C00450500").strike == D("450.5")
    assert occ.build("X", dt.date(2026, 11, 21), "put", D("10")) == "X261121P00010000"
    with pytest.raises(ValueError):
        occ.parse("AAPL")
    with pytest.raises(ValueError):
        occ.parse("AAPL261321P00150000")


# ── the engine through the API ─────────────────────────────────────────────────────────────────────────────
def _h(client):
    return {"Authorization": f"Bearer {sign_in(client)}"}


def _sql(sql, params=()):
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute(sql, params)


def quote(symbol, bid, ask, last=None, source="tradier"):
    _sql("INSERT INTO quotes (ticker, bid, ask, last, ts, source, as_of, delayed) VALUES (%s, %s, %s, %s, now(), %s, now(), true)",
         (symbol, bid, ask, last, source))


def bars(ticker, n=25, volume=1_000_000, close=100):
    for i in range(n):
        day = dt.datetime(2026, 9, 1, tzinfo=dt.UTC) + dt.timedelta(days=i)
        _sql("INSERT INTO market_bars (ticker, timeframe, ts, open, high, low, close, volume, source, as_of) VALUES "
             "(%s, '1D', %s, %s, %s, %s, %s, %s, 'alpaca', now()) ON CONFLICT DO NOTHING",
             (ticker, day, close, close, close, close, volume))


def earnings(ticker, days):
    _sql("INSERT INTO earnings (ticker, at, estimate, actual, source, as_of) VALUES (%s, %s, 1, NULL, 'fmp', now())",
         (ticker, dt.date.today() + dt.timedelta(days=days)))


def chain_row(symbol, open_interest, bid, ask):
    o = occ.parse(symbol)
    _sql("INSERT INTO option_chains (option_symbol, underlying, expiry, strike, kind, bid, ask, open_interest, source, as_of) "
         "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'tradier', now())",
         (o.symbol, o.underlying, o.expiry, o.strike, o.kind, bid, ask, open_interest))


def set_rules(client, h, **over):
    rules = [{"key": k, "value": str(over.get(k, v)), "enabled": True} for k, (kind, v, _) in risk.RULES.items()]
    r = client.put("/risk-rules", json={"rules": rules}, headers=h)
    assert r.status_code == 200 and r.json()["complete"], r.text


def deposit(client, h, amount, book="practice"):
    assert client.post("/cash-flows", json={"book": book, "amount": str(amount), "at": "2026-10-01T00:00:00Z"}, headers=h).status_code == 201


def ready_stock(ticker="AAPL", bid="99.90", ask="100.10"):
    """A ticker with everything the rules look at: a quote, twenty bars of volume, earnings well away."""
    quote(ticker, bid, ask, "100.00")
    bars(ticker)
    earnings(ticker, 40)


def order(client, h, **kw):
    body = {"book": "practice", "ticker": "AAPL", "side": "buy", "type": "market", "quantity": "2"}
    body.update(kw)
    r = client.post("/practice/orders", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_no_rules_no_opening_order_but_the_ticket_is_validated_first(client):
    h = _h(client)
    deposit(client, h, 1000)
    ready_stock()
    assert client.post("/practice/orders", json={"side": "buy", "quantity": "1"}, headers=h).status_code == 422            # no ticker
    assert client.post("/practice/orders", json={"ticker": "AAPL", "side": "buy", "type": "limit", "quantity": "1"}, headers=h).status_code == 422
    assert client.post("/practice/orders", json={"ticker": "AAPL", "side": "sell_to_open", "quantity": "1"}, headers=h).status_code == 422
    assert client.post("/practice/orders", json={"asset": "option", "side": "sell_to_open", "quantity": "1", "option_symbol": "nope"}, headers=h).status_code == 422
    o = order(client, h, stop_loss="95")
    assert o["status"] == "refused" and "risk rules not set" in o["reason"]
    assert client.post("/practice/orders", json={"ticker": "AAPL", "side": "buy", "quantity": "1"}).status_code == 401
    assert client.post("/practice/orders", json={"ticker": "AAPL", "side": "buy", "quantity": "1"},
                       headers={"X-Worker-Token": "worker-token-for-tests"}).status_code == 401


def test_a_market_buy_fills_at_the_ask_and_a_sale_settles_next_business_day(client):
    h = _h(client)
    deposit(client, h, 1000)
    ready_stock()
    set_rules(client, h)
    o = order(client, h, stop_loss="95")              # worst case 2 × 5.10 = 10.20 ≤ 1.5% of 1000
    assert o["status"] == "filled" and o["fill_price"] == "100.1000" and o["trade_id"]
    t = client.get(f"/trades/{o['trade_id']}", headers=h).json()
    assert t["book"] == "practice" and t["fill"] == "100.1000" and t["broker"] == "practice" and "ask" in t["note"]
    acct = client.get("/practice/account", headers=h).json()
    assert acct["cash"] == "799.80" and acct["available"] == "799.80" and acct["unsettled"] == "0.00"
    assert acct["open_positions"] == 1 and acct["fee_per_order"] == "0.00"
    # sell: proceeds at the bid, unsettled until the next business day's open
    s = order(client, h, side="sell", quantity="2")
    assert s["status"] == "filled" and s["fill_price"] == "99.9000"
    acct = client.get("/practice/account", headers=h).json()
    assert acct["cash"] == "999.60" and acct["unsettled"] == "199.80" and acct["available"] == "799.80"
    assert len(acct["pending_settlements"]) == 1
    # the unsettled cash cannot buy: 9 shares need 900.90, available is 799.80
    r = order(client, h, quantity="9", stop_loss="99")
    assert r["status"] == "refused" and "unsettled" in r["reason"] and "900.90" in r["reason"]
    # after settlement it can
    far = dt.datetime.now(dt.UTC) + dt.timedelta(days=4)
    assert Practice(client.app.state.db).settle_due(far) == 1
    assert client.get("/practice/account", headers=h).json()["available"] == "999.60"
    assert order(client, h, quantity="9", stop_loss="99")["status"] == "filled"


def test_closing_orders_need_the_position_and_skip_the_rules(client):
    h = _h(client)
    ready_stock()
    assert order(client, h, side="sell", quantity="1")["reason"].startswith("you hold 0 AAPL")
    assert client.post("/trades", json={"book": "practice", "ticker": "AAPL", "side": "buy", "quantity": "3", "fill": "90",
                                        "at": "2026-10-03T14:30:00Z"}, headers=h).status_code == 201
    assert order(client, h, side="sell", quantity="3")["status"] == "filled"     # no rules set: a sale still closes


def test_the_rules_refuse_what_breaks_them_and_name_the_rule(client):
    h = _h(client)
    deposit(client, h, 1000)
    ready_stock()
    set_rules(client, h)
    o = order(client, h, quantity="5")                          # no stop: the worst case is the whole 500.50
    assert o["status"] == "refused" and o["reason"].startswith("max_loss_per_idea_share:")
    assert order(client, h, quantity="5", stop_loss="99")["status"] == "filled"
    # earnings: none stored for MSFT → unknown → refused by the blackout rule, never passed by silence
    quote("MSFT", "49.9", "50.1")
    bars("MSFT")
    o = order(client, h, ticker="MSFT", quantity="1", stop_loss="49")
    assert o["status"] == "refused" and o["reason"].startswith("earnings_blackout_days:") and "unknown" in o["reason"]
    earnings("MSFT", 2)
    assert "earnings in 2 days" in order(client, h, ticker="MSFT", quantity="1", stop_loss="49")["reason"]
    # thin volume
    quote("TINY", "9.9", "10.1")
    bars("TINY", volume=1000)
    earnings("TINY", 40)
    assert order(client, h, ticker="TINY", quantity="1", stop_loss="9")["reason"].startswith("liquidity_min_volume:")
    # a wide spread
    quote("WIDE", "9.0", "10.0")
    bars("WIDE")
    earnings("WIDE", 40)
    assert order(client, h, ticker="WIDE", quantity="1", stop_loss="8")["reason"].startswith("liquidity_max_spread_share:")
    decisions = client.app.state.db.fetch_all("SELECT result, rule FROM risk_decisions WHERE subject_kind = 'order'")
    assert len(decisions) == 6 and sum(1 for d in decisions if d["result"] == "refuse") == 5


def test_limit_and_stop_orders_wait_for_the_quote_and_day_orders_expire(client):
    h = _h(client)
    deposit(client, h, 1000)
    ready_stock()
    set_rules(client, h)
    lim = order(client, h, type="limit", limit_price="90", tif="gtc", quantity="1", stop_loss="85")
    assert lim["status"] == "open"
    day = order(client, h, type="limit", limit_price="90", tif="day", quantity="1", stop_loss="85")
    stp = order(client, h, side="sell", type="stop", stop_price="95", tif="gtc", quantity="1")
    assert stp["reason"].startswith("you hold 0")
    p = Practice(client.app.state.db)
    assert p.fill_open_orders()["filled"] == 0
    assert set(o["status"] for o in client.get("/practice/orders", headers=h).json()["orders"]) == {"open", "refused"}
    acct = client.get("/practice/account", headers=h).json()
    assert len(acct["open_orders"]) == 2
    # the close: the day order expires, the GTC one stays. "The close" is the next session's (an order placed after
    # today's close, or on a weekend, belongs to the next session), so this holds at any hour of any day
    cal = Calendar.load(client.app.state.db)
    after = cal.closes_at(cal.next_session(dt.datetime.now(NEW_YORK).date())) + dt.timedelta(minutes=1)
    assert p.close_session(after)["expired_orders"] == 1
    assert client.get("/practice/orders?status=expired", headers=h).json()["orders"][0]["id"] == day["id"]
    # the market comes down to the limit: the GTC order fills at the ask
    quote("AAPL", "89.4", "89.5")
    assert p.fill_open_orders()["filled"] == 1
    got = client.get("/practice/orders?status=filled", headers=h).json()["orders"][0]
    assert got["id"] == lim["id"] and got["fill_price"] == "89.5000"
    # cancel: only an open order
    assert client.delete(f"/practice/orders/{lim['id']}", headers=h).status_code == 409
    gtc2 = order(client, h, type="limit", limit_price="80", tif="gtc", quantity="1", stop_loss="75")
    assert client.delete(f"/practice/orders/{gtc2['id']}", headers=h).json()["status"] == "cancelled"
    assert client.delete("/practice/orders/999", headers=h).status_code == 404


def test_cash_secured_puts_lock_collateral_and_are_assigned_or_expire_from_the_stored_price(client):
    h = _h(client)
    deposit(client, h, 2000)
    sym = occ.build("X", dt.date.today() + dt.timedelta(days=30), "put", D("10"))
    ready_stock("X", "10.95", "11.05")
    quote(sym, "0.50", "0.60")
    chain_row(sym, 800, "0.50", "0.60")
    set_rules(client, h, max_loss_per_idea_share="1")          # a put's worst case is (strike − premium) × 100
    o = client.post("/options/practice", json={"asset": "option", "side": "sell_to_open", "quantity": "1", "option_symbol": sym,
                                               "type": "market"}, headers=h).json()
    assert o["status"] == "filled", o["reason"]
    assert o["fill_price"] == "0.5000" and o["ticker"] == "X"
    acct = client.get("/practice/account", headers=h).json()
    assert acct["cash"] == "2050.00" and acct["locked_collateral"] == "1000.00" and acct["available"] == "1000.00"
    assert acct["short_puts"] == [{"option_symbol": sym, "underlying": "X", "expiry": sym and occ.parse(sym).expiry.isoformat(),
                                   "strike": "10", "contracts": "1", "collateral": "1000.00"}]
    # the premium is a sale: it settles next business day
    assert acct["unsettled"] == "50.00"
    # a second put needs 2000 of collateral; only 1000 (minus the unsettled premium) is set aside
    two = client.post("/options/practice", json={"asset": "option", "side": "sell_to_open", "quantity": "2", "option_symbol": sym}, headers=h).json()
    assert two["status"] == "refused" and two["reason"].startswith("cash_secured_puts_only:")
    # a call is never sold here; an expired symbol is refused
    call = occ.build("X", dt.date.today() + dt.timedelta(days=30), "call", D("12"))
    assert "only puts" in client.post("/options/practice", json={"asset": "option", "side": "sell_to_open", "quantity": "1", "option_symbol": call}, headers=h).json()["reason"]
    old = occ.build("X", dt.date.today() - dt.timedelta(days=1), "put", D("10"))
    assert "expired on" in client.post("/options/practice", json={"asset": "option", "side": "sell_to_open", "quantity": "1", "option_symbol": old}, headers=h).json()["reason"]
    # expiry, in the money: assigned 100 shares at the strike; the premium stays
    p = Practice(client.app.state.db)
    assert p.expire_options(dt.date.today()) == {"assigned": 0, "expired": 0, "waiting": 0}     # not yet
    quote("X", "8.9", "9.1", "9.0")
    assert p.expire_options(dt.date.today() + dt.timedelta(days=30)) == {"assigned": 1, "expired": 0, "waiting": 0}
    acct = client.get("/practice/account", headers=h).json()
    assert acct["short_puts"] == [] and acct["locked_collateral"] == "0.00" and acct["cash"] == "1050.00"
    pos = client.get("/portfolio", headers=h).json()["books"][1]["positions"]
    assert pos == [{**pos[0], "ticker": "X", "quantity": "100", "average_cost": "10.00"}]
    events = client.app.state.db.fetch_all("SELECT kind, strike, shares FROM option_events")
    assert [(e["kind"], e["strike"], e["shares"]) for e in events] == [("assignment", D("10.0000"), D("100.000000"))]
    hist = client.get("/history?book=practice&asset=option", headers=h).json()
    assert hist["totals"]["net_pnl"] == "50.00"


def test_an_out_of_the_money_put_expires_worthless_and_no_price_means_waiting(client):
    h = _h(client)
    deposit(client, h, 2000)
    sym = occ.build("Y", dt.date.today() + dt.timedelta(days=10), "put", D("10"))
    ready_stock("Y", "10.95", "11.05")
    quote(sym, "0.40", "0.50")
    chain_row(sym, 800, "0.40", "0.50")
    set_rules(client, h, max_loss_per_idea_share="1")
    o = client.post("/options/practice", json={"asset": "option", "side": "sell_to_open", "quantity": "1", "option_symbol": sym}, headers=h).json()
    assert o["status"] == "filled", o["reason"]
    p = Practice(client.app.state.db)
    _sql("DELETE FROM quotes WHERE ticker = 'Y'")
    _sql("DELETE FROM market_bars WHERE ticker = 'Y'")
    assert p.expire_options(dt.date.today() + dt.timedelta(days=10))["waiting"] == 1       # no stored price: no guess
    quote("Y", "11.9", "12.1", "12.0")
    assert p.expire_options(dt.date.today() + dt.timedelta(days=10))["expired"] == 1
    assert client.get("/practice/account", headers=h).json()["short_puts"] == []
    assert client.app.state.db.fetch_one("SELECT kind FROM option_events")["kind"] == "expiry"
    assert client.get("/history?book=practice", headers=h).json()["totals"]["net_pnl"] == "40.00"
    # buy to close needs an open leg
    r = client.post("/options/practice", json={"asset": "option", "side": "buy_to_close", "quantity": "1", "option_symbol": sym}, headers=h).json()
    assert r["status"] == "refused" and "0 open" in r["reason"]


def test_fees_apply_per_order_and_are_a_setting(client):
    h = _h(client)
    deposit(client, h, 1000)
    ready_stock()
    set_rules(client, h)
    assert client.put("/practice/settings", json={"fee_per_order": "0.65"}, headers=h).json() == {"fee_per_order": "0.65"}
    o = order(client, h, quantity="1", stop_loss="99")
    t = client.get(f"/trades/{o['trade_id']}", headers=h).json()
    assert t["fees"] == "0.65"
    assert client.get("/practice/account", headers=h).json()["cash"] == "899.25"


CHAIN = {"api.tradier.com/v1/markets/options/expirations": {"expirations": {"date": ["2026-11-20", "2026-12-18"]}},
         "api.tradier.com/v1/markets/options/chains": {"options": {"option": [
             {"symbol": "X261120P00010000", "bid": 0.5, "ask": 0.6, "last": 0.55, "volume": 120, "open_interest": 800,
              "greeks": {"delta": -0.3, "mid_iv": 0.42}},
             {"symbol": "X261120P00030000", "bid": 19.5, "ask": 20.5, "volume": 1, "open_interest": 10, "greeks": {"delta": -0.95}},
             {"symbol": "X261120C00010000", "bid": 1.0, "ask": 1.2, "open_interest": 300}]}}}


def test_the_chain_names_each_puts_collateral_and_whether_the_book_covers_it(client, monkeypatch):
    h = _h(client)
    deposit(client, h, 1500)
    r = client.get("/options/expirations?ticker=X", headers=h).json()
    assert r["configured"] is False and r["expirations"] == [] and "not configured" in r["reason"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CHAIN[request.url.host + request.url.path])
    monkeypatch.setenv("GHOSTLINE_KEY_TRADIER", "k")
    client.app.state.tradier = Tradier(httpx.Client(transport=httpx.MockTransport(handler)))
    r = client.get("/options/expirations?ticker=x", headers=h).json()
    assert r["configured"] and r["expirations"] == ["2026-11-20", "2026-12-18"]
    c = client.get("/options/chain?ticker=X&expiry=2026-11-20", headers=h).json()
    assert c["configured"] and c["available"] == "1500.00" and len(c["options"]) == 3
    puts = [o for o in c["options"] if o["kind"] == "put"]
    assert [(p["strike"], p["collateral"], p["covered"]) for p in puts] == [("10", "1000", True), ("30", "3000", False)]
    assert puts[0]["open_interest"] == "800" and puts[0]["delta"] == "-0.3" and puts[0]["iv"] == "0.42"
    assert [o for o in c["options"] if o["kind"] == "call"][0]["collateral"] is None
    # the chain's bid and ask are stored as the option's quote and in the snapshot table
    assert client.get("/quotes?tickers=X261120P00010000", headers=h).json()["quotes"]["X261120P00010000"]["bid"] == "0.5000"
    assert client.app.state.db.fetch_one("SELECT count(*) AS n FROM option_chains")["n"] == 3
    status = client.get("/market/status", headers=h).json()
    assert [p for p in status["pulls"] if p["source"] == "tradier"][0]["today"] == 2     # the unconfigured call was skipped, not counted


def test_the_worker_registers_the_practice_jobs(client):
    from apscheduler.schedulers.background import BackgroundScheduler

    from ghostline.worker import schedule
    s = BackgroundScheduler(timezone="America/Los_Angeles")
    ids = schedule.register(s, client.app.state.db)
    assert {"fill_orders", "close_session"} <= set(ids)


def test_drawdown_is_measured_from_the_books_peak():
    closed = [practice.ledger.Closed(1, 1, "A", D(1), D(150), D(100), dt.datetime(2026, 1, 1, tzinfo=dt.UTC), (1,)),
              practice.ledger.Closed(2, 1, "A", D(1), D(60), D(100), dt.datetime(2026, 1, 2, tzinfo=dt.UTC), (2,))]
    # deposits 1000, peak 1050 after the win, equity now 1010 → 40/1050
    assert Practice._drawdown([D(1000)], closed, D(1010)) == D("0.0381")
    assert Practice._drawdown([D(1000)], closed, D(1100)) == D(0)
    assert Practice._drawdown([], [], D(0)) == D(0)
