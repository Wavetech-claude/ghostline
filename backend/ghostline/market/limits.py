"""The free tiers' counters, read from data_pulls: a call past a source's per-minute or per-day limit is refused
before it is made (RateLimited), so a job never burns the day's budget on one ticker."""
from __future__ import annotations

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
    "gdelt": (10, None),        # its own limit: one request every 5 seconds
}


class Limiter:
    def __init__(self, db: Database) -> None:
        self.db = db

    def used(self, source: str, seconds: int) -> int:
        row = self.db.fetch_one("SELECT count(*) AS n FROM data_pulls WHERE source = %s AND status <> 'skipped' "
                                "AND at > now() - make_interval(secs => %s)", (source, seconds))
        return int(row["n"]) if row else 0

    def check(self, source: str) -> None:
        per_minute, per_day = LIMITS.get(source, (None, None))
        if per_minute is not None and self.used(source, 60) >= per_minute:
            raise RateLimited(f"{source}: {per_minute} calls a minute reached")
        if per_day is not None and self.used(source, 86400) >= per_day:
            raise RateLimited(f"{source}: {per_day} calls a day reached")

    def record(self, source: str, endpoint: str, status: str, detail: str | None = None, rows: int = 0) -> None:
        self.db.execute("INSERT INTO data_pulls (source, endpoint, status, detail, rows) VALUES (%s, %s, %s, %s, %s)",
                        (source, endpoint, status, (detail or "")[:500] or None, rows))

    def status(self) -> list[dict]:
        rows = self.db.fetch_all(
            "SELECT source, count(*) FILTER (WHERE at > now() - interval '1 day' AND status <> 'skipped') AS today, "
            "max(at) FILTER (WHERE status = 'ok') AS last_ok, max(at) AS last_call FROM data_pulls GROUP BY source")
        return [{**r, "last_ok": r["last_ok"].isoformat() if r["last_ok"] else None,
                 "last_call": r["last_call"].isoformat() if r["last_call"] else None} for r in rows]
