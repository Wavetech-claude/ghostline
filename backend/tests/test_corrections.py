"""Edit, void and reset (migration 0015): the real book's entries are corrected or voided, never deleted, and every
number leaves a voided entry out; a practice order is replaced; a simulated book is reset without touching learning."""
import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from ghostline import corporate, corrections, edge, statements
from tests.conftest import TEST_DB, sign_in
from tests.test_invest_ai import bar, flow, idea
from tests.test_practice import deposit, order, quote, ready_stock, set_rules

D = Decimal
BACKEND = Path(__file__).resolve().parent.parent / "ghostline"


def _h(client):
    return {"Authorization": f"Bearer {sign_in(client)}"}


def _one(sql, params=()):
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        return conn.execute(sql, params).fetchone()


def trade(client, h, book="real", ticker="AAPL", side="buy", quantity="10", fill="100", at="2026-10-05T14:30:00Z",
          **kw):
    r = client.post("/trades", json={"book": book, "ticker": ticker, "side": side, "quantity": quantity, "fill": fill,
                                     "at": at, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def real_book(client, h):
    return next(b for b in client.get("/books", headers=h).json()["books"] if b["kind"] == "real")


# ── the guard: no reader can forget the voided rows ────────────────────────────────────────────────────────
def test_voided_rows_guard():
    """Backend code reads live_trades / live_cash_flows. A read of the base tables is allowed only on a line that says
    it wants voided rows too ("includes voided"): the history views and the corrections themselves."""
    pat = re.compile(r"\b(FROM|JOIN)\s+(trades|cash_flows)\b")
    offenders = []
    for p in BACKEND.rglob("*.py"):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line) and "includes voided" not in line:
                offenders.append(f"{p.relative_to(BACKEND)}:{n}: {line.strip()}")
    assert offenders == [], "read live_trades / live_cash_flows instead:\n" + "\n".join(offenders)


def test_live_views_have_every_column(client):
    """A view's columns are fixed when it is made: a migration that adds a column to the base table must re-create
    the live view, or the readers silently lose the column."""
    def cols(t):
        with psycopg.connect(TEST_DB) as conn:
            return [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s "
                                               "ORDER BY ordinal_position", (t,)).fetchall()]
    assert cols("live_trades") == cols("trades")
    assert cols("live_cash_flows") == cols("cash_flows")


# ── edit ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_edit_any_field_of_a_real_trade_keeps_an_edit_history(client):
    h = _h(client)
    tid = trade(client, h, fees="1", broker="Robinhood")
    r = client.patch(f"/trades/{tid}", json={"ticker": "msft", "quantity": "12", "fill": "101.5",
                                             "at": "2026-10-06T15:00:00Z", "fees": "0", "broker": "Fidelity",
                                             "note": "typo fixed", "lane": "swing"}, headers=h)
    assert r.status_code == 200, r.text
    t = r.json()
    assert (t["ticker"], t["quantity"], t["fill"], t["fees"], t["broker"], t["note"], t["lane"]) == \
        ("MSFT", "12", "101.5000", "0.00", "Fidelity", "typo fixed", "swing")
    assert sorted(t["changed"]) == ["at", "broker", "fees", "fill", "lane", "note", "quantity", "ticker"]
    edits = {e["field"]: (e["old"], e["new"]) for e in t["edits"]}
    assert edits["ticker"] == ("AAPL", "MSFT") and edits["quantity"] == ("10", "12") and edits["broker"] == ("Robinhood", "Fidelity")
    assert all(e["action"] == "edit" and e["by_whom"] == "owner" for e in t["edits"])
    # the same values again change nothing and log nothing
    again = client.patch(f"/trades/{tid}", json={"quantity": "12"}, headers=h).json()
    assert again["changed"] == [] and len(again["edits"]) == 8
    # a note can be cleared; a quantity cannot
    assert client.patch(f"/trades/{tid}", json={"note": None}, headers=h).json()["note"] is None
    assert client.patch(f"/trades/{tid}", json={"quantity": None}, headers=h).status_code == 422
    # the result is checked like a new trade
    assert client.patch(f"/trades/{tid}", json={"side": "sell_to_open"}, headers=h).status_code == 422   # a stock
    assert client.patch(f"/trades/{tid}", json={"asset": "option"}, headers=h).status_code == 422          # no symbol
    assert client.patch(f"/trades/{tid}", json={"quantity": "-1"}, headers=h).status_code == 422
    assert client.patch(f"/trades/{tid}", json={}, headers=h).status_code == 422
    assert client.patch("/trades/9999", json={"fees": "1"}, headers=h).status_code == 404
    # an option leg with its symbol is fine
    ok = client.patch(f"/trades/{tid}", json={"asset": "option", "side": "sell_to_open", "option_symbol": "MSFT261121P00100000",
                                              "quantity": "1"}, headers=h)
    assert ok.status_code == 200, ok.text


