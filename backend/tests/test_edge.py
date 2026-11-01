"""Reconciliation part 4: the loop learns from what the owner does (the owner, 2026-11-01: ranking plus a your-edge
report; module weights stay on paper results only, the unbiased record of each module).

Per module and lane: what the owner took, declined or let lapse, the take rate, the real result of what was taken
against that idea's own paper result (the execution gap), and what the skipped ideas did on paper. Open ideas rank by
score × a fit factor from the take rate (0.75 to 1.25, neutral until the owner decides); nothing is hidden."""
import datetime as dt
from decimal import Decimal

import psycopg

from ghostline import edge
from tests.conftest import TEST_DB
from tests.test_invest_ai import _h, flow, idea
from tests.test_reconcile import _trade


def _sql(sql, params=()):
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute(sql, params)


def _paper(idea_id, ticker, lane, module, result, excess):
    _sql("INSERT INTO paper_results (idea_id, ticker, lane, module, opened_on, closes_on, status, result_pct, excess_pct) "
         "VALUES (%s, %s, %s, %s, CURRENT_DATE, CURRENT_DATE, 'settled', %s, %s)",
         (idea_id, ticker, lane, module, result, excess))


def test_the_fit_factor_is_neutral_until_the_owner_decides_and_bounded():
    assert edge.fit_factor(took=0, decided=0) == Decimal("1.00")
    assert edge.fit_factor(took=5, decided=5) > Decimal("1.15")
    assert edge.fit_factor(took=0, decided=5) < Decimal("0.85")
    assert Decimal("0.75") < edge.fit_factor(took=0, decided=500) < edge.fit_factor(took=500, decided=500) < Decimal("1.25")


def test_your_edge_per_module_and_lane(client):
    h = _h(client)
    now = dt.datetime.now(dt.UTC)
    flow(client, h, "real", 10000, now.date())
    took = idea("AAA", lane="swing", module="vcp")
    said_no = idea("BBB", lane="swing", module="vcp")
    lapsed = idea("CCC", lane="long", module="canslim")
    idea("DDD", lane="long", module="canslim")                                   # still open: not a decision
    _sql("UPDATE ideas SET expires_at = now() - interval '1 day' WHERE id = %s", (lapsed,))
    _trade(client, h, "AAA", "buy", 10, 100, now - dt.timedelta(minutes=2), idea_id=took)
    _trade(client, h, "AAA", "sell", 10, 108, now - dt.timedelta(minutes=1))       # real +8 %
    client.post("/decisions", json={"kind": "idea", "ref_id": said_no, "reason": "no"}, headers=h)
    _paper(took, "AAA", "swing", "vcp", "10.00", "6.00")                          # the idea itself made +10 %
    _paper(said_no, "BBB", "swing", "vcp", "-3.00", "-5.00")                       # saying no avoided -5 % vs SPY
    _paper(lapsed, "CCC", "long", "canslim", "4.00", "2.00")                       # letting it lapse missed +2 %

    rep = client.get("/edge", headers=h).json()
    by = {(g["module"], g["lane"]): g for g in rep["groups"]}
    vcp = by[("vcp", "swing")]
    assert (vcp["took"], vcp["declined"], vcp["lapsed"], vcp["open"]) == (1, 1, 0, 0)
    assert vcp["take_rate"] == "0.50" and vcp["real_pct_avg"] == "8.00"
    assert vcp["gap_pct_avg"] == "-2.00"                                          # real +8 against the idea's +10
    assert vcp["skipped_excess_avg"] == "-5.00"
    canslim = by[("canslim", "long")]
    assert (canslim["took"], canslim["lapsed"], canslim["open"]) == (0, 1, 1) and canslim["skipped_excess_avg"] == "2.00"
    assert rep["totals"]["took"] == 1 and rep["totals"]["decided"] == 3
    assert "paper results only" in rep["note"]


def test_open_ideas_rank_by_score_times_fit(client):
    h = _h(client)
    now = dt.datetime.now(dt.UTC)
    flow(client, h, "real", 10000, now.date())
    # the owner took three VCP ideas and declined three CANSLIM ones
    for t in ("V1", "V2", "V3"):
        i = idea(t, module="vcp", lane="swing")
        _trade(client, h, t, "buy", 1, 10, now - dt.timedelta(minutes=5), idea_id=i)
    for t in ("C1", "C2", "C3"):
        i = idea(t, module="canslim", lane="long")
        client.post("/decisions", json={"kind": "idea", "ref_id": i, "reason": "no"}, headers=h)
    _sql("UPDATE ideas SET status = 'expired'")                                    # those were decided; new ones below
    idea("NEWC", module="canslim", lane="long", score=84)
    idea("NEWV", module="vcp", lane="swing", score=80)
    got = [i["ticker"] for i in client.get("/ideas", headers=h).json()["ideas"]]
    assert got[:2] == ["NEWV", "NEWC"]                                            # 80 × fit(vcp) beats 84 × fit(canslim)
