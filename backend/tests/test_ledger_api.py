"""Books, cash flows, trades, history, portfolio, attachments and the risk rules, through the API."""
import io
from decimal import Decimal

from tests.conftest import sign_in


def _h(client):
    return {"Authorization": f"Bearer {sign_in(client)}"}


def test_the_four_books_exist_and_cash_follows_flows_and_trades(client):
    h = _h(client)
    assert [b["kind"] for b in client.get("/books", headers=h).json()["books"]] == ["real", "practice", "ai", "ghost", "options"]
    assert client.post("/cash-flows", json={"book": "real", "amount": "250.5", "at": "2026-10-01T00:00:00Z"}, headers=h).status_code == 201
    assert client.post("/cash-flows", json={"book": "real", "amount": "-50", "at": "2026-10-02T00:00:00Z", "note": "fee"}, headers=h).status_code == 201
    assert client.post("/cash-flows", json={"book": "real", "amount": "0", "at": "2026-10-02T00:00:00Z"}, headers=h).status_code == 422
    r = client.post("/trades", json={"book": "real", "ticker": "aapl", "side": "buy", "quantity": "0.5", "fill": "200",
                                     "at": "2026-10-03T14:30:00Z", "fees": "1", "broker": "Robinhood"}, headers=h)
    assert r.status_code == 201 and r.json()["ticker"] == "AAPL" and r.json()["book"] == "real" and r.json()["attachments"] == 0
    real = next(b for b in client.get("/books", headers=h).json()["books"] if b["kind"] == "real")
    assert real == {**real, "deposits": "250.50", "withdrawals": "50.00", "cash": "99.50", "trades": 1}
    flows = client.get("/cash-flows?book=real", headers=h).json()["cash_flows"]
    assert [f["amount"] for f in flows] == ["-50.00", "250.50"]


def test_trades_are_validated_and_filtered(client):
    h = _h(client)
    assert client.post("/trades", json={"book": "real", "ticker": "X", "side": "sell_to_open", "quantity": "1", "fill": "1",
                                        "at": "2026-10-03T14:30:00Z"}, headers=h).status_code == 422   # a stock cannot sell_to_open
    assert client.post("/trades", json={"book": "real", "ticker": "X", "asset": "option", "side": "sell_to_open",
                                        "quantity": "1", "fill": "1", "at": "2026-10-03T14:30:00Z"}, headers=h).status_code == 422  # no symbol
    ok = client.post("/trades", json={"book": "practice", "ticker": "X", "asset": "option", "side": "sell_to_open",
                                      "quantity": "1", "fill": "1.2", "at": "2026-10-03T14:30:00Z",
                                      "option_symbol": "X261121P00010000", "lane": "puts"}, headers=h)
    assert ok.status_code == 201
    assert client.post("/trades", json={"book": "real", "ticker": "Y", "side": "buy", "quantity": "-1", "fill": "1",
                                        "at": "2026-10-03T14:30:00Z"}, headers=h).status_code == 422
    client.post("/trades", json={"book": "real", "ticker": "Y", "side": "buy", "quantity": "2", "fill": "10",
                                 "at": "2026-10-04T14:30:00Z", "lane": "swing"}, headers=h)
    assert len(client.get("/trades?book=practice", headers=h).json()["trades"]) == 1
    assert len(client.get("/trades?lane=swing", headers=h).json()["trades"]) == 1
    assert len(client.get("/trades?asset=option", headers=h).json()["trades"]) == 1
    assert len(client.get("/trades?since=2026-10-04T00:00:00Z", headers=h).json()["trades"]) == 1
    tid = ok.json()["id"]
    p = client.patch(f"/trades/{tid}", json={"fees": "0.65", "note": "filled at the ask"}, headers=h)
    assert p.status_code == 200 and p.json()["fees"] == "0.65" and p.json()["note"] == "filled at the ask"
    assert client.patch(f"/trades/{tid}", json={}, headers=h).status_code == 422
    assert client.patch("/trades/999", json={"fees": "1"}, headers=h).status_code == 404
    assert client.get(f"/trades/{tid}", headers=h).json()["option_events"] == []
    assert client.get("/trades").status_code == 401


def test_history_realizes_pnl_after_fees_with_totals_and_filters(client):
    h = _h(client)
    def t(**kw):
        body = {"book": "real", "ticker": "AAPL", "side": "buy", "quantity": "10", "fill": "100", "at": "2026-10-01T14:30:00Z", "fees": "1"}
        body.update(kw)
        assert client.post("/trades", json=body, headers=h).status_code == 201
    t()
    t(side="buy", fill="110", at="2026-10-02T14:30:00Z")
    t(side="sell", quantity="15", fill="120", at="2026-10-03T14:30:00Z", fees="1.5", lane="swing")
    t(ticker="MSFT", side="buy", quantity="1", fill="300", at="2026-10-04T14:30:00Z", fees="0")
    t(ticker="MSFT", side="sell", quantity="1", fill="290", at="2026-10-05T14:30:00Z", fees="0", lane="long")
    hist = client.get("/history", headers=h).json()
    assert [(c["ticker"], c["pnl"], c["pnl_pct"]) for c in hist["closed"]] == [("AAPL", "247.00", "15.92"), ("MSFT", "-10.00", "-3.33")]
    assert hist["totals"] == {"trades": 2, "win_rate": "50.00", "average_win": "247.00", "average_loss": "-10.00", "net_pnl": "237.00"}
    (open_,) = hist["open"]
    assert open_ == {"book": "real", "ticker": "AAPL", "quantity": "5", "average_cost": "110.10", "cost_basis": "550.50",
                     "price": None, "price_source": None, "price_as_of": None, "unrealized_pnl": None}
    assert [c["ticker"] for c in client.get("/history?result=win", headers=h).json()["closed"]] == ["AAPL"]
    assert [c["ticker"] for c in client.get("/history?lane=long", headers=h).json()["closed"]] == ["MSFT"]
    assert client.get("/history?book=practice", headers=h).json() == {"closed": [], "open": [], "totals": {
        "trades": 0, "win_rate": None, "average_win": None, "average_loss": None, "net_pnl": "0.00"}}