def test_only_the_real_book_is_edited_and_an_actions_entry_only_voided(client):
    h = _h(client)
    pid = trade(client, h, book="practice")
    r = client.patch(f"/trades/{pid}", json={"fees": "1"}, headers=h)
    assert r.status_code == 409 and "reset the book" in r.json()["detail"]
    assert client.post(f"/trades/{pid}/void", json={"reason": "x"}, headers=h).status_code == 409
    trade(client, h)
    split = _one("INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at, fees, broker, note) SELECT id, "
                 "'AAPL', 'stock', 'split', 2, 1, '2026-10-07T08:00:00Z', 0, 'corporate action', 'AAPL 2-for-1' FROM books "
                 "WHERE kind = 'real' RETURNING id")[0]
    r = client.patch(f"/trades/{split}", json={"note": "x"}, headers=h)
    assert r.status_code == 409 and "corporate action" in r.json()["detail"]
    assert client.post(f"/trades/{split}/void", json={"reason": "broker shows no split"}, headers=h).status_code == 200


# ── void ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_voided_trade_leaves_every_number_and_unvoid_brings_it_back(client):
    h = _h(client)
    db = client.app.state.db
    deposit(client, h, 2000, book="real")
    trade(client, h, ticker="AAPL", quantity="10", fill="100", at="2026-10-05T14:30:00Z")
    trade(client, h, ticker="AAPL", side="sell", quantity="5", fill="120", at="2026-10-06T14:30:00Z")
    wrong = trade(client, h, ticker="MSFT", quantity="3", fill="50", at="2026-10-07T14:30:00Z")
    quote("AAPL", "119", "121", "120")
    quote("MSFT", "49", "51", "50")

    def snapshot():
        book = real_book(client, h)
        port = next(b for b in client.get("/portfolio", headers=h).json()["books"] if b["book"] == "real")
        hist = client.get("/history?book=real", headers=h).json()
        st = statements.build(db, "real", "2026-10")
        pos = {r["ticker"]: r["quantity"] for r in db.fetch_all(
            "SELECT p.ticker, p.quantity FROM positions p JOIN books b ON b.id = p.book_id WHERE b.kind = 'real'")}
        held = corporate.held(db, book["id"], "MSFT", dt.datetime(2026, 10, 8, tzinfo=dt.UTC))
        return (book["cash"], book["trades"], sorted(p["ticker"] for p in port["positions"]), hist["totals"]["net_pnl"],
                sorted(o["ticker"] for o in hist["open"]), len(st["trades"]), st["cash_at_end"], pos, held)

    before = snapshot()
    assert before[0] == "1450.00" and before[2] == ["AAPL", "MSFT"] and before[8] == D(3)
    r = client.post(f"/trades/{wrong}/void", json={"reason": "never bought MSFT"}, headers=h)
    assert r.status_code == 200 and r.json()["voided_ids"] == [wrong] and r.json()["void_reason"] == "never bought MSFT"
    assert [e["action"] for e in r.json()["edits"]] == ["void"]
    after = snapshot()
    assert after[0] == "1600.00" and after[1] == 2 and after[2] == ["AAPL"] and after[3] == before[3] == "100.00"
    assert after[4] == ["AAPL"] and after[5] == 2 and after[6] == "1600.00" and "MSFT" not in after[7] and after[8] == 0
    # shown apart, struck through, only on request
    assert all(t["id"] != wrong for t in client.get("/trades?book=real", headers=h).json()["trades"])
    listed = client.get("/trades?book=real&include_voided=true", headers=h).json()["trades"]
    assert next(t for t in listed if t["id"] == wrong)["voided_at"] is not None
    hist = client.get("/history?book=real&include_voided=true", headers=h).json()
    assert [t["id"] for t in hist["voided"]] == [wrong] and "voided" not in client.get("/history", headers=h).json()
    assert client.get(f"/trades/{wrong}", headers=h).json()["void_reason"] == "never bought MSFT"
    # no reason, no void; twice is refused
    assert client.post(f"/trades/{wrong}/void", json={"reason": ""}, headers=h).status_code == 422
    assert client.post(f"/trades/{wrong}/void", json={"reason": "again"}, headers=h).status_code == 409
    assert client.patch(f"/trades/{wrong}", json={"fees": "1"}, headers=h).status_code == 409   # unvoid first
    # unvoid: everything as it was
    r = client.post(f"/trades/{wrong}/unvoid", headers=h)
    assert r.status_code == 200 and r.json()["voided_at"] is None and [e["action"] for e in r.json()["edits"]] == ["void", "unvoid"]
    assert snapshot() == before
    assert client.post(f"/trades/{wrong}/unvoid", headers=h).status_code == 409


