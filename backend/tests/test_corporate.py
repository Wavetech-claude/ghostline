"""Corporate actions: a split scales every open lot at the same cost (the ledger, the positions view, the value series),
a dividend is cash as return, Alpaca's answer is read, and actions are applied to the simulated books and wait for the
owner in the real one."""
import datetime as dt
from decimal import Decimal

import httpx
import psycopg

from ghostline import corporate, ledger, series
from ghostline.db import Database
from ghostline.ledger import Trade
from ghostline.market.jobs import Jobs
from ghostline.market.sources import Alpaca
from tests.conftest import TEST_DB, settings
from tests.test_invest_ai import _h, bar

T0 = dt.datetime(2026, 6, 1, 15, tzinfo=dt.UTC)


def _t(i, side, q, fill, days, ticker="NVDA"):
    return Trade(id=i, book_id=1, ticker=ticker, asset="stock", side=side, quantity=Decimal(q), fill=Decimal(fill),
                 fees=Decimal(0), at=T0 + dt.timedelta(days=days))


def _sql(sql, params=()):
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return cur.fetchone() if cur.description else None


def _rows(sql, params=()):
    """Every row, on a connection that is closed after: an open one holds its transaction and the next test's schema
    reset waits for ever."""
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        return conn.execute(sql, params).fetchall()


def _book(kind):
    return _sql("SELECT id FROM books WHERE kind = %s", (kind,))[0]


def _trade(book_id, ticker, side, q, fill, at):
    return _sql("INSERT INTO trades (book_id, ticker, asset, side, quantity, fill, at) VALUES (%s, %s, 'stock', %s, %s, "
                "%s, %s) RETURNING id", (book_id, ticker, side, q, fill, at))[0]


def test_a_split_scales_every_open_lot_at_the_same_cost():
    closed, pos = ledger.settle([_t(1, "buy", 10, 100, 0), _t(2, "buy", 10, 120, 1), _t(3, "split", 4, 1, 2)])
    (p,) = pos
    assert (p.quantity, p.cost_basis, p.average_cost) == (Decimal(80), Decimal("2200.00"), Decimal("27.50"))
    closed, pos = ledger.settle([_t(1, "buy", 10, 100, 0), _t(2, "split", 4, 1, 1), _t(3, "sell", 40, 30, 2)])
    assert [c.pnl for c in closed] == [Decimal("200.00")] and pos == []          # 40 × 30 − 1000: no gain from the split
    _, (p,) = ledger.settle([_t(1, "buy", 30, 10, 0), _t(2, "split", 1, 3, 1)])   # 1-for-3: exact
    assert (p.quantity, p.cost_basis) == (Decimal(10), Decimal("300.00"))


def test_a_split_moves_no_cash_and_a_dividend_is_cash():
    trades = [_t(1, "buy", 10, 50, 0), _t(2, "split", 2, 1, 1), _t(3, "dividend", 1, "8.30", 2)]
    assert ledger.cash_balance([Decimal(1000)], trades) == Decimal("508.30")
    assert ledger.day_pnl(Decimal(20), Decimal(26), Decimal(25), trades[1:]) == Decimal("20.00")   # neither is a trade


def test_the_positions_view_counts_shares_after_every_split(fresh_db):
    practice = _book("practice")
    _trade(practice, "AAA", "buy", 10, 100, T0)
    _trade(practice, "AAA", "split", 4, 1, T0 + dt.timedelta(days=1))
    _trade(practice, "AAA", "buy", 5, 26, T0 + dt.timedelta(days=2))                # after the split: not scaled
    _trade(practice, "BBB", "buy", 30, 10, T0)
    _trade(practice, "BBB", "split", 1, 3, T0 + dt.timedelta(days=1))
    rows = dict(_rows("SELECT ticker, quantity FROM positions WHERE book_id = %s", (practice,)))
    assert rows == {"AAA": Decimal("45.000000"), "BBB": Decimal("10.000000")}


def test_the_value_series_counts_shares_in_todays_terms(fresh_db):
    """Closes are split-adjusted: before the split, 10 shares at an adjusted 50 must still be worth 1,000."""
    db = Database(settings())
    practice = _book("practice")
    _sql("INSERT INTO cash_flows (book_id, amount, at) VALUES (%s, 1000, %s)", (practice, T0))
    _trade(practice, "AAA", "buy", 10, 100, T0 + dt.timedelta(minutes=5))
    _trade(practice, "AAA", "split", 2, 1, dt.datetime(2026, 6, 2, 8, tzinfo=dt.UTC))
    for day in (1, 2, 3):
        bar("AAA", dt.date(2026, 6, day), 50)
    pts = series.equity_series(db, "practice", dt.date(2026, 6, 3))["points"]
    assert [p["value"] for p in pts] == ["1000.00", "1000.00", "1000.00"] and pts[-1]["twr_pct"] == "0.00"
    db.close()


