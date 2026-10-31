"""Reconciliation: each suggestion's outcome (took, declined, lapsed, open), own calls, the paper and real results, the
owner's "no" and its undo, the broker check, and the monthly reminder."""
import datetime as dt

import psycopg

from ghostline import reconcile
from ghostline.db import Database
from ghostline.market.calendar import Calendar
from tests.conftest import TEST_DB, settings
from tests.test_invest_ai import _h, flow, idea


def _sql(sql, params=()):
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        return conn.execute(sql, params).fetchone() if "RETURNING" in sql else conn.execute(sql, params)


def _trade(client, h, ticker, side, quantity, fill, at, **extra):
    r = client.post("/trades", json={"book": "real", "ticker": ticker, "side": side, "quantity": str(quantity),
                                     "fill": str(fill), "at": at.isoformat(), **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _paper(idea_id, ticker, excess):
    _sql("INSERT INTO paper_results (idea_id, ticker, lane, module, opened_on, closes_on, status, excess_pct) "
         "VALUES (%s, %s, 'swing', 'vcp', CURRENT_DATE, CURRENT_DATE, 'settled', %s)", (idea_id, ticker, excess))


def test_each_suggestion_gets_its_outcome_and_its_results(client):
    h = _h(client)
    now = dt.datetime.now(dt.UTC)
    flow(client, h, "real", 10000, now.date())
    took, said_no, lapsed, refused, still_open = (idea(t) for t in ("AAA", "BBB", "CCC", "DDD", "EEE"))
    _sql("UPDATE ideas SET expires_at = now() - interval '1 day' WHERE id = %s", (lapsed,))
    _sql("UPDATE ideas SET status = 'refused' WHERE id = %s", (refused,))
    _sql("INSERT INTO memos (ticker, call) VALUES ('MMM', 'buy')")
    _trade(client, h, "AAA", "buy", 10, 100, now - dt.timedelta(minutes=2), idea_id=took)
    _trade(client, h, "AAA", "sell", 10, 110, now - dt.timedelta(minutes=1))      # a sale by hand: still the idea's
    _trade(client, h, "ZZZ", "buy", 5, 20, now - dt.timedelta(minutes=1))          # no idea, no memo: an own call
    r = client.post("/decisions", json={"kind": "idea", "ref_id": said_no, "reason": "  Too close to earnings "},
                    headers=h)
    assert r.status_code == 201 and r.json()["reason"] == "Too close to earnings"
    _paper(took, "AAA", "-1.00")
    _paper(said_no, "BBB", "2.50")

    rep = client.get("/reconciliation", headers=h).json()
    by = {s["ticker"]: s for s in rep["suggestions"]}
    assert set(by) == {"AAA", "BBB", "CCC", "EEE", "MMM"}                          # the refused idea is no suggestion
    assert [by[t]["outcome"] for t in ("AAA", "BBB", "CCC", "EEE", "MMM")] == ["took", "declined", "lapsed", "open",
                                                                                 "open"]
    assert by["AAA"]["taken"]["quantity"] == "10.000000" and by["AAA"]["realized"] == "100.00"
    assert by["BBB"]["reason"] == "Too close to earnings" and by["BBB"]["paper"]["excess_pct"] == "2.50"
    assert by["MMM"]["kind"] == "memo" and by["MMM"]["label"] == "buy call"
    assert [o["ticker"] for o in rep["own_calls"]] == ["ZZZ"]
    s = rep["summary"]
    assert s["took"] == {"n": 1, "paper_settled": 1, "paper_excess_avg": "-1.00", "realized": "100.00"}
    assert s["declined"]["paper_excess_avg"] == "2.50"                              # what saying no avoided or missed
    assert (s["lapsed"]["n"], s["open"]["n"], s["own_calls"]["n"]) == (1, 2, 1)
    assert rep["broker"] is None


def test_a_dropped_to_do_is_a_no_and_a_no_can_be_undone(client):
    h = _h(client)
    a, b = idea("AAA"), idea("BBB")
    _sql("INSERT INTO todos (source, ticker, side, quantity, idea_id, status) VALUES ('invest', 'AAA', 'buy', 1, %s, "
         "'dropped')", (a,))
    client.post("/decisions", json={"kind": "idea", "ref_id": b}, headers=h)
    out = {s["ticker"]: s for s in client.get("/reconciliation", headers=h).json()["suggestions"]}
    assert out["AAA"]["outcome"] == "declined" and out["AAA"]["reason"].startswith("Dropped")
    assert out["BBB"]["outcome"] == "declined" and out["BBB"]["reason"] is None
    assert client.delete(f"/decisions/idea/{b}", headers=h).status_code == 200
    out = {s["ticker"]: s for s in client.get("/reconciliation", headers=h).json()["suggestions"]}
    assert out["BBB"]["outcome"] == "open"
    assert client.delete(f"/decisions/idea/{b}", headers=h).status_code == 404
    assert client.post("/decisions", json={"kind": "idea", "ref_id": 999999}, headers=h).status_code == 404
    assert client.post("/decisions", json={"kind": "trade", "ref_id": a}, headers=h).status_code == 422
    assert client.get("/reconciliation?month=2026-13", headers=h).status_code == 422
    assert client.get("/reconciliation", headers={}).status_code == 401


def test_the_broker_check_compares_shares_and_cash_on_the_statements_day(client):
    h = _h(client)
    day = dt.date(2026, 10, 15)
    flow(client, h, "real", 5000, day)
    _trade(client, h, "AAPL", "buy", 10, 200, dt.datetime(2026, 10, 15, 17, tzinfo=dt.UTC))
    _trade(client, h, "MSFT", "buy", 3, 400, dt.datetime(2026, 10, 20, 17, tzinfo=dt.UTC))   # after the statement
    r = client.post("/broker-statements", json={"as_of": "2026-10-15", "cash": "3000.00",
                                                "positions": [{"ticker": "aapl", "quantity": "10"}]}, headers=h)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["matches"] is True and out["cash"] == {"app": "3000.00", "broker": "3000.00", "difference": "0.00",
                                                      "match": True}
    assert [x["ticker"] for x in out["shares"]] == ["AAPL"]                          # MSFT came after that day
    r = client.post("/broker-statements", json={"as_of": "2026-10-21", "cash": "1700.00",
                                                "positions": [{"ticker": "AAPL", "quantity": "10"},
                                                              {"ticker": "NVDA", "quantity": "2"}]}, headers=h)
    out = r.json()
    lines = {x["ticker"]: x for x in out["shares"]}
    assert lines["MSFT"] == {"ticker": "MSFT", "app": "3", "broker": "0", "difference": "-3", "match": False}
    assert lines["NVDA"]["difference"] == "2" and out["cash"]["difference"] == "-100.00" and not out["matches"]
    assert client.get("/reconciliation", headers=h).json()["broker"]["as_of"] == "2026-10-21"   # the latest one
    future = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    assert client.post("/broker-statements", json={"as_of": future, "cash": "1"}, headers=h).status_code == 422


def test_the_reminder_goes_on_the_months_first_trading_day_only(fresh_db):
    db = Database(settings())
    cal = Calendar()                                     # no stored calendar: weekdays
    assert reconcile.first_session_of_month(cal, dt.date(2026, 11, 20)) == dt.date(2026, 11, 2)   # Nov 1 is a Sunday
    ny = dt.timezone(dt.timedelta(hours=-5))
    assert reconcile.remind(db, settings(), dt.datetime(2026, 11, 3, 9, tzinfo=ny))["skipped"]
    sent = reconcile.remind(db, settings(), dt.datetime(2026, 11, 2, 9, tzinfo=ny))
    assert "skipped" not in sent and sent["devices"] == 0                          # no device registered in the test
    db.close()


def test_a_month_is_read_in_pacific_time():
    start, end, label = reconcile.month_bounds("2026-12")
    assert (label, start.month, end.year, end.month) == ("2026-12", 12, 2027, 1)
    assert reconcile.month_bounds(None, dt.date(2026, 11, 5))[2] == "2026-11"


def test_a_to_do_closed_with_a_real_trade_is_taken_even_without_the_ideas_number(client):
    h = _h(client)
    a = idea("AAA")
    trade = _trade(client, h, "AAA", "buy", 2, 100, dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1))
    _sql("INSERT INTO todos (source, ticker, side, quantity, idea_id, status, trade_id) VALUES ('invest', 'AAA', 'buy', "
         "2, %s, 'done', %s)", (a, trade["id"]))
    (row,) = client.get("/reconciliation", headers=h).json()["suggestions"]
    assert row["outcome"] == "took" and row["taken"]["trade_id"] == trade["id"]
    assert client.get("/reconciliation", headers=h).json()["own_calls"] == []      # not an own call as well

