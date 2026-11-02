"""Recurring buys in practice (the owner's go, 2026-11-01): a plan buys $X of a ticker every day, week, two weeks or
month, as an ordinary practice market order, on sessions only; a closed day moves to the next session; refusals and
missing prices are recorded as skipped; pause, resume and delete; once per day."""
import datetime as dt
from decimal import Decimal

from ghostline.market.calendar import NEW_YORK, WEEKDAYS, Calendar
from ghostline.recurring import Recurring, due, is_scheduled, next_run
from tests.test_practice import _h, _sql, deposit, ready_stock, set_rules

D = Decimal


def _plan(every, **kw):
    return {"every": every, "weekday": None, "month_day": None, "starts_on": dt.date(2026, 10, 1), **kw}


def _holiday_calendar(closed: dt.date) -> Calendar:
    """The weekday rule with one closed weekday (a holiday), over three months."""
    from ghostline.market import MarketSession
    sessions = {}
    d = dt.date(2026, 10, 1)
    while d <= dt.date(2026, 12, 31):
        if d.weekday() < 5 and d != closed:
            sessions[d] = MarketSession(d, dt.datetime.combine(d, dt.time(9, 30), tzinfo=NEW_YORK),
                                        dt.datetime.combine(d, dt.time(16), tzinfo=NEW_YORK), d + dt.timedelta(days=1))
        d += dt.timedelta(days=1)
    return Calendar(sessions, dt.date(2026, 10, 1), dt.date(2026, 12, 31))


# ── the schedule ───────────────────────────────────────────────────────────────────────────────────────────
def test_each_frequency_names_its_own_dates():
    wed = dt.date(2026, 11, 4)
    assert is_scheduled(_plan("week", weekday=2), wed) and not is_scheduled(_plan("week", weekday=2), wed + dt.timedelta(days=1))
    every_other = _plan("two_weeks", weekday=0, starts_on=dt.date(2026, 11, 1))           # starts Sunday: Mondays 2, 16, 30
    assert [d.day for d in (dt.date(2026, 11, d) for d in range(1, 31)) if is_scheduled(every_other, d)] == [2, 16, 30]
    monthly = _plan("month", month_day=31)
    assert is_scheduled(monthly, dt.date(2026, 11, 30)) and is_scheduled(monthly, dt.date(2026, 12, 31))   # a short month: its last day
    assert not is_scheduled(_plan("week", weekday=2, starts_on=dt.date(2026, 11, 5)), wed)               # before it starts


def test_a_closed_day_moves_to_the_next_session():
    thanksgiving = dt.date(2026, 11, 26)
    cal = _holiday_calendar(thanksgiving)
    weekly = _plan("week", weekday=3)                                       # Thursdays
    assert due(weekly, thanksgiving, cal) is None
    assert due(weekly, dt.date(2026, 11, 27), cal) == thanksgiving          # runs Friday, scheduled Thursday
    monthly = _plan("month", month_day=1)
    assert due(monthly, dt.date(2026, 11, 2), WEEKDAYS) == dt.date(2026, 11, 1)   # Sunday the 1st runs Monday the 2nd
    daily = _plan("day")
    assert due(daily, dt.date(2026, 11, 7), WEEKDAYS) is None and due(daily, dt.date(2026, 11, 9), WEEKDAYS) == dt.date(2026, 11, 9)
    assert next_run(weekly, dt.date(2026, 11, 23), cal) == dt.date(2026, 11, 27)