# Alpaca's answer, read live 2026-10-30 (NVDA, June 2024)
NVDA = {"corporate_actions": {
    "cash_dividends": [{"cusip": "67066G104", "ex_date": "2024-06-11", "foreign": False, "id": "div-1",
                        "payable_date": "2024-06-28", "process_date": "2024-06-28", "rate": 0.01,
                        "record_date": "2024-06-11", "special": False, "symbol": "NVDA"}],
    "forward_splits": [{"cusip": "67066G104", "due_bill_redemption_date": "2024-06-10", "ex_date": "2024-06-10",
                        "id": "split-1", "new_rate": 10, "old_rate": 1, "payable_date": "2024-06-10",
                        "process_date": "2024-06-10", "record_date": "2024-06-07", "symbol": "NVDA"}]},
    "next_page_token": None}


def _alpaca(monkeypatch, answer=NVDA):
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA", "test-alpaca")
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA_SECRET", "test-alpaca-secret")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=answer)
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_alpacas_corporate_actions_are_read(monkeypatch):
    http, seen = _alpaca(monkeypatch)
    out = {a.kind: a for a in Alpaca(http).corporate_actions(["NVDA"], dt.date(2024, 6, 1), dt.date(2024, 6, 30))}
    assert (out["forward_split"].new_rate, out["forward_split"].old_rate) == (Decimal(10), Decimal(1))
    assert out["cash_dividend"].rate == Decimal("0.01") and out["cash_dividend"].payable_date == dt.date(2024, 6, 28)
    assert seen[0].url.path == "/v1/corporate-actions" and seen[0].headers["APCA-API-KEY-ID"] == "test-alpaca"


def test_actions_apply_to_simulated_books_and_wait_for_the_owner_in_the_real_one(client, monkeypatch):
    h = _h(client)
    practice, real = _book("practice"), _book("real")
    before = dt.datetime(2024, 6, 3, 15, tzinfo=dt.UTC)
    _trade(practice, "NVDA", "buy", 10, 1000, before)
    _trade(real, "NVDA", "buy", 5, 1000, before)
    _sql("INSERT INTO watchlist (ticker) VALUES ('NVDA')")
    http, _ = _alpaca(monkeypatch)
    db = client.app.state.db
    assert Jobs(db, http).pull_corporate_actions(today=dt.date(2024, 6, 20)) == {"actions": 2}
    assert Jobs(db, http).pull_corporate_actions(today=dt.date(2024, 6, 20)) == {"actions": 2}     # upserted

    assert corporate.apply(db, dt.date(2024, 6, 10)) == {"applied": 1, "pending": 1, "waiting": 0}   # not ex yet
    assert corporate.apply(db, dt.date(2024, 6, 11)) == {"applied": 0, "pending": 0, "waiting": 1}   # ex, not paid
    held = dict(_rows("SELECT book_id, quantity FROM positions"))
    assert held == {practice: Decimal("100.000000"), real: Decimal("5.000000")}    # the real book waits

    (waiting,) = client.get("/corporate-actions", headers=h).json()["pending"]
    assert waiting["id"] == "split-1" and waiting["text"] == "NVDA 10-for-1 split: 5 shares become 50"
    assert client.post("/corporate-actions/split-1/confirm", headers=h).json()["status"] == "confirmed"
    assert client.post("/corporate-actions/split-1/confirm", headers=h).status_code == 404       # nothing waits now
    held = dict(_rows("SELECT book_id, quantity FROM positions"))
    assert held[real] == Decimal("50.000000")

    # the dividend: paid on the shares held when NVDA went ex (after the split), on its payable date
    assert corporate.apply(db, dt.date(2024, 6, 28)) == {"applied": 1, "pending": 1, "waiting": 0}
    paid = _sql("SELECT fill FROM trades WHERE book_id = %s AND side = 'dividend'", (practice,))[0]
    assert paid == Decimal("1.0000")                                                 # 100 × 0.01
    (div,) = client.get("/corporate-actions", headers=h).json()["pending"]
    assert div["amount"] == "0.50"
    r = client.post("/corporate-actions/div-1/confirm", json={"amount": "0.49"}, headers=h)   # what the broker paid
    assert r.status_code == 200 and _sql("SELECT fill FROM trades WHERE id = %s", (r.json()["trade_id"],))[0] == \
        Decimal("0.4900")
    assert client.get("/statements?book=real&month=2024-06", headers=h).json()["dividends"] == "0.49"


def test_the_owner_can_say_the_broker_does_not_show_it(client, monkeypatch):
    h = _h(client)
    _trade(_book("real"), "NVDA", "buy", 5, 1000, dt.datetime(2024, 6, 3, 15, tzinfo=dt.UTC))
    _sql("INSERT INTO watchlist (ticker) VALUES ('NVDA')")
    http, _ = _alpaca(monkeypatch)
    db = client.app.state.db
    Jobs(db, http).pull_corporate_actions(today=dt.date(2024, 6, 20))
    corporate.apply(db, dt.date(2024, 6, 10))
    assert client.post("/corporate-actions/split-1/dismiss", headers=h).json() == {
        "id": "split-1", "status": "dismissed", "trade_id": None}
    assert client.get("/corporate-actions", headers=h).json()["pending"] == []
    assert _sql("SELECT count(*) FROM trades WHERE side = 'split'")[0] == 0
