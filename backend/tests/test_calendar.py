"""The exchange calendar: Alpaca's answer parsed, stored and replaced as one. A holiday is no session, an early close
ends the session early, settlement follows Alpaca's dates, and every place that used the weekday rule asks the
calendar. Outside the stored range the weekday rule still holds."""
import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

from ghostline.db import Database
from ghostline.invest import Planner, default_date
from ghostline.learning import next_open
from ghostline.market import store
from ghostline.market.calendar import Calendar
from ghostline.market.jobs import Jobs
from ghostline.market.sources import Alpaca
from ghostline.practice import session_close, settles_at
from ghostline.worker.schedule import before_close, on_sessions
from tests.conftest import settings

PT = ZoneInfo("America/Los_Angeles")
NOV = {d: dt.date(2026, 11, d) for d in range(20, 31)}


def _day(date: str, close: str, settles: str) -> dict:
    return {"date": date, "open": "09:30", "close": close, "session_open": "0400",
            "session_close": "2000" if close == "16:00" else "1700", "settlement_date": settles}


# Alpaca's answer, read live 2026-10-30: Thanksgiving (11-26) absent, the day after closes at 13:00 New York time
LATE_NOVEMBER = [_day("2026-11-24", "16:00", "2026-11-25"), _day("2026-11-25", "16:00", "2026-11-27"),
                 _day("2026-11-27", "13:00", "2026-11-30"), _day("2026-11-30", "16:00", "2026-12-01")]


@pytest.fixture
def alpaca_keys(monkeypatch):
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA", "test-alpaca")
    monkeypatch.setenv("GHOSTLINE_KEY_ALPACA_SECRET", "test-alpaca-secret")


def _http(answer: list[dict]):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer)
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def _stored(db: Database, answer: list[dict], start: dt.date = NOV[24], end: dt.date = NOV[30]) -> Calendar:
    http, _ = _http(answer)
    store.save_sessions(db, Alpaca(http).calendar(start, end), start, end, "alpaca", dt.datetime.now(dt.UTC))
    return Calendar.load(db)


def test_alpacas_calendar_is_read_with_early_closes_and_settlement(alpaca_keys):
    http, seen = _http(LATE_NOVEMBER)
    sessions = Alpaca(http).calendar(NOV[24], NOV[30])
    assert [s.day for s in sessions] == [NOV[24], NOV[25], NOV[27], NOV[30]]
    assert sessions[2].closes_at == dt.datetime(2026, 11, 27, 10, 0, tzinfo=PT)      # 13:00 New York
    assert sessions[1].settles_on == NOV[27]                                          # past the holiday
    assert seen[0].url.host == "paper-api.alpaca.markets" and seen[0].url.path == "/v2/calendar"
    assert seen[0].headers["APCA-API-KEY-ID"] == "test-alpaca"


def test_a_holiday_is_no_session_and_an_early_close_ends_the_day_early(fresh_db, alpaca_keys):
    db = Database(settings())
    cal = _stored(db, LATE_NOVEMBER)
    assert not cal.is_session(NOV[26]) and cal.is_session(NOV[27])
    assert cal.next_session(NOV[25]) == NOV[27] and cal.previous_session(NOV[27]) == NOV[25]
    assert cal.closes_at(NOV[27]) == dt.datetime(2026, 11, 27, 10, 0, tzinfo=PT)
    assert cal.settles_on(NOV[25]) == NOV[27]
    # outside the stored range the weekday rule holds
    assert not cal.is_session(dt.date(2026, 12, 5)) and cal.is_session(dt.date(2026, 12, 7))
    assert not Calendar().is_session(NOV[21]) and Calendar().is_session(NOV[26])     # no calendar: weekdays only
    db.close()