def test_a_change_that_would_oversell_is_refused_naming_the_sale(client):
    h = _h(client)
    buy = trade(client, h, quantity="10")
    sale = trade(client, h, side="sell", quantity="6", fill="110", at="2026-10-06T14:30:00Z")
    r = client.post(f"/trades/{buy}/void", json={"reason": "wrong"}, headers=h)
    assert r.status_code == 409 and f"#{sale}" in r.json()["detail"]
    assert client.patch(f"/trades/{buy}", json={"quantity": "5"}, headers=h).status_code == 409
    assert client.patch(f"/trades/{buy}", json={"at": "2026-10-07T14:30:00Z"}, headers=h).status_code == 409   # after the sale
    assert client.patch(f"/trades/{sale}", json={"quantity": "11"}, headers=h).status_code == 409
    assert client.patch(f"/trades/{buy}", json={"quantity": "7"}, headers=h).status_code == 200
    # the sale first, then the buy
    assert client.post(f"/trades/{sale}/void", json={"reason": "wrong"}, headers=h).status_code == 200
    assert client.post(f"/trades/{buy}/void", json={"reason": "wrong"}, headers=h).status_code == 200
    r = client.post(f"/trades/{sale}/unvoid", headers=h)
    assert r.status_code == 409 and f"#{sale}" in r.json()["detail"]
    assert client.post(f"/trades/{buy}/unvoid", headers=h).status_code == 200
    assert client.post(f"/trades/{sale}/unvoid", headers=h).status_code == 200


def test_a_conversion_is_voided_as_one(client):
    h = _h(client)
    trade(client, h, ticker="OLD", quantity="10", fill="20")
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        bid = conn.execute("SELECT id FROM books WHERE kind = 'real'").fetchone()[0]
        out_id = conn.execute(
            "INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at, fees, broker, note, related_ticker) "
            "VALUES (%s, 'OLD', 'stock', 'convert_out', 10, 0, '2026-10-08T08:00:00Z', 0, 'corporate action', 'x', 'NEW') "
            "RETURNING id", (bid,)).fetchone()[0]
        in_id = conn.execute(
            "INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at, fees, broker, note, related_ticker) "
            "VALUES (%s, 'NEW', 'stock', 'convert_in', 5, 0, '2026-10-08T08:00:00Z', 0, 'corporate action', 'x', 'OLD') "
            "RETURNING id", (bid,)).fetchone()[0]
    db = client.app.state.db
    def held(t):
        row = db.fetch_one("SELECT quantity FROM positions WHERE book_id = %s AND ticker = %s", (bid, t))
        return row["quantity"] if row else 0
    assert (held("OLD"), held("NEW")) == (0, 5)
    r = client.post(f"/trades/{in_id}/void", json={"reason": "no merger at my broker"}, headers=h)
    assert r.status_code == 200 and r.json()["voided_ids"] == sorted([out_id, in_id])
    assert (held("OLD"), held("NEW")) == (10, 0)
    assert client.post(f"/trades/{out_id}/unvoid", headers=h).json()["unvoided_ids"] == sorted([out_id, in_id])
    assert (held("OLD"), held("NEW")) == (0, 5)


