"""The ledger's arithmetic (FIFO lots, fees, totals) and the risk rules, pure, no database."""
import datetime as dt
from decimal import Decimal

from ghostline import ledger, risk
from ghostline.ledger import Trade

T0 = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)


def trade(id_, side, qty, fill, fees="0", book=1, ticker="AAPL", asset="stock", minutes=0):
    return Trade(id=id_, book_id=book, ticker=ticker, asset=asset, side=side, quantity=Decimal(qty), fill=Decimal(fill),
                 fees=Decimal(fees), at=T0 + dt.timedelta(minutes=minutes))


def test_fifo_lots_realize_pnl_after_fees():
    trades = [trade(1, "buy", "10", "100", "1", minutes=0), trade(2, "buy", "10", "110", "1", minutes=1),
              trade(3, "sell", "15", "120", "1.5", minutes=2)]
    closed, positions = ledger.settle(trades)
    (c,) = closed
    # 10 @ 100.10 + 5 @ 110.10 = 1001 + 550.5 = 1551.5 cost; proceeds 15*120 - 1.5 = 1798.5
    assert c.cost == Decimal("1551.50") and c.proceeds == Decimal("1798.50") and c.pnl == Decimal("247.00")
    assert c.opened_trade_ids == (1, 2) and c.quantity == Decimal("15")
    (p,) = positions
    assert p.quantity == Decimal("5") and p.cost_basis == Decimal("550.50") and p.average_cost == Decimal("110.10")
    assert p.unrealized(Decimal("130")) == Decimal("99.50") and p.unrealized(None) is None


def test_fractional_shares_and_a_loss():
    trades = [trade(1, "buy", "0.5", "200", "0", minutes=0), trade(2, "sell", "0.5", "180", "0", minutes=1)]
    closed, positions = ledger.settle(trades)
    assert closed[0].pnl == Decimal("-10.00") and closed[0].pnl_pct == Decimal("-10.00") and positions == []


def test_a_close_larger_than_the_lots_closes_what_exists():
    trades = [trade(1, "buy", "10", "50", minutes=0), trade(2, "sell", "12", "60", minutes=1)]
    closed, positions = ledger.settle(trades)
    assert closed[0].quantity == Decimal("10") and closed[0].pnl == Decimal("100.00") and positions == []


def test_books_and_tickers_never_mix():
    trades = [trade(1, "buy", "1", "10", book=1), trade(2, "buy", "1", "20", book=2),
              trade(3, "sell", "1", "30", book=2, minutes=1), trade(4, "buy", "2", "5", book=1, ticker="MSFT")]
    closed, positions = ledger.settle(trades)
    assert [(c.book_id, c.pnl) for c in closed] == [(2, Decimal("10.00"))]
    assert [(p.book_id, p.ticker, p.quantity) for p in positions] == [(1, "AAPL", Decimal("1")),
                                                                      (1, "MSFT", Decimal("2"))]


def test_options_open_and_close_but_are_not_stock_positions():
    trades = [trade(1, "sell_to_open", "1", "2.50", "0.65", asset="option", ticker="AAPL"),
              trade(2, "buy_to_close", "1", "1.00", "0.65", asset="option", ticker="AAPL", minutes=1)]
    closed, positions = ledger.settle(trades)
    assert closed[0].pnl == Decimal("148.70") and positions == []   # (2.50 × 100 − 0.65) − (1.00 × 100 + 0.65)


def test_totals():
    closed = [ledger.Closed(1, 1, "A", Decimal(1), Decimal("110"), Decimal("100"), T0, (1,)),
              ledger.Closed(2, 1, "B", Decimal(1), Decimal("90"), Decimal("100"), T0, (2,)),
              ledger.Closed(3, 1, "C", Decimal(1), Decimal("130"), Decimal("100"), T0, (3,))]
    t = ledger.totals(closed)
    assert t == {"trades": 3, "win_rate": Decimal("66.67"), "average_win": Decimal("20.00"),
                 "average_loss": Decimal("-10.00"), "net_pnl": Decimal("30.00")}
    assert ledger.totals([]) == {"trades": 0, "win_rate": None, "average_win": None, "average_loss": None,
                                 "net_pnl": Decimal("0.00")}


def test_cash_balance():
    trades = [trade(1, "buy", "2", "100", "1"), trade(2, "sell", "1", "120", "1", minutes=1)]
    assert ledger.cash_balance([Decimal("500"), Decimal("-50")], trades) == Decimal("368.00")


