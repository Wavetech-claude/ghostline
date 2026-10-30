"""The scan writes signals and turns the actionable ones into ideas sized to the balance, through the risk rules; a
refused idea stays visible with its reason. The routes serve the lanes."""
import datetime as dt
from decimal import Decimal

from ghostline.ideas import Scan
from ghostline.modules import Signal
from tests.conftest import sign_in
from tests.test_practice import _sql, bars, deposit, earnings, quote, set_rules

D = Decimal


def _h(client):
    return {"Authorization": f"Bearer {sign_in(client)}"}


def swing_signal(ticker="AAPL", entry=100.0, stop=95.0, score=80.0, actionable=True) -> Signal:
    return Signal(ticker, "vcp", "swing", "long", score, 40, f"{ticker}: a tight base", entry=entry, stop=stop,
                  target=entry * 1.2, actionable=actionable, evidence={"state": "pre_breakout"}, source="test",
                  as_of=dt.datetime.now(dt.UTC))


def test_a_signal_becomes_a_sized_idea_through_the_rules(client):
    h = _h(client)
    db = client.app.state.db
    deposit(client, h, 10000)
    quote("AAPL", "99.95", "100.05")
    bars("AAPL")
    earnings("AAPL", 40)
    set_rules(client, h)
    scan = Scan(db)
    sid = scan.save(swing_signal())
    made = scan.idea(sid, swing_signal())
    assert made["status"] == "open", made
    row = db.fetch_one("SELECT * FROM ideas WHERE id = %s", (made["id"],))
    # 1.5% of 10,000 = 150 at risk, 5 per share → 30 shares (3,000, under the 20% cap)
    assert row["quantity"] == D("30") and row["max_loss"] == D("150.00") and row["size_share"] == D("0.3000") or row["size_share"] == D("0.3")
    assert row["book"] == "practice" and row["balance"] == D("10000.00") and row["risk_decision_id"]
    # the same signal again is the same open idea, not a second one
    assert scan.idea(sid, swing_signal())["existing"] is True
    r = client.get("/ideas?lane=swing", headers=h).json()
    assert len(r["ideas"]) == 1 and r["ideas"][0]["status"] == "open" and r["ideas"][0]["score"] == "80.00"
    one = client.get(f"/ideas/{made['id']}", headers=h).json()
    assert one["signal"]["module"] == "vcp" and one["decision"]["result"] == "pass"
    assert client.get("/ideas/999", headers=h).status_code == 404


def test_the_size_is_capped_at_a_fifth_of_the_balance(client):
    h = _h(client)
    db = client.app.state.db
    deposit(client, h, 10000)
    quote("AAPL", "99.95", "100.05")
    bars("AAPL")
    earnings("AAPL", 40)
    set_rules(client, h)
    scan = Scan(db)
    s = swing_signal(entry=100.0, stop=99.5)                     # 0.50 per share → 300 shares would be 30,000
    made = scan.idea(scan.save(s), s)
    row = db.fetch_one("SELECT quantity, max_loss FROM ideas WHERE id = %s", (made["id"],))
    assert row["quantity"] == D("20") and row["max_loss"] == D("10.00")


def test_a_refused_idea_keeps_its_reason(client):
    h = _h(client)
    db = client.app.state.db
    deposit(client, h, 10000)
    quote("MSFT", "49.95", "50.05")
    bars("MSFT")
    set_rules(client, h)                                           # no earnings stored for MSFT → unknown → refused
    scan = Scan(db)
    s = swing_signal("MSFT", 50.0, 48.0)
    made = scan.idea(scan.save(s), s)
    assert made["status"] == "refused" and made["refusal"].startswith("earnings_blackout_days:")
    r = client.get("/ideas", headers=h).json()
    assert r["ideas"][0]["status"] == "refused" and "unknown" in r["ideas"][0]["refusal"]
    assert {c["status"] for c in r["counts"]} == {"refused"}


def test_a_put_idea_counts_contracts_from_the_available_cash(client):
    h = _h(client)
    db = client.app.state.db
    deposit(client, h, 25000)
    quote("X", "99.95", "100.05")
    bars("X")
    earnings("X", 60)
    _sql("INSERT INTO option_chains (option_symbol, underlying, expiry, strike, kind, bid, ask, open_interest, delta, source, as_of) "
         "VALUES ('X261204P00095000', 'X', %s, 95, 'put', 2.2, 2.3, 1500, -0.3, 'tradier', now())", (dt.date.today() + dt.timedelta(days=35),))
    set_rules(client, h, max_loss_per_idea_share="0.1")
    scan = Scan(db)
    s = Signal("X", "sell_puts", "puts", "short_put", 75, 35, "X: sell the put", entry=2.2, stop=85.5, target=0, actionable=True,
               evidence={"best": {"open_interest": 1500, "spread_share": 0.044}}, source="tradier", as_of=dt.datetime.now(dt.UTC),
               option_symbol="X261204P00095000")
    made = scan.idea(scan.save(s), s)
    assert made["status"] == "open", made
    row = db.fetch_one("SELECT * FROM ideas WHERE id = %s", (made["id"],))
    assert row["quantity"] == D("2") and row["collateral"] == D("19000.00") and row["lane"] == "puts"


def test_the_scan_runs_on_what_is_stored_and_the_routes_answer(client):
    h = _h(client)
    deposit(client, h, 1000)
    bars("AAPL", n=30)
    client.post("/watchlist", json={"ticker": "AAPL"}, headers=h)
    r = client.post("/ideas/scan", headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["tickers"] == 1 and out["signals"] >= 3 and out["ideas"] == 0
    sig = client.get("/signals?ticker=AAPL", headers=h).json()["signals"]
    assert {s["module"] for s in sig} >= {"vcp", "canslim"}
    assert all("fewer than 260" in s["reason"] for s in sig if s["module"] in ("vcp", "canslim"))
    regime = client.get("/market/regime", headers=h).json()["regime"]
    assert regime["module"] == "macro_regime" and regime["evidence"]["regime"] == "unclear"
    assert client.get("/ideas", headers=h).json()["last_scan"]["signals"] >= 3
    assert client.get("/ideas").status_code == 401
