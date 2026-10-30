"""The exchange's calendar: which days are sessions, when each opens and closes (early closes included), and when a
sale made that day settles. The truth is Alpaca's calendar, pulled every day into market_sessions about a year ahead
(checked 2026-10-30: Thanksgiving 2026-11-26 is absent, 11-27 closes at 13:00 New York time, and a sale on 11-25
settles on 11-27). A day the stored calendar does not cover follows the weekday rule with a regular session's hours
and next-session settlement: that is how tests run, and how the app runs until the first pull."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from ghostline.db import Database
from ghostline.market import MarketSession

NEW_YORK = ZoneInfo("America/New_York")
REGULAR_OPEN = dt.time(9, 30)
REGULAR_CLOSE = dt.time(16, 0)


@dataclass(frozen=True)
class Calendar:
    sessions: dict[dt.date, MarketSession] = field(default_factory=dict)
    first: dt.date | None = None
    last: dt.date | None = None

    @classmethod
    def load(cls, db: Database) -> Calendar:
        rows = db.fetch_all("SELECT day, opens_at, closes_at, settles_on FROM market_sessions ORDER BY day")
        sessions = {r["day"]: MarketSession(r["day"], r["opens_at"], r["closes_at"], r["settles_on"]) for r in rows}
        return cls(sessions, rows[0]["day"] if rows else None, rows[-1]["day"] if rows else None)

    def covers(self, day: dt.date) -> bool:
        return self.first is not None and self.last is not None and self.first <= day <= self.last

    def is_session(self, day: dt.date) -> bool:
        return day in self.sessions if self.covers(day) else day.weekday() < 5

    def next_session(self, day: dt.date) -> dt.date:
        """The first session after `day`."""
        n = day + dt.timedelta(days=1)
        while not self.is_session(n):
            n += dt.timedelta(days=1)
        return n

    def previous_session(self, day: dt.date) -> dt.date:
        """The last session before `day`."""
        n = day - dt.timedelta(days=1)
        while not self.is_session(n):
            n -= dt.timedelta(days=1)
        return n

    def opens_at(self, day: dt.date) -> dt.datetime:
        s = self.sessions.get(day)
        return s.opens_at if s else dt.datetime.combine(day, REGULAR_OPEN, tzinfo=NEW_YORK)

    def closes_at(self, day: dt.date) -> dt.datetime:
        s = self.sessions.get(day)
        return s.closes_at if s else dt.datetime.combine(day, REGULAR_CLOSE, tzinfo=NEW_YORK)

    def settles_on(self, day: dt.date) -> dt.date:
        """When a sale made on `day` settles: T+1 on the settlement calendar (Alpaca's own date when stored, which
        also skips bank holidays the exchange trades through)."""
        s = self.sessions.get(day)
        return s.settles_on if s else self.next_session(day)

    def last_close(self, now: dt.datetime) -> dt.datetime:
        """The most recent session close at or before `now`."""
        today = now.astimezone(NEW_YORK).date()
        if self.is_session(today) and self.closes_at(today) <= now:
            return self.closes_at(today)
        return self.closes_at(self.previous_session(today))


WEEKDAYS = Calendar()     # the weekday rule only: no stored calendar
