"""Phase 6: the ghost, value over time, the AI trader and its copies, Invest this and the to-do list, options practice,
price alerts and statements."""
import datetime as dt
import re
from decimal import Decimal

import pytest

from ghostline import alerts, series, statements
from ghostline.ai_trader import AITrader
from ghostline.ghost import Ghost
from ghostline.invest import Planner, default_date
from ghostline.practice import Practice
from tests.conftest import settings as make_settings
from tests.conftest import sign_in
from tests.test_practice import _sql, bars, chain_row, deposit, earnings, quote, set_rules

D = Decimal


def _h(client):
    return {"Authorization": f"Bearer {sign_in(client)}"}


def bar(ticker, day, close, volume=1_000_000):
    ts = dt.datetime.combine(day, dt.time(16, 0), tzinfo=dt.UTC)
    _sql("INSERT INTO market_bars (ticker, timeframe, ts, open, high, low, close, volume, source, as_of) VALUES "
         "(%s, '1D', %s, %s, %s, %s, %s, %s, 'alpaca', now()) ON CONFLICT (ticker, timeframe, ts) DO UPDATE SET close = EXCLUDED.close",
         (ticker, ts, close, close, close, close, volume))


def flow(client, h, book, amount, day):
    r = client.post("/cash-flows", json={"book": book, "amount": str(amount), "at": f"{day.isoformat()}T17:00:00Z"}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def idea(ticker, lane="swing", module="vcp", entry=100, stop=95, target=120, score=80, horizon=40, option_symbol=None):
    """An open idea, as the scan would leave it."""
    import psycopg

    from tests.conftest import TEST_DB
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        s = conn.execute(
            "INSERT INTO signals (ticker, module, lane, direction, score, actionable, horizon_days, entry, stop, target, reason, day, source, as_of) "
            "VALUES (%s, %s, %s, 'long', %s, true, %s, %s, %s, %s, %s, CURRENT_DATE, 'test', now()) RETURNING id",
            (ticker, module, lane, score, horizon, entry, stop, target, f"{ticker}: a test idea")).fetchone()
        i = conn.execute(
            "INSERT INTO ideas (lane, ticker, signal_id, module, status, book, balance, entry, stop, target, option_symbol, expires_at) "
            "VALUES (%s, %s, %s, %s, 'open', 'practice', 0, %s, %s, %s, %s, now() + interval '30 days') RETURNING id",
            (lane, ticker, s[0], module, entry, stop, target, option_symbol)).fetchone()
    return i[0]


def ready(ticker, bid="99.95", ask="100.05", last="100.00"):
    quote(ticker, bid, ask, last)
    bars(ticker)
    earnings(ticker, 40)


# ── the ghost ───────────────────────────────────────────────────────────────────────────────────────────────
def test_the_ghost_mirrors_ai_flows_with_spy_at_that_days_close_and_waits_without_a_price(client):
    h = _h(client)
    out = flow(client, h, "ai", 1000, dt.date(2026, 9, 2))        # no SPY bar stored yet
    assert out["ghost"] == {"mirrored": 0, "waiting": 1}
    bar("SPY", dt.date(2026, 9, 2), 400)
    bar("SPY", dt.date(2026, 9, 4), 410)                            # Friday
    assert Ghost(client.app.state.db).sync() == {"mirrored": 1, "waiting": 0}
    out = flow(client, h, "ai", 820, dt.date(2026, 9, 6))           # Sunday: Friday's close
    assert out["ghost"] == {"mirrored": 1, "waiting": 0}
    out = flow(client, h, "ai", -205, dt.date(2026, 9, 6))          # a withdrawal sells
    db = client.app.state.db
    rows = db.fetch_all("SELECT t.side, t.quantity, t.fill FROM trades t JOIN books b ON b.id = t.book_id "
                        "WHERE b.kind = 'ghost' ORDER BY t.id")
    assert [(r["side"], r["quantity"], r["fill"]) for r in rows] == [
        ("buy", D("2.500000"), D("400.0000")), ("buy", D("2.000000"), D("410.0000")), ("sell", D("0.500000"), D("410.0000"))]
    ghost = next(b for b in client.get("/books", headers=h).json()["books"] if b["kind"] == "ghost")
    assert ghost["deposits"] == "1820.00" and ghost["withdrawals"] == "205.00"
    # the ghost is the system's: never typed in
    assert client.post("/cash-flows", json={"book": "ghost", "amount": "5", "at": "2026-09-02T00:00:00Z"}, headers=h).status_code == 422
    assert client.post("/trades", json={"book": "ghost", "ticker": "SPY", "side": "buy", "quantity": "1", "fill": "1",
                                        "at": "2026-09-02T00:00:00Z"}, headers=h).status_code == 422


def test_time_weighted_return_ignores_deposits(client):
    h = _h(client)
    for d, c in ((dt.date(2026, 9, 1), 400), (dt.date(2026, 9, 2), 440), (dt.date(2026, 9, 3), 440), (dt.date(2026, 9, 4), 400)):
        bar("SPY", d, c)
    flow(client, h, "ai", 1000, dt.date(2026, 9, 1))
    flow(client, h, "ai", 1100, dt.date(2026, 9, 3))
    s = series.equity_series(client.app.state.db, "ghost", end=dt.date(2026, 9, 4))
    assert [(p["date"], p["value"], p["twr_pct"]) for p in s["points"]] == [
        ("2026-09-01", "1000.00", "0.00"), ("2026-09-02", "1100.00", "10.00"),
        ("2026-09-03", "2200.00", "10.00"), ("2026-09-04", "2000.00", "0.00")]
    assert series.equity_series(client.app.state.db, "real")["points"] == []


# ── the AI trader ───────────────────────────────────────────────────────────────────────────────────────────
def test_the_ai_trader_waits_for_money_and_rules_then_sizes_from_the_rules(client):
    h = _h(client)
    db = client.app.state.db
    ready("AAPL")
    iid = idea("AAPL")
    t = AITrader(db)
    assert "no money" in t.run_entries()["skipped"]
    deposit(client, h, 10000, book="ai")
    assert "risk rules not set" in t.run_entries()["skipped"]
    set_rules(client, h)
    out = t.run_entries()
    assert len(out["placed"]) == 1 and out["placed"][0]["status"] == "filled", out
    tr = db.fetch_one("SELECT t.* FROM trades t JOIN books b ON b.id = t.book_id WHERE b.kind = 'ai'")
    # 1.5% of 10,000 = 150 at risk over a 5.00 stop distance = 30 shares, capped at a fifth of the book = 20
    assert tr["quantity"] == D("19.990005") or tr["quantity"] == D("20.000000")
    assert tr["idea_id"] == iid and tr["lane"] == "swing" and f"idea #{iid}" in tr["note"] and "a test idea" in tr["note"]
    assert t.run_entries()["placed"] == []                          # the same idea is not taken twice
    r = client.get("/ai-trader", headers=h).json()
    assert r["state"]["open_positions"] == 1 and r["trades"][0]["ticker"] == "AAPL" and r["trades"][0]["module"] == "vcp"
    assert r["trades"][0]["copy_real_allowed"] is False and "has not graduated" in r["trades"][0]["copy_real_reason"]
    assert {lane["lane"] for lane in r["lanes"]} == {"long", "swing", "puts"}


def test_the_ai_trader_exits_at_the_stop_and_after_the_horizon(client):
    h = _h(client)
    db = client.app.state.db
    ready("AAPL")
    ready("MSFT")
    idea("AAPL")
    idea("MSFT", horizon=10)
    deposit(client, h, 10000, book="ai")
    set_rules(client, h)
    t = AITrader(db)
    assert len(t.run_entries()["placed"]) == 2
    assert t.run_exits()["sold"] == []
    quote("AAPL", "94.00", "94.20", "94.10")                       # under the 95 stop
    _sql("UPDATE trades SET at = now() - interval '11 days' WHERE ticker = 'MSFT'")
    out = t.run_exits()
    whys = {s["ticker"]: s["why"] for s in out["sold"]}
    assert whys["AAPL"].startswith("stop 95") and "10-day horizon passed" in whys["MSFT"]
    assert all(s["status"] == "filled" for s in out["sold"])
    assert client.get("/ai-trader", headers=h).json()["state"]["open_positions"] == 0
    assert t.run_exits()["sold"] == []


def test_copy_to_practice_at_the_same_share_and_to_real_only_after_the_lane_graduates(client):
    h = _h(client)
    db = client.app.state.db
    ready("AAPL")
    idea("AAPL")
    deposit(client, h, 10000, book="ai")
    deposit(client, h, 5000, book="practice")
    set_rules(client, h)
    AITrader(db).run_entries()
    tid = db.fetch_one("SELECT t.id FROM trades t JOIN books b ON b.id = t.book_id WHERE b.kind = 'ai'")["id"]
    r = client.post(f"/ai-trader/trades/{tid}/copy", json={"to": "practice"}, headers=h)
    assert r.status_code == 201, r.text
    order = r.json()["order"]
    # the AI trade is 2,001.00 of a 9,999.00 book (20.012%); the same share of 5,000 at 100.00 is 10.006001 shares
    assert order["status"] == "filled" and D(order["quantity"]) == D("10.006001")
    refused = client.post(f"/ai-trader/trades/{tid}/copy", json={"to": "real"}, headers=h)
    assert refused.status_code == 409 and "has not graduated" in refused.json()["detail"]
    _sql("UPDATE lane_graduation SET graduated = true WHERE lane = 'swing'")
    deposit(client, h, 2000, book="real")
    ok = client.post(f"/ai-trader/trades/{tid}/copy", json={"to": "real"}, headers=h).json()
    assert ok["to"] == "real" and D(ok["quantity"]) == D("4.002400")      # the same 20.012% of 2,000
    todo = client.get("/todos", headers=h).json()["todos"][0]
    assert todo["source"] == "ai_copy" and todo["ticker"] == "AAPL" and todo["lane"] == "swing"
    assert client.post("/ai-trader/trades/999/copy", json={"to": "practice"}, headers=h).status_code == 409


def test_a_simulated_order_never_reaches_the_real_book(client):
    with pytest.raises(ValueError):
        Practice(client.app.state.db).place({"book": "real", "ticker": "AAPL", "asset": "stock", "side": "buy",
                                             "quantity": 1}, by="test")


# ── Invest this ─────────────────────────────────────────────────────────────────────────────────────────────
def test_invest_this_splits_by_score_moves_refused_money_and_places_on_its_date(client):
    h = _h(client)
    db = client.app.state.db
    ready("AAPL")
    quote("MSFT", "99.95", "100.05", "100.00")
    bars("MSFT")                                                     # no earnings stored for MSFT: refused
    idea("AAPL", stop=99, score=80)
    idea("MSFT", stop=99, score=60)
    set_rules(client, h)
    r = client.post("/invest", json={"amount": "50"}, headers=h)
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["for_date"] == default_date().isoformat() and plan["status"] == "proposed" and plan["book"] == "practice"
    by = {s["ticker"]: s for s in plan["splits"]}
    assert by["MSFT"]["decision"] == "refuse" and by["MSFT"]["dollars"] == "0.00" and "moved to the ideas that pass" in by["MSFT"]["why"]
    assert by["AAPL"]["decision"] == "pass" and by["AAPL"]["dollars"] == "50.00" and by["AAPL"]["shares"] == "0.49975"
    assert plan["unallocated"] == "0.00"
    a = client.post(f"/invest/{plan['id']}/approve", json={"to": "practice"}, headers=h).json()
    assert a["status"] == "approved"                                # tomorrow: not placed yet
    assert client.get("/practice/account", headers=h).json()["cash"] == "50.00"
    assert Planner(db).place_due() == {"placed": []}
    _sql("UPDATE invest_plans SET for_date = CURRENT_DATE - 1 WHERE id = %s", (plan["id"],))
    placed = Planner(db).place_due()["placed"][0]["orders"]
    assert placed[0]["ticker"] == "AAPL" and placed[0]["status"] == "filled"
    assert client.get("/invest", headers=h).json()["plans"][0]["status"] == "placed"
    assert client.post(f"/invest/{plan['id']}/approve", json={"to": "practice"}, headers=h).status_code == 409


def test_invest_this_for_real_money_makes_to_do_items_closed_by_a_real_trade(client):
    h = _h(client)
    ready("AAPL")
    idea("AAPL", stop=99)
    set_rules(client, h)
    deposit(client, h, 1000, book="real")
    plan = client.post("/invest", json={"amount": "40", "book": "real"}, headers=h).json()
    out = client.post(f"/invest/{plan['id']}/approve", json={"to": "real"}, headers=h).json()
    assert out["status"] == "placed" and len(out["todos"]) == 1
    todo = client.get("/todos", headers=h).json()["todos"][0]
    assert todo["side"] == "buy" and todo["ticker"] == "AAPL" and D(todo["quantity"]) == D("0.3998")
    practice_trade = client.post("/trades", json={"book": "practice", "ticker": "AAPL", "side": "buy", "quantity": "1", "fill": "100",
                                                  "at": "2026-10-03T14:30:00Z"}, headers=h).json()
    assert client.post(f"/todos/{todo['id']}/close", json={"trade_id": practice_trade["id"]}, headers=h).status_code == 422
    real = client.post("/trades", json={"book": "real", "ticker": "AAPL", "side": "buy", "quantity": "0.3998", "fill": "100.05",
                                        "at": "2026-10-03T14:30:00Z"}, headers=h).json()
    done = client.post(f"/todos/{todo['id']}/close", json={"trade_id": real["id"]}, headers=h).json()
    assert done["status"] == "done" and done["trade_id"] == real["id"]
    assert client.get("/todos", headers=h).json()["todos"] == []
    assert client.post(f"/todos/{todo['id']}/close", json={}, headers=h).status_code == 409


def test_invest_this_with_no_ideas_says_so(client):
    h = _h(client)
    plan = client.post("/invest", json={"amount": "50"}, headers=h).json()
    assert plan["splits"] == [] and plan["unallocated"] == "50.00" and "no open idea" in plan["note"]
    assert client.post("/invest", json={"amount": "50", "for_date": "2020-01-01"}, headers=h).status_code == 422
    assert client.post(f"/invest/{plan['id']}/drop", headers=h).json() == {"ok": True}


# ── options practice ────────────────────────────────────────────────────────────────────────────────────────
def test_options_practice_sells_the_put_ideas_the_amount_can_cover(client):
    h = _h(client)
    db = client.app.state.db
    exp = dt.date.today() + dt.timedelta(days=35)
    small = f"X{exp:%y%m%d}P00010000"
    big = f"Y{exp:%y%m%d}P00200000"
    ready("X", "10.95", "11.05", "11.00")
    ready("Y", "219.95", "220.05", "220.00")
    quote(small, "0.50", "0.60")
    chain_row(small, 900, "0.50", "0.60")
    idea("X", lane="puts", module="sell_puts", entry=0.5, stop=9, target=0, option_symbol=small)
    idea("Y", lane="puts", module="sell_puts", entry=3, stop=180, target=0, option_symbol=big)
    set_rules(client, h, max_loss_per_idea_share="1")
    deposit(client, h, 1500, book="options")
    out = client.post("/options-practice/run", headers=h).json()["entries"]
    assert [p["ticker"] for p in out["placed"]] == ["X"] and out["placed"][0]["status"] == "filled"
    assert "collateral" in out["skipped"][0]["why"] and out["skipped"][0]["ticker"] == "Y"
    st = client.get("/options-practice", headers=h).json()
    assert st["state"]["short_puts"] == 1 and st["state"]["locked_collateral"] == "1000.00"
    assert st["trades"][0]["option_symbol"] == small and st["trades"][0]["lane"] == "puts"
    # expiry runs over the options book too
    quote("X", "11.9", "12.1", "12.0")
    assert Practice(db).expire_options(exp)["expired"] == 1


# ── alerts ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_alert_rules():
    a = lambda kind, value: {"ticker": "T", "kind": kind, "value": D(str(value))}  # noqa: E731
    assert alerts.evaluate(a("above", 100), D("100.5"), [], None).startswith("T is at 100.5, at or above")
    assert alerts.evaluate(a("above", 100), D("99"), [], None) is None
    assert alerts.evaluate(a("below", 100), D("99"), [], None) is not None
    assert "moved -5.00%" in alerts.evaluate(a("pct_move", 5), D("95"), [100.0], None)
    assert alerts.evaluate(a("pct_move", 5), D("97"), [100.0], None) is None
    closes = [100.0] * 20 + [98.0]                                  # under its 20-day average
    assert "crossed above its 20-day average" in alerts.evaluate(a("ma_cross", 20), D("105"), closes, None)
    assert alerts.evaluate(a("ma_cross", 20), D("97"), closes, None) is None
    assert alerts.evaluate(a("earnings", 5), None, [], 3) == "T reports earnings in 3 days"
    assert alerts.evaluate(a("earnings", 5), None, [], 9) is None
    assert alerts.evaluate(a("above", 1), None, [], None) is None


def test_alerts_fire_once_push_and_can_be_turned_back_on(client):
    h = _h(client)
    db = client.app.state.db
    assert client.post("/alerts", json={"ticker": "aapl", "kind": "ma_cross", "value": "2.5"}, headers=h).status_code == 422
    assert client.post("/alerts", json={"ticker": "aapl", "kind": "earnings", "value": "90"}, headers=h).status_code == 422
    a = client.post("/alerts", json={"ticker": "aapl", "kind": "above", "value": "100"}, headers=h).json()
    assert a["ticker"] == "AAPL" and a["status"] == "active"
    from ghostline.market import store
    assert "AAPL" in store.tracked_tickers(db)                     # an alert's ticker gets quotes and bars
    pushes = []
    pusher = lambda db_, s, title, body, data: pushes.append((title, body)) or {"sent": 0}  # noqa: E731
    assert alerts.check(db, make_settings(), pusher)["fired"] == []  # no price yet
    quote("AAPL", "100.10", "100.30", "100.20")
    out = alerts.check(db, make_settings(), pusher)
    assert len(out["fired"]) == 1 and pushes == [("AAPL alert", "AAPL is at 100.2000, at or above 100.0000")]
    assert alerts.check(db, make_settings(), pusher)["fired"] == []
    got = client.get("/alerts?ticker=AAPL", headers=h).json()["alerts"][0]
    assert got["status"] == "triggered" and got["message"].startswith("AAPL is at")
    assert client.put(f"/alerts/{a['id']}", json={"active": True}, headers=h).json()["status"] == "active"
    assert client.delete(f"/alerts/{a['id']}", headers=h).json() == {"ok": True}
    assert client.delete(f"/alerts/{a['id']}", headers=h).status_code == 404


# ── statements ──────────────────────────────────────────────────────────────────────────────────────────────
def _pdf_is_sound(data: bytes) -> int:
    """Header, a startxref that points at the xref table, every xref offset at its object; returns the page count."""
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    start = int(re.search(rb"startxref\n(\d+)\n", data).group(1))
    assert data[start:start + 4] == b"xref"
    entries = re.findall(rb"(\d{10}) 00000 n ", data[start:])
    for n, off in enumerate(entries, 1):
        assert data[int(off):].startswith(f"{n} 0 obj".encode()), n
    count = int(re.search(rb"/Count (\d+)", data).group(1))
    assert data.count(b"/Type /Page ") == count
    return count


def test_a_monthly_statement_in_json_csv_and_pdf(client):
    h = _h(client)
    flow(client, h, "practice", 1000, dt.date(2026, 10, 1))
    for side, price, day in (("buy", "100", "03"), ("sell", "110", "05")):
        assert client.post("/trades", json={"book": "practice", "ticker": "AAPL", "side": side, "quantity": "10", "fill": price,
                                            "fees": "1", "at": f"2026-10-{day}T14:30:00Z"}, headers=h).status_code == 201
    s = client.get("/statements?book=practice&month=2026-10", headers=h).json()
    assert (s["deposits"], s["fees"], s["realized_pnl"], s["cash_at_end"]) == ("1000.00", "2.00", "98.00", "1098.00")
    assert s["dividends"] is None and "not tracked" in s["dividends_note"] and len(s["trades"]) == 2
    sept = client.get("/statements?book=practice&month=2026-09", headers=h).json()
    assert sept["trades"] == [] and sept["realized_pnl"] == "0.00" and sept["cash_at_end"] == "0.00"
    csv_r = client.get("/statements?book=practice&month=2026-10&format=csv", headers=h)
    assert csv_r.headers["content-type"].startswith("text/csv") and "realized_pnl,98.00" in csv_r.text
    assert 'filename="ghostline-practice-2026-10.csv"' in csv_r.headers["content-disposition"]
    pdf = client.get("/statements?book=practice&month=2026-10&format=pdf", headers=h)
    assert pdf.headers["content-type"] == "application/pdf" and _pdf_is_sound(pdf.content) == 1
    assert client.get("/statements?book=practice&month=2026-13", headers=h).status_code == 422


def test_a_long_statement_pdf_runs_over_several_pages():
    s = {"name": "Practice (QA)", "month": "2026-10", "deposits": "1", "withdrawals": "0", "fees": "0", "realized_pnl": "0",
         "cash_at_end": "1", "dividends_note": "dividends are not tracked yet", "flows": [],
         "trades": [{"at": "2026-10-01T00:00:00", "ticker": "AAPL", "asset": "stock", "side": "buy", "quantity": "1",
                     "fill": "100", "fees": "0", "option_symbol": None, "note": "(brackets) and \\ slashes"}] * 90}
    assert _pdf_is_sound(statements.to_pdf(s)) >= 2


def test_the_worker_registers_the_phase_six_jobs(client):
    from apscheduler.schedulers.background import BackgroundScheduler

    from ghostline.worker import schedule
    ids = schedule.register(BackgroundScheduler(timezone="America/Los_Angeles"), client.app.state.db, settings=make_settings())
    assert {"ghost_sync", "invest_place", "ai_entries", "options_entries", "ai_exits", "alerts_check"} <= set(ids)