def test_cash_flows_are_edited_and_voided_with_history(client):
    h = _h(client)
    fid = client.post("/cash-flows", json={"book": "real", "amount": "500", "at": "2026-10-01T00:00:00Z"}, headers=h).json()["id"]
    client.post("/cash-flows", json={"book": "real", "amount": "100", "at": "2026-10-02T00:00:00Z"}, headers=h)
    r = client.patch(f"/cash-flows/{fid}", json={"amount": "450", "note": "typo"}, headers=h)
    assert r.status_code == 200 and r.json()["amount"] == "450.00" and sorted(r.json()["changed"]) == ["amount", "note"]
    assert {e["field"]: (e["old"], e["new"]) for e in r.json()["edits"]}["amount"] == ("500", "450")
    assert client.patch(f"/cash-flows/{fid}", json={"amount": "0"}, headers=h).status_code == 422
    assert client.patch(f"/cash-flows/{fid}", json={"amount": None}, headers=h).status_code == 422
    assert real_book(client, h)["deposits"] == "550.00"
    assert client.post(f"/cash-flows/{fid}/void", json={"reason": "duplicate"}, headers=h).status_code == 200
    assert real_book(client, h)["deposits"] == "100.00" and real_book(client, h)["cash"] == "100.00"
    assert [f["amount"] for f in client.get("/cash-flows?book=real", headers=h).json()["cash_flows"]] == ["100.00"]
    listed = client.get("/cash-flows?book=real&include_voided=true", headers=h).json()["cash_flows"]
    assert [f["void_reason"] for f in listed] == [None, "duplicate"]
    assert client.patch(f"/cash-flows/{fid}", json={"note": "x"}, headers=h).status_code == 409
    assert client.post(f"/cash-flows/{fid}/unvoid", headers=h).status_code == 200
    assert real_book(client, h)["deposits"] == "550.00"
    pf = client.post("/cash-flows", json={"book": "practice", "amount": "5", "at": "2026-10-02T00:00:00Z"}, headers=h).json()["id"]
    assert client.post(f"/cash-flows/{pf}/void", json={"reason": "x"}, headers=h).status_code == 409
    assert client.get("/cash-flows/9999", headers=h).status_code == 404


# ── replace a practice order ───────────────────────────────────────────────────────────────────────────────
def test_replace_an_open_limit_order(client):
    h = _h(client)
    set_rules(client, h)
    ready_stock()
    deposit(client, h, 100_000)
    old = order(client, h, type="limit", limit_price="90", tif="day")
    assert old["status"] == "open"
    r = client.post(f"/practice/orders/{old['id']}/replace", json={"limit_price": "95", "quantity": "3", "tif": "gtc"},
                    headers=h)
    assert r.status_code == 201, r.text
    new = r.json()
    assert (new["status"], new["type"], new["limit_price"], new["quantity"], new["tif"], new["replaces_order_id"]) == \
        ("open", "limit", "95.0000", "3", "gtc", old["id"])
    was = client.get("/practice/orders?book=practice", headers=h).json()["orders"]
    old_now = next(o for o in was if o["id"] == old["id"])
    assert old_now["status"] == "replaced" and old_now["reason"] == f"replaced by order {new['id']}"
    # only an open order; a refused replacement leaves the order open and unchanged
    assert client.post(f"/practice/orders/{old['id']}/replace", json={"limit_price": "96"}, headers=h).status_code == 409
    r = client.post(f"/practice/orders/{new['id']}/replace", json={"quantity": "1000"}, headers=h)
    assert r.status_code == 409, r.text
    assert next(o for o in client.get("/practice/orders", headers=h).json()["orders"] if o["id"] == new["id"])["status"] == "open"
    assert client.post(f"/practice/orders/{new['id']}/replace", json={}, headers=h).status_code == 422
    assert client.post("/practice/orders/9999/replace", json={"quantity": "1"}, headers=h).status_code == 404
    # a replacement the market already allows fills at once, as a new order does
    filled = client.post(f"/practice/orders/{new['id']}/replace", json={"limit_price": "101"}, headers=h).json()
    assert filled["status"] == "filled" and filled["fill_price"] == "100.1000"