# ── the risk rules ─────────────────────────────────────────────────────────────────────────────────────────────
def rules(**over):
    rows = [{"key": k, "value": v, "enabled": True} for k, (_, v, _) in risk.RULES.items()]
    for r in rows:
        if r["key"] in over:
            val = over[r["key"]]
            if isinstance(val, dict):
                r.update(val)
            else:
                r["value"] = val
    return risk.RuleSet.from_rows(rows)


def idea(**over):
    base = dict(kind="swing", max_loss=Decimal("10"), days_to_earnings=30, avg_volume=Decimal("1000000"),
                spread_share=Decimal("0.002"))
    base.update(over)
    return risk.Idea(**base)


GOOD = idea()
ACCOUNT = risk.Account(balance=Decimal("1000"), cash_set_aside=Decimal("0"), open_ideas=1,
                       paper_drawdown_share=Decimal("0.02"))


def test_until_every_rule_is_set_nothing_passes():
    d = risk.RuleSet.from_rows([]).check_idea(GOOD, ACCOUNT)
    assert d.result == "refuse" and "not set" in d.reason and "max_loss_per_idea_share" in d.reason
    partial = risk.RuleSet.from_rows([{"key": "max_open_ideas", "value": 5, "enabled": True}])
    assert partial.check_idea(GOOD, ACCOUNT).result == "refuse"


def test_a_sound_idea_passes_and_each_rule_refuses_by_name():
    assert rules().check_idea(GOOD, ACCOUNT).passed
    assert rules().check_idea(idea(max_loss=Decimal("20")), ACCOUNT).rule == "max_loss_per_idea_share"
    crowded = risk.Account(Decimal("1000"), Decimal(0), 5, Decimal("0.02"))
    assert rules().check_idea(GOOD, crowded).rule == "max_open_ideas"
    down = risk.Account(Decimal("1000"), Decimal(0), 1, Decimal("0.10"))
    assert rules().check_idea(GOOD, down).rule == "drawdown_pause_share"
    assert rules().check_idea(idea(days_to_earnings=3), ACCOUNT).rule == "earnings_blackout_days"
    assert rules().check_idea(idea(days_to_earnings=3, about_earnings=True), ACCOUNT).passed
    assert rules().check_idea(idea(avg_volume=Decimal("1000")), ACCOUNT).rule == "liquidity_min_volume"
    assert rules().check_idea(idea(spread_share=Decimal("0.05")), ACCOUNT).rule == "liquidity_max_spread_share"


def test_puts_need_open_interest_and_cash_for_the_strike():
    put = risk.Idea(kind="puts", max_loss=Decimal("10"), days_to_earnings=40, avg_volume=Decimal("1000000"),
                    spread_share=Decimal("0.002"), open_interest=Decimal("800"), collateral=Decimal("5000"))
    poor = risk.Account(Decimal("1000"), Decimal("4000"), 1, Decimal("0"))
    assert rules().check_idea(put, poor).rule == "cash_secured_puts_only"
    rich = risk.Account(Decimal("1000"), Decimal("6000"), 1, Decimal("0"))
    assert rules().check_idea(put, rich).passed
    illiquid = risk.Idea(kind="puts", max_loss=Decimal("10"), days_to_earnings=40, avg_volume=Decimal("1000000"),
                         spread_share=Decimal("0.002"), open_interest=Decimal("10"), collateral=Decimal("5000"))
    assert rules().check_idea(illiquid, rich).rule == "liquidity_min_open_interest"


def test_day_trades_stay_in_practice_and_a_rule_switched_off_does_not_apply():
    day = risk.Idea(kind="day", max_loss=Decimal("10"), days_to_earnings=30, avg_volume=Decimal("1000000"),
                    spread_share=Decimal("0.002"), is_day_trade=True, book="real")
    assert rules().check_idea(day, ACCOUNT).rule == "day_trades_practice_only"
    assert rules().check_idea(risk.Idea(**{**day.__dict__, "book": "practice"}), ACCOUNT).passed
    off = rules(day_trades_practice_only={"value": 1, "enabled": False})
    assert off.check_idea(day, ACCOUNT).passed


def test_an_unknown_fact_refuses_never_passes():
    assert rules().check_idea(idea(max_loss=None), ACCOUNT).rule == "max_loss_per_idea_share"
    assert rules().check_idea(idea(days_to_earnings=None), ACCOUNT).rule == "earnings_blackout_days"


def test_suggestions_are_shares_counts_and_switches_never_dollars():
    s = risk.suggestions()
    assert {x["kind"] for x in s} == {"share", "count", "switch"}
    assert all(Decimal(x["suggested"]) < 1 for x in s if x["kind"] == "share")
