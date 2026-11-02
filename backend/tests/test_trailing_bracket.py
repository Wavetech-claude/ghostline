"""Practice order types (the owner's go, 2026-11-01): a trailing stop that follows the best price since it was placed
and never loosens, and a bracket whose buy opens a take-profit and a stop that cancel each other."""
from decimal import Decimal

from ghostline.practice import Practice, trail
from tests.test_practice import _h, deposit, order, quote, ready_stock, set_rules

D = Decimal


def _orders(client, h):
    return {o["id"]: o for o in client.get("/practice/orders?book=practice", headers=h).json()["orders"]}


def _tick(client, symbol, bid, ask):
    """A fresh quote, then the engine's pass over the open orders (the worker does this every 30 minutes)."""
    quote(symbol, bid, ask, bid)
    return Practice(client.app.state.db).fill_open_orders()


# ── the pure rule ──────────────────────────────────────────────────────────────────────────────────────────
def test_the_trail_follows_the_best_price_and_never_loosens():
    assert trail("sell", None, D(100), D(5), None) == (D(100), D("95.0000"))
    assert trail("sell", D(100), D(110), D(5), None) == (D(110), D("105.0000"))          # a new high lifts the stop
    assert trail("sell", D(110), D(104), D(5), None) == (D(110), D("105.0000"))          # a dip leaves it where it is
    assert trail("sell", None, D(200), None, D(10)) == (D(200), D("180.0000"))
    assert trail("buy", D(50), D(45), D(2), None) == (D(45), D("47.0000"))               # a buy follows the low
    assert trail("buy", D(45), D(48), D(2), None) == (D(45), D("47.0000"))


# ── trailing stops ─────────────────────────────────────────────────────────────────────────────────────────
def test_a_trailing_stop_ratchets_up_with_the_bid_then_sells_when_it_falls_through(client):
    h = _h(client)
    set_rules(client, h)
    ready_stock()                                                     # bid 99.90 ask 100.10
    deposit(client, h, 100_000)
    assert order(client, h, quantity="2", stop_loss="95")["status"] == "filled"
    t = order(client, h, side="sell", type="trailing_stop", trail_amount="5", tif="gtc")
    assert (t["status"], t["water_mark"], t["stop_price"]) == ("open", "99.9000", "94.9000")
    _tick(client, "AAPL", "110.00", "110.20")
    now = _orders(client, h)[t["id"]]
    assert (now["status"], now["water_mark"], now["stop_price"]) == ("open", "110.0000", "105.0000")
    _tick(client, "AAPL", "106.00", "106.20")                        # a dip above the stop: nothing moves
    assert _orders(client, h)[t["id"]]["stop_price"] == "105.0000"
    _tick(client, "AAPL", "104.50", "104.70")                        # through the stop: sells at the bid
    done = _orders(client, h)[t["id"]]
    assert done["status"] == "filled" and done["fill_price"] == "104.5000"


def test_a_percent_trail_and_the_ticket_rules(client):
    h = _h(client)
    set_rules(client, h)
    ready_stock()
    deposit(client, h, 100_000)
    order(client, h, quantity="2", stop_loss="95")
    t = order(client, h, side="sell", type="trailing_stop", trail_percent="10", tif="gtc")
    assert t["stop_price"] == "89.9100" and t["trail_percent"] == "10.0000"
    bad = [{"trail_amount": "5", "trail_percent": "5"}, {}, {"trail_amount": "5", "stop_price": "90"},
           {"trail_amount": "5", "asset": "option", "side": "buy_to_close", "option_symbol": "AAPL261218P00090000"}]
    for extra in bad:
        body = {"ticker": "AAPL", "side": "sell", "type": "trailing_stop", "quantity": "1", **extra}
        assert client.post("/practice/orders", json=body, headers=h).status_code == 422, extra
    body = {"ticker": "AAPL", "side": "sell", "type": "limit", "limit_price": "120", "quantity": "1", "trail_amount": "5"}
    assert client.post("/practice/orders", json=body, headers=h).status_code == 422             # only a trailing stop
    big = order(client, h, side="sell", type="trailing_stop", trail_amount="150", quantity="1")
    assert big["status"] == "refused" and "larger than the price" in big["reason"]


def test_replacing_a_trailing_stop_starts_its_mark_from_the_latest_price(client):
    h = _h(client)
    set_rules(client, h)
    ready_stock()
    deposit(client, h, 100_000)
    order(client, h, quantity="2", stop_loss="95")
    t = order(client, h, side="sell", type="trailing_stop", trail_amount="5", tif="gtc")
    _tick(client, "AAPL", "110.00", "110.20")
    quote("AAPL", "108.00", "108.20")
    r = client.post(f"/practice/orders/{t['id']}/replace", json={"trail_percent": "2"}, headers=h)
    assert r.status_code == 201, r.text
    new = r.json()
    assert (new["type"], new["trail_amount"], new["trail_percent"], new["water_mark"], new["stop_price"]) == \
        ("trailing_stop", None, "2.0000", "108.0000", "105.8400")                  # the new order's own mark
    assert client.post(f"/practice/orders/{new['id']}/replace", json={"stop_price": "100"}, headers=h).status_code == 409


