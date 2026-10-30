"""Phase 7: paper results settled at the next open against SPY after costs, module weights pulled toward equal, a lane
or a module graduating only from settled results, lessons, the weekly test set and the weekly digest."""
import datetime as dt
import json
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from ghostline.claude import evaluate, jobs
from ghostline.claude import facts as F
from ghostline.claude.tracing import Tracer
from ghostline.ideas import qualifies
from ghostline.learning import Learning, blended_weight, graduates, next_open, weekly_digest
from ghostline.modules import Signal
from ghostline.storage import MemoryStore
from tests.conftest import settings as make_settings
from tests.conftest import sign_in
from tests.test_invest_ai import bar, idea
from tests.test_practice import _sql, deposit

D = Decimal
PT = ZoneInfo("America/Los_Angeles")


def _h(client):
    return {"Authorization": f"Bearer {sign_in(client)}"}


def made_at(idea_id, when: dt.datetime):
    _sql("UPDATE ideas SET created_at = %s WHERE id = %s", (when, idea_id))


def settled_idea(ticker, day: dt.date, entry, exit_, spy_in=400, spy_out=400, lane="swing", module="vcp", horizon=10, **kw):
    """An idea made before the open on `day`, with the opens it settles on stored."""
    iid = idea(ticker, lane=lane, module=module, horizon=horizon, **kw)
    made_at(iid, dt.datetime.combine(day, dt.time(6, 20), tzinfo=PT))
    bar(ticker, day, entry)
    bar("SPY", day, spy_in)
    close = day + dt.timedelta(days=horizon)
    bar(ticker, close, exit_)
    bar("SPY", close, spy_out)
    return iid


# ── the rules in code ───────────────────────────────────────────────────────────────────────────────────────
def test_the_next_open_follows_the_moment_the_idea_was_made():
    tue = dt.date(2026, 9, 1)
    assert next_open(dt.datetime.combine(tue, dt.time(6, 20), tzinfo=PT)) == tue           # before the bell
    assert next_open(dt.datetime.combine(tue, dt.time(7, 0), tzinfo=PT)) == dt.date(2026, 9, 2)
    fri = dt.date(2026, 9, 4)
    assert next_open(dt.datetime.combine(fri, dt.time(14, 0), tzinfo=PT)) == dt.date(2026, 9, 7)
    assert next_open(dt.datetime.combine(dt.date(2026, 9, 5), dt.time(6, 0), tzinfo=PT)) == dt.date(2026, 9, 7)


def test_weights_follow_expectancy_pulled_toward_equal_and_graduation_needs_both():
    assert blended_weight(None, 0, D(20)) == D(1)
    assert blended_weight(D(5), 20, D(20)) == D(2)                  # +5% excess, enough trades: double
    assert blended_weight(D(5), 5, D(20)) == D("1.25")              # a quarter of the way there
    assert blended_weight(D(-10), 40, D(20)) == D("0.25")           # floored, never muted
    assert graduates(30, D("0.1"), D(30)) and not graduates(29, D(5), D(30)) and not graduates(30, D("-0.1"), D(30))
    assert not graduates(30, None, D(30))
    s = Signal("T", "vcp", "swing", "long", 80, 40, "r", actionable=True)
    assert qualifies(s, D(1)) and not qualifies(s, D("0.5"))        # 80 × 0.5 = 40, under the 70 threshold
    assert not qualifies(Signal("T", "vcp", "swing", "long", 95, 40, "r", actionable=False), D(2))


# ── settlement ──────────────────────────────────────────────────────────────────────────────────────────────
def test_a_long_idea_settles_at_the_next_open_against_spy_after_costs(client):
    db = client.app.state.db
    iid = settled_idea("AAPL", dt.date(2026, 9, 1), entry=100, exit_=110, spy_in=400, spy_out=404)
    out = Learning(db).run(today=dt.date(2026, 9, 12))
    assert out["opened"] == 1 and out["settled"] == 1
    r = db.fetch_one("SELECT * FROM paper_results WHERE idea_id = %s", (iid,))
    assert (r["opened_on"], r["closes_on"]) == (dt.date(2026, 9, 1), dt.date(2026, 9, 11))
    assert (r["entry"], r["exit"], r["costs_pct"], r["result_pct"], r["spy_pct"], r["excess_pct"]) == (
        D("100.0000"), D("110.0000"), D("0.2000"), D("9.8000"), D("1.0000"), D("8.8000"))


def test_a_put_idea_settles_on_the_premium_kept_against_its_collateral(client):
    db = client.app.state.db
    exp = dt.date(2026, 10, 6)
    kept = settled_idea("X", dt.date(2026, 9, 1), entry=11, exit_=12, lane="puts", module="sell_puts", horizon=35,
                        option_symbol=f"X{exp:%y%m%d}P00010000")
    assigned = settled_idea("Y", dt.date(2026, 9, 1), entry=11, exit_=9, lane="puts", module="sell_puts", horizon=35,
                            option_symbol=f"Y{exp:%y%m%d}P00010000")
    _sql("UPDATE ideas SET entry = 0.5 WHERE id IN (%s, %s)", (kept, assigned))
    Learning(db).run(today=dt.date(2026, 10, 10))
    r = {x["idea_id"]: x for x in db.fetch_all("SELECT * FROM paper_results")}
    assert r[kept]["result_pct"] == D("4.8000")                     # 0.50 kept of 10 collateral, less 0.2% costs
    assert r[assigned]["result_pct"] == D("-5.2000")                # 0.50 − (10 − 9) = −0.50
    assert "strike 10" in r[assigned]["note"]