# ── the job ────────────────────────────────────────────────────────────────────────────────────────────────
def _make(client, h, **body):
    r = client.post("/recurring", json={"ticker": "aapl", "dollars": "50", "every": "day", **body}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _today_session() -> dt.date:
    d = dt.datetime.now(NEW_YORK).date()
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def test_a_due_plan_buys_fractional_shares_once_and_records_the_run(client):
    h = _h(client)
    set_rules(client, h)
    ready_stock()                                                              # ask 100.10
    deposit(client, h, 100_000)
    day = _today_session()
    plan = _make(client, h, starts_on=day.isoformat())
    out = Recurring(client.app.state.db).run_due(day)
    assert out["placed"] == 1
    again = Recurring(client.app.state.db).run_due(day)
    assert again["placed"] == 0                                                # once a day
    got = client.get("/recurring", headers=h).json()["plans"][0]
    run = got["runs"][0]
    assert run["status"] == "placed" and run["order_status"] == "filled" and run["quantity"] == "0.499500"
    o = client.get("/practice/orders?book=practice", headers=h).json()["orders"][0]
    assert o["type"] == "market" and f"recurring buy #{plan['id']}" in o["note"]


def test_a_refused_buy_or_no_price_is_skipped_with_the_reason(client):
    h = _h(client)
    deposit(client, h, 100_000)
    ready_stock()
    day = _today_session()
    _make(client, h, starts_on=day.isoformat())                                # no risk rules set: refused
    _make(client, h, ticker="ZZZZ", starts_on=day.isoformat())                 # no price stored
    out = Recurring(client.app.state.db).run_due(day)
    assert out["skipped"] == 2
    runs = {p["ticker"]: p["runs"][0] for p in client.get("/recurring", headers=h).json()["plans"]}
    assert "risk rules not set" in runs["AAPL"]["reason"] and runs["AAPL"]["order_id"]
    assert runs["ZZZZ"]["reason"] == "no stored price for ZZZZ" and runs["ZZZZ"]["order_id"] is None


def test_a_closed_day_is_recorded_on_the_run_of_the_next_session(client):
    h = _h(client)
    set_rules(client, h)
    ready_stock()
    deposit(client, h, 100_000)
    _make(client, h, every="month", month_day=1, starts_on="2026-10-01")
    _sql("INSERT INTO market_sessions (day, opens_at, closes_at, settles_on, source, as_of) VALUES "
         "('2026-11-02', '2026-11-02 14:30+00', '2026-11-02 21:00+00', '2026-11-03', 'test', now()), "
         "('2026-10-30', '2026-10-30 13:30+00', '2026-10-30 20:00+00', '2026-11-02', 'test', now())")
    out = Recurring(client.app.state.db).run_due(dt.date(2026, 11, 2))         # Sunday the 1st runs Monday the 2nd
    run = client.get("/recurring", headers=h).json()["plans"][0]["runs"][0]
    assert out["placed"] == 1 and run["scheduled"] == "2026-11-01" and "market was closed" in run["reason"]
    assert Recurring(client.app.state.db).run_due(dt.date(2026, 11, 1)) == {"placed": 0, "skipped": 0, "not_due": 0,
                                                                           "closed": "2026-11-01"}


def test_pause_resume_delete_and_the_ticket_rules(client):
    h = _h(client)
    day = _today_session()
    p = _make(client, h, every="week", weekday=day.weekday(), starts_on=day.isoformat())
    assert p["next_run"] is not None and p["status"] == "active"
    paused = client.patch(f"/recurring/{p['id']}", json={"status": "paused"}, headers=h).json()
    assert paused["status"] == "paused" and paused["next_run"] is None
    assert Recurring(client.app.state.db).run_due(day)["placed"] == 0          # a paused plan does not run
    assert client.patch(f"/recurring/{p['id']}", json={"status": "active"}, headers=h).json()["status"] == "active"
    assert client.delete(f"/recurring/{p['id']}", headers=h).json() == {"id": p["id"], "status": "deleted"}
    assert client.get("/recurring", headers=h).json()["plans"] == []
    assert client.delete(f"/recurring/{p['id']}", headers=h).status_code == 404
    kept = client.app.state.db.fetch_one("SELECT status FROM recurring_plans WHERE id = %s", (p["id"],))
    assert kept["status"] == "deleted"                                          # kept on record
    for bad in ({"every": "week"}, {"every": "month"}, {"every": "day", "weekday": 1}, {"every": "week", "weekday": 6},
                {"every": "hourly"}, {"every": "day", "dollars": "0"}, {"every": "day", "dollars": "1.234"}):
        body = {"ticker": "AAPL", "dollars": "50", **bad}
        assert client.post("/recurring", json=body, headers=h).status_code == 422, bad
    assert client.get("/recurring").status_code == 401
