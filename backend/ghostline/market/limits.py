"""The free tiers' counters, read from data_pulls: a call past a source's per-minute or per-day limit is refused
before it is made (RateLimited), so a job never burns the day's budget on one ticker. A job may wait out a full minute
instead (check(wait=True)), so a long list is paced rather than cut; a day's limit is never waited for."""
from __future__ import annotations

import time
from collections.abc import Callable

from ghostline.db import Database
from ghostline.market import RateLimited

# per source: (calls per minute, calls per day); None = no published limit
LIMITS: dict[str, tuple[int | None, int | None]] = {
    "alpaca": (200, None),
    "massive": (5, None),
    "fmp": (None, 250),
    "finnhub": (60, None),
    "edgar": (10, None),        # SEC asks for at most 10 requests a second; we keep well under
    "fred": (120, None),
    "tradier": (120, None),
    "gdelt": (1, 12),           # stricter in practice than its published one every 5 s: see Jobs.pull_tone
}
# Only production pulls: staging reads production's data, copied every night (worker/schedule.py), so production
# alone spends each key's free limit.
MAX_WAIT_S = 65


class Limiter:
    def __init__(self, db: Database, sleep: Callable[[float], None] = time.sleep) -> None:
        self.db = db
        self.sleep = sleep

    def used(self, source: str, seconds: int) -> int:
        row = self.db.fetch_one("SELECT count(*) AS n FROM data_pulls WHERE source = %s AND status <> 'skipped' "
                                "AND at > now() - make_interval(secs => %s)", (source, seconds))
        return int(row["n"]) if row else 0

    def check(self, source: str, wait: bool = False) -> None:
        per_minute, per_day = LIMITS.get(source, (None, None))
        if per_day is not None and self.used(source, 86400) >= per_day:
            raise RateLimited(f"{source}: {per_day} calls a day reached")
        waited = 0.0
        while per_minute is not None and self.used(source, 60) >= per_minute:
            pause = self.until_free(source)
            if not wait or waited + pause > MAX_WAIT_S:
                raise RateLimited(f"{source}: {per_minute} calls a minute reached")
            self.sleep(pause)
            waited += pause

    def until_free(self, source: str) -> float:
        """Seconds until the oldest counted call of the last minute leaves it, plus half a second."""
        row = self.db.fetch_one("SELECT EXTRACT(EPOCH FROM min(at) + interval '60 seconds' - now()) AS s "
                                "FROM data_pulls WHERE source = %s AND status <> 'skipped' "
                                "AND at > now() - interval '60 seconds'", (source,))
        return max(float(row["s"] or 0) if row else 0.0, 0.0) + 0.5

    def record(self, source: str, endpoint: str, status: str, detail: str | None = None, rows: int = 0) -> None:
        self.db.execute("INSERT INTO data_pulls (source, endpoint, status, detail, rows) VALUES (%s, %s, %s, %s, %s)",
                        (source, endpoint, status, (detail or "")[:500] or None, rows))

    def status(self) -> list[dict]:
        rows = self.db.fetch_all(
            "SELECT source, count(*) FILTER (WHERE at > now() - interval '1 day' AND status <> 'skipped') AS today, "
            "max(at) FILTER (WHERE status = 'ok') AS last_ok, max(at) AS last_call FROM data_pulls GROUP BY source")
        return [{**r, "last_ok": r["last_ok"].isoformat() if r["last_ok"] else None,
                 "last_call": r["last_call"].isoformat() if r["last_call"] else None} for r in rows]