def test_portfolio_values_cash_and_positions_and_says_when_unpriced(client):
    h = _h(client)
    client.post("/cash-flows", json={"book": "real", "amount": "1000", "at": "2026-10-01T00:00:00Z"}, headers=h)
    client.post("/trades", json={"book": "real", "ticker": "AAPL", "side": "buy", "quantity": "2", "fill": "100",
                                 "at": "2026-10-03T14:30:00Z", "fees": "1", "lane": "long"}, headers=h)
    pf = client.get("/portfolio", headers=h).json()
    real = next(b for b in pf["books"] if b["book"] == "real")
    assert real["cash"] == "799.00" and real["invested"] == "201.00" and real["total"] == "1000.00"
    assert real["priced"] is False and real["net_deposits"] == "1000.00" and real["total_pnl"] == "0.00"
    assert real["positions"][0]["price"] is None and real["positions"][0]["lane"] == "long"
    assert real["allocation"] == [{"name": "long", "share": "0.2010"}]
    assert pf["note"] and "no stored price" in pf["note"]
    ghost = next(b for b in pf["books"] if b["book"] == "ghost")
    assert ghost["total"] == "0.00" and ghost["total_pnl"] is None and ghost["priced"] is True


def test_attachments_are_stored_privately_and_read_back(client):
    h = _h(client)
    tid = client.post("/trades", json={"book": "real", "ticker": "AAPL", "side": "buy", "quantity": "1", "fill": "100",
                                       "at": "2026-10-03T14:30:00Z"}, headers=h).json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    r = client.post(f"/attachments?trade_id={tid}", files={"file": ("confirmation.png", io.BytesIO(png), "image/png")}, headers=h)
    assert r.status_code == 201 and r.json()["bytes"] == len(png) and r.json()["filename"] == "confirmation.png"
    aid = r.json()["id"]
    assert client.get(f"/trades/{tid}", headers=h).json()["attachments"] == 1
    got = client.get(f"/attachments/{aid}", headers=h)
    assert got.status_code == 200 and got.content == png and got.headers["content-type"].startswith("image/png")
    assert client.get(f"/attachments/{aid}").status_code == 401
    assert client.post(f"/attachments?trade_id={tid}", files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")}, headers=h).status_code == 415
    assert client.post("/attachments?trade_id=999", files={"file": ("a.png", io.BytesIO(png), "image/png")}, headers=h).status_code == 404
    assert client.post(f"/attachments?trade_id={tid}", files={"file": ("a.png", io.BytesIO(b""), "image/png")}, headers=h).status_code == 422
    assert client.delete(f"/attachments/{aid}", headers=h).json() == {"ok": True}
    assert client.get(f"/attachments/{aid}", headers=h).status_code == 404


def test_risk_rules_start_empty_are_saved_by_the_owner_only_and_logged(client):
    h = _h(client)
    r = client.get("/risk-rules", headers=h).json()
    assert r["complete"] is False and len(r["missing"]) == 9 and r["rules"][0]["value"] is None
    assert {x["kind"] for x in r["rules"]} == {"share", "count", "switch"}
    assert client.get("/risk-rules").status_code == 401
    assert client.put("/risk-rules", json={"rules": [{"key": "max_open_ideas", "value": "5"}]},
                      headers={"X-Worker-Token": "worker-token-for-tests"}).status_code == 401   # the worker is refused
    assert client.put("/risk-rules", json={"rules": [{"key": "nope", "value": "1"}]}, headers=h).status_code == 422
    assert client.put("/risk-rules", json={"rules": [{"key": "max_loss_per_idea_share", "value": "50"}]}, headers=h).status_code == 422
    assert client.put("/risk-rules", json={"rules": [{"key": "cash_secured_puts_only", "value": "2"}]}, headers=h).status_code == 422
    rules = [{"key": x["key"], "value": x["suggested"], "enabled": True} for x in r["rules"]]
    saved = client.put("/risk-rules", json={"rules": rules}, headers=h).json()
    assert saved["complete"] is True and len(saved["changes"]) == 9
    again = client.put("/risk-rules", json={"rules": [{"key": "max_open_ideas", "value": "3", "enabled": False}]}, headers=h).json()
    assert again["changes"] == [{"key": "max_open_ideas", "old": {"value": "5.000000", "enabled": True},
                                 "new": {"value": "3", "enabled": False}}]
    changes = client.get("/risk-rules/changes", headers=h).json()["changes"]
    assert changes[0]["key"] == "max_open_ideas" and changes[0]["new_enabled"] is False and len(changes) == 10
    assert Decimal(next(x["value"] for x in again["rules"] if x["key"] == "max_open_ideas")) == 3