def test_a_market_or_filled_order_is_not_replaced(client):
    h = _h(client)
    set_rules(client, h)
    ready_stock()
    deposit(client, h, 100_000)
    done = order(client, h)                       # market: fills at once
    assert done["status"] == "filled"
    r = client.post(f"/practice/orders/{done['id']}/replace", json={"quantity": "1"}, headers=h)
    assert r.status_code == 409 and "not open" in r.json()["detail"]
    waiting = _one("INSERT INTO orders (book_id, ticker, asset, side, type, tif, quantity, status) SELECT id, 'AAPL', "
                   "'stock', 'buy', 'market', 'day', 1, 'open' FROM books WHERE kind = 'practice' RETURNING id")[0]
    r = client.post(f"/practice/orders/{waiting}/replace", json={"quantity": "1"}, headers=h)
    assert r.status_code == 409 and "market order" in r.json()["detail"]


# ── reset ──────────────────────────────────────────────────────────────────────────────────────────────────
LEARNING = ("signals", "ideas", "paper_results", "module_weights", "lane_graduation", "lessons", "eval_runs",
            "learning_settings")


def _learning_state():
    out = {}
    with psycopg.connect(TEST_DB) as conn:
        for t in LEARNING:
            out[t] = conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()  # noqa: S608 — fixed names
    return out


def test_reset_a_practice_book_keeps_everything_and_leaves_learning_alone(client):
    h = _h(client)
    db = client.app.state.db
    set_rules(client, h)
    ready_stock()
    deposit(client, h, 100_000)
    idea("AAPL")
    order(client, h, quantity="5")                                    # fills
    open_order = order(client, h, type="limit", limit_price="80")    # stays open
    trade(client, h, quantity="1")                                    # the real book is not touched
    learning, edge_before = _learning_state(), edge.compute(db)
    r = client.post("/practice/reset", json={"book": "practice"}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    today = out["reason"][len("reset "):]
    assert out["trades"] == 1 and out["cash_flows"] == 1 and out["orders_cancelled"] == 1 and out["reason"].startswith("reset ")
    acct = client.get("/practice/account", headers=h).json()
    assert (acct["cash"], acct["equity"], acct["open_positions"], acct["open_orders"], acct["last_reset"]) == \
        ("0.00", "0.00", 0, [], today)
    orders = {o["id"]: o for o in client.get("/practice/orders", headers=h).json()["orders"]}
    assert orders[open_order["id"]]["status"] == "cancelled"
    voided = client.get("/trades?book=practice&include_voided=true", headers=h).json()["trades"]
    assert [t["void_reason"] for t in voided] == [out["reason"]]
    assert len(client.get("/trades?book=real", headers=h).json()["trades"]) == 1
    assert _learning_state() == learning and edge.compute(db) == edge_before
    # a fresh start with a deposit; a second reset voids only what came after
    r = client.post("/practice/reset", json={"book": "practice", "deposit": "500"}, headers=h).json()
    assert r["trades"] == 0 and r["cash_flows"] == 0 and r["deposit_id"]
    assert client.get("/practice/account", headers=h).json()["cash"] == "500.00"
    assert client.post("/practice/reset", json={"book": "real"}, headers=h).status_code == 422
    with pytest.raises(corrections.Refused):
        corrections.reset(db, "real")


def test_reset_the_ai_book_takes_the_ghost_with_it(client):
    h = _h(client)
    bar("SPY", dt.date(2026, 9, 2), 400)
    out = flow(client, h, "ai", 1000, dt.date(2026, 9, 2))
    assert out["ghost"] == {"mirrored": 1, "waiting": 0}
    books = {b["kind"]: b for b in client.get("/books", headers=h).json()["books"]}
    assert books["ghost"]["deposits"] == "1000.00" and books["ghost"]["trades"] == 1
    quote("SPY", "399", "401", "400")                                            # today's price for the new deposit
    r = client.post("/practice/reset", json={"book": "ai", "deposit": "300"}, headers=h).json()
    assert r["trades"] == 1 and r["cash_flows"] == 2 and r["ghost"] is True     # the AI flow and the ghost's
    books = {b["kind"]: b for b in client.get("/books", headers=h).json()["books"]}
    assert books["ai"]["deposits"] == "300.00" and books["ghost"]["deposits"] == "300.00"
    assert books["ghost"]["trades"] == 1                                         # the new deposit's SPY buy
    assert r["ghost_sync"] == {"mirrored": 1, "waiting": 0}