# ── brackets ───────────────────────────────────────────────────────────────────────────────────────────────
def test_a_bracket_buy_opens_two_exits_and_the_one_that_fills_cancels_the_other(client):
    h = _h(client)
    set_rules(client, h)
    ready_stock()
    deposit(client, h, 100_000)
    b = order(client, h, quantity="2", take_profit="110", bracket_stop="95")
    assert b["status"] == "filled" and b["stop_loss"] == "95.0000"         # the stop is the risk check's worst case
    exits = [o for o in _orders(client, h).values() if o["parent_order_id"] == b["id"]]
    assert sorted((o["type"], o["limit_price"] or o["stop_price"], o["quantity"], o["tif"], o["status"]) for o in exits) == \
        [("limit", "110.0000", "2", "gtc", "open"), ("stop", "95.0000", "2", "gtc", "open")]
    _tick(client, "AAPL", "111.00", "111.20")                                # the target is reached
    after = {o["type"]: o for o in _orders(client, h).values() if o["parent_order_id"] == b["id"]}
    assert after["limit"]["status"] == "filled" and after["limit"]["fill_price"] == "111.0000"
    assert after["stop"]["status"] == "cancelled" and "other side of the bracket filled" in after["stop"]["reason"]
    assert client.get("/practice/account", headers=h).json()["open_positions"] == 0


def test_cancelling_one_exit_ends_the_bracket_and_a_replaced_exit_stays_in_it(client):
    h = _h(client)
    set_rules(client, h)
    ready_stock()
    deposit(client, h, 100_000)
    b = order(client, h, quantity="2", take_profit="110", bracket_stop="95")
    exits = {o["type"]: o for o in _orders(client, h).values() if o["parent_order_id"] == b["id"]}
    r = client.post(f"/practice/orders/{exits['stop']['id']}/replace", json={"stop_price": "97"}, headers=h)
    assert r.status_code == 201 and r.json()["parent_order_id"] == b["id"]
    assert client.post(f"/practice/orders/{r.json()['id']}/replace", json={"quantity": "1"}, headers=h).status_code == 409
    assert client.delete(f"/practice/orders/{exits['limit']['id']}", headers=h).status_code == 200
    now = _orders(client, h)
    assert now[r.json()["id"]]["status"] == "cancelled" and "bracket was cancelled" in now[r.json()["id"]]["reason"]


def test_a_limit_bracket_opens_its_exits_only_when_it_fills_and_the_ticket_rules(client):
    h = _h(client)
    set_rules(client, h)
    ready_stock()
    deposit(client, h, 100_000)
    b = order(client, h, quantity="1", type="limit", limit_price="98", take_profit="110", bracket_stop="95", tif="gtc")
    assert b["status"] == "open" and not [o for o in _orders(client, h).values() if o["parent_order_id"] == b["id"]]
    _tick(client, "AAPL", "97.80", "97.90")
    assert len([o for o in _orders(client, h).values() if o["parent_order_id"] == b["id"]]) == 2
    bad = [{"take_profit": "110"}, {"take_profit": "90", "bracket_stop": "95"},
           {"take_profit": "110", "bracket_stop": "95", "side": "sell"},
           {"take_profit": "110", "bracket_stop": "95", "type": "limit", "limit_price": "120"}]
    for extra in bad:
        body = {"ticker": "AAPL", "side": "buy", "quantity": "1", **extra}
        assert client.post("/practice/orders", json=body, headers=h).status_code == 422, extra


def test_invest_this_buys_a_swing_idea_as_a_bracket(client):
    from ghostline.invest import bracket_for
    assert bracket_for("swing", "95", "110") == {"take_profit": D("110"), "bracket_stop": D("95")}
    assert bracket_for("long", "95", "110") == {} and bracket_for("swing", "95", None) == {}
    assert bracket_for("swing", "110", "95") == {}                          # a target under the stop: no bracket


def test_an_order_cancelled_during_a_pass_never_fills(client):
    # the pass reads the open orders once; an exit cancelled by its sibling after that read must not fill
    h = _h(client)
    set_rules(client, h)
    ready_stock()
    deposit(client, h, 100_000)
    b = order(client, h, quantity="2", take_profit="110", bracket_stop="95")
    db = client.app.state.db
    stale = db.fetch_one("SELECT * FROM orders WHERE parent_order_id = %s AND type = 'stop'", (b["id"],))
    db.execute("UPDATE orders SET status = 'cancelled' WHERE id = %s", (stale["id"],))
    q = {"bid": D("90"), "ask": D("90.2"), "source": "test", "ts": stale["placed_at"]}
    assert Practice(db).try_fill(stale, q) is False
    assert db.fetch_one("SELECT status, trade_id FROM orders WHERE id = %s", (stale["id"],)) == \
        {"status": "cancelled", "trade_id": None}