def test_no_stored_open_waits_then_is_marked_unpriced_never_guessed(client):
    db = client.app.state.db
    iid = idea("ZZZ", horizon=10)                                  # due on 2026-09-11
    made_at(iid, dt.datetime(2026, 9, 1, 6, 20, tzinfo=PT))
    learning = Learning(db)
    assert learning.run(today=dt.date(2026, 9, 12))["waiting"] == 1
    assert learning.run(today=dt.date(2026, 9, 30))["unpriced"] == 1
    assert db.fetch_one("SELECT status, note FROM paper_results WHERE idea_id = %s", (iid,))["status"] == "unpriced"


# ── graduation: the spec's done-when ────────────────────────────────────────────────────────────────────────
def test_a_lane_graduates_from_settled_results_only(client):
    h = _h(client)
    db = client.app.state.db
    assert client.put("/learning/settings", json={"graduation_n": 5}, headers=h).json()["graduation_n"] == "5.000000"
    days = [dt.date(2026, 9, d) for d in (1, 2, 3, 8, 9)]
    ids = [settled_idea(f"T{i}", d, entry=100, exit_=105) for i, d in enumerate(days)]
    learning = Learning(db)
    # five ideas exist but none has settled: nothing graduates
    learning.open_results()
    learning.update_lanes()
    assert db.fetch_one("SELECT graduated FROM lane_graduation WHERE lane = 'swing'")["graduated"] is False
    # four settled with a positive edge: one short
    learning.run(today=days[3] + dt.timedelta(days=10))           # the fourth is due, the fifth not yet
    swing = db.fetch_one("SELECT * FROM lane_graduation WHERE lane = 'swing'")
    assert swing["settled_n"] == 4 and swing["graduated"] is False
    # the fifth settles: the lane graduates, and so does the module
    learning.run(today=days[4] + dt.timedelta(days=10))
    swing = db.fetch_one("SELECT * FROM lane_graduation WHERE lane = 'swing'")
    assert swing["settled_n"] == 5 and swing["positive_expectancy"] and swing["graduated"] is True
    assert db.fetch_one("SELECT graduated FROM module_weights WHERE module = 'vcp'")["graduated"] is True
    assert db.fetch_one("SELECT graduated FROM lane_graduation WHERE lane = 'long'")["graduated"] is False
    # graduation is what the routes read: the idea is live, its lane may be copied for real
    _sql("UPDATE ideas SET expires_at = now() + interval '1 day' WHERE id = %s", (ids[0],))
    got = client.get("/ideas?lane=swing", headers=h).json()["ideas"]
    assert got and got[0]["live"] is True and got[0]["lane_graduated"] is True
    # raising the bar applies at once, from the same results
    client.put("/learning/settings", json={"graduation_n": 6}, headers=h)
    assert db.fetch_one("SELECT graduated FROM lane_graduation WHERE lane = 'swing'")["graduated"] is False


def test_a_lane_with_a_negative_edge_never_graduates(client):
    h = _h(client)
    db = client.app.state.db
    client.put("/learning/settings", json={"graduation_n": 5}, headers=h)
    for i, d in enumerate((1, 2, 3, 8, 9)):
        settled_idea(f"N{i}", dt.date(2026, 9, d), entry=100, exit_=101, spy_in=400, spy_out=420)   # +0.8% vs +5%
    Learning(db).run(today=dt.date(2026, 9, 30))
    swing = db.fetch_one("SELECT * FROM lane_graduation WHERE lane = 'swing'")
    assert swing["settled_n"] == 5 and swing["positive_expectancy"] is False and swing["graduated"] is False
    w = db.fetch_one("SELECT * FROM module_weights WHERE module = 'vcp'")
    assert w["weight"] < 1 and w["graduated"] is False and w["wins"] == 0


# ── lessons, the test set, the digest ───────────────────────────────────────────────────────────────────────
def _jobs(client, answer):
    calls = []

    def call(prompt, model, trace_id, name, **kw):
        calls.append({"name": name, "prompt": prompt})
        return answer(prompt) if callable(answer) else answer
    return jobs.Jobs(client.app.state.db, make_settings(), MemoryStore(), call=call, tracer=Tracer("", "", "", "test")), calls