def test_a_closure_announced_later_replaces_the_stored_day(fresh_db, alpaca_keys):
    db = Database(settings())
    assert _stored(db, LATE_NOVEMBER).is_session(NOV[27])
    later = [d for d in LATE_NOVEMBER if d["date"] != "2026-11-27"]
    cal = _stored(db, later)
    assert not cal.is_session(NOV[27]) and cal.next_session(NOV[25]) == NOV[30]
    db.close()


def test_practice_settles_and_closes_on_the_calendar(fresh_db, alpaca_keys):
    db = Database(settings())
    cal = _stored(db, LATE_NOVEMBER)
    sold = dt.datetime(2026, 11, 25, 12, 0, tzinfo=PT)
    assert settles_at(sold, cal) == dt.datetime(2026, 11, 27, 6, 30, tzinfo=PT)      # not on Thanksgiving
    assert session_close(NOV[27], cal) == dt.datetime(2026, 11, 27, 10, 0, tzinfo=PT)
    assert session_close(NOV[26], cal) == dt.datetime(2026, 11, 25, 13, 0, tzinfo=PT)   # a holiday expiry
    assert cal.last_close(dt.datetime(2026, 11, 27, 9, 0, tzinfo=PT)) == session_close(NOV[25], cal)
    assert cal.last_close(dt.datetime(2026, 11, 27, 11, 0, tzinfo=PT)) == session_close(NOV[27], cal)
    db.close()


def test_invest_and_the_learning_loop_skip_the_holiday(fresh_db, alpaca_keys):
    db = Database(settings())
    cal = _stored(db, LATE_NOVEMBER)
    evening = dt.datetime(2026, 11, 25, 15, 0, tzinfo=PT)
    assert default_date(evening, cal) == NOV[27]
    assert Planner(db).plan(Decimal("100"), NOV[26], "practice")["for_date"] == "2026-11-27"
    assert next_open(dt.datetime(2026, 11, 25, 7, 0, tzinfo=PT), cal) == NOV[27]
    assert next_open(dt.datetime(2026, 11, 27, 6, 0, tzinfo=PT), cal) == NOV[27]      # before the bell
    db.close()


def test_practice_fills_stop_at_the_close_even_an_early_one(fresh_db, alpaca_keys):
    db = Database(settings())
    cal = _stored(db, LATE_NOVEMBER)
    assert before_close(cal, dt.datetime(2026, 11, 27, 9, 59, tzinfo=PT))
    assert not before_close(cal, dt.datetime(2026, 11, 27, 10, 0, tzinfo=PT))
    assert not before_close(cal, dt.datetime(2026, 11, 26, 8, 0, tzinfo=PT))
    assert before_close(cal, dt.datetime(2026, 11, 30, 12, 59, tzinfo=PT))
    db.close()


def test_a_session_job_does_nothing_on_a_closed_day(fresh_db, alpaca_keys):
    db = Database(settings())
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()
    ran = []
    job = on_sessions(db, lambda: ran.append(1) or {"ran": True})
    before, after = today - dt.timedelta(days=1), today + dt.timedelta(days=1)
    around = [_day(before.isoformat(), "16:00", today.isoformat()), _day(after.isoformat(), "16:00", after.isoformat())]
    _stored(db, around, before, after)                              # today between two sessions, absent: closed
    assert job() == {"skipped": f"the market is closed on {today.isoformat()}"} and ran == []
    _stored(db, [*around[:1], _day(today.isoformat(), "16:00", after.isoformat()), *around[1:]], before, after)
    assert job() == {"ran": True} and ran == [1]
    db.close()


def test_the_calendar_pull_stores_a_year_ahead(fresh_db, alpaca_keys):
    db = Database(settings())
    http, seen = _http(LATE_NOVEMBER)
    out = Jobs(db, http).pull_calendar(today=NOV[20])
    assert out == {"sessions": 4, "through": "2026-11-30"}
    assert seen[0].url.params["start"] == "2026-11-10" and seen[0].url.params["end"] == "2027-12-25"
    assert Calendar.load(db).last == NOV[30]
    db.close()