def test_a_settled_idea_gets_one_grounded_lesson_that_later_prompts_read(client):
    db = client.app.state.db
    iid = settled_idea("AAPL", dt.date(2026, 9, 1), entry=100, exit_=110, spy_in=400, spy_out=404)
    out = Learning(db).run(today=dt.date(2026, 9, 12))
    assert out["lessons_queued"] == 1 and Learning(db).queue_lessons() == 0          # never queued twice
    j, calls = _jobs(client, "The VCP call on AAPL gained 9.8% against SPY's 1.0%, an 8.8% edge; one result, and 42% is invented.")
    assert j.run_next()["status"] == "done"
    assert '"excess_pct": "8.8000"' in calls[0]["prompt"] and "prior_lessons" in calls[0]["prompt"]
    lesson = db.fetch_one("SELECT * FROM lessons WHERE idea_id = %s", (iid,))
    assert lesson["grounded"] is False and "[n/a]" in lesson["text"] and "8.8%" in lesson["text"]
    assert Learning(db).queue_lessons() == 0
    assert F.ticker_facts(db, "AAPL")["lessons"][0]["ticker"] == "AAPL"
    assert F.brief_facts(db)["lessons"][0]["module"] == "vcp"


def test_the_weekly_test_set_asks_about_stored_numbers_and_scores_the_answers(client):
    h = _h(client)
    db = client.app.state.db
    for d, c in ((dt.date(2026, 10, 1), 100), (dt.date(2026, 10, 2), 104.5)):
        bar("AAPL", d, c)
        bar("SPY", d, c * 4)
    client.post("/watchlist", json={"ticker": "AAPL"}, headers=h)
    deposit(client, h, 1234.5)
    qs, facts = evaluate.questions(db)
    texts = [q["question"] for q in qs]
    assert "What was AAPL's most recent stored daily close?" in texts and len(qs) <= 12
    assert facts["tickers"]["AAPL"]["last_close"] == "104.5000" and facts["books"]["practice"]["cash"] == "1234.50"

    def answer(prompt):
        out = []
        for q in qs:
            if "AAPL's most recent" in q["question"]:
                out.append({"n": q["n"], "answer": "104.50, the close of 2026-10-02"})
            elif "practice" in q["question"]:
                out.append({"n": q["n"], "answer": "$1,234.50"})
            else:
                out.append({"n": q["n"], "answer": "not in the stored data"})
        return json.dumps({"answers": out})
    j, _ = _jobs(client, answer)
    r = client.post("/journal/eval", headers=h)
    assert r.status_code == 202
    assert j.run_next()["status"] == "done"
    ev = client.get("/journal", headers=h).json()["last_eval"]
    assert ev["correct"] == 2 and ev["questions"] == len(qs)
    runs = client.get("/journal/evals", headers=h).json()["evals"][0]["results"]
    assert {r["question"] for r in runs if r["correct"]} == {
        "What was AAPL's most recent stored daily close?", "How much cash does the practice book hold?"}
    assert evaluate.correct("there are 3 ideas", D(3)) and not evaluate.correct("about 3.2", D(3))


def test_the_weekly_digest_is_written_in_code_from_stored_numbers(client):
    h = _h(client)
    db = client.app.state.db
    settled_idea("AAPL", dt.date(2026, 9, 1), entry=100, exit_=110, spy_in=400, spy_out=404)
    Learning(db).run(today=dt.date(2026, 9, 12))
    _sql("UPDATE paper_results SET settled_at = '2026-09-12T20:00:00Z'")
    d = weekly_digest(db, today=dt.date(2026, 9, 13))                  # a Sunday: the week from Monday 7 September
    assert d["week_of"] == "2026-09-07"
    assert "Settled this week: 1 ideas, 1 beat SPY" in d["content"] and "AAPL (vcp, swing): 9.8000% vs SPY 1.0000%" in d["content"]
    assert "- vcp: 1 settled" in d["content"] and "- swing: 1 settled" in d["content"]
    assert weekly_digest(db, today=dt.date(2026, 9, 13))["id"] == d["id"]   # one digest per week
    j = client.get("/journal", headers=h).json()
    assert j["digest"]["week_of"] == "2026-09-07" and j["counts"]["settled"] == 1
    assert j["recent"][0]["ticker"] == "AAPL" and j["settings"]["graduation_n"] == "30.000000"


def test_the_journal_routes(client):
    h = _h(client)
    db = client.app.state.db
    settled_idea("AAPL", dt.date(2026, 9, 1), entry=100, exit_=110)
    assert client.post("/journal/run", headers=h).json()["settled"] >= 0
    Learning(db).run(today=dt.date(2026, 9, 12))
    assert len(client.get("/journal/results?status=settled&lane=swing", headers=h).json()["results"]) == 1
    assert client.get("/journal/results?lane=puts", headers=h).json()["results"] == []
    assert client.get("/lessons", headers=h).json()["lessons"] == []
    assert client.put("/learning/settings", json={}, headers=h).status_code == 422
    assert client.put("/learning/settings", json={"graduation_n": 2}, headers=h).status_code == 422   # at least 5
    assert client.get("/journal").status_code == 401


@pytest.mark.parametrize("jobs_expected", [{"learning_run", "weekly_eval", "weekly_digest"}])
def test_the_worker_registers_the_learning_jobs(client, jobs_expected):
    from apscheduler.schedulers.background import BackgroundScheduler

    from ghostline.worker import schedule
    ids = schedule.register(BackgroundScheduler(timezone="America/Los_Angeles"), client.app.state.db, settings=make_settings())
    assert jobs_expected <= set(ids)
