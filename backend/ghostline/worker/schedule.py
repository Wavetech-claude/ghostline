"""The worker's schedules, Pacific time (the plan): macro and the pre-market scan around 05:30, holdings checks every
30 minutes while the market is open, settlement and lessons after the 13:00 close, a weekly digest on Sunday."""
from __future__ import annotations

from typing import Any

from apscheduler.triggers.cron import CronTrigger

from ghostline.db import Database
from ghostline.market.jobs import Jobs

PT = "America/Los_Angeles"


def register(scheduler: Any, db: Database, jobs: Jobs | None = None) -> list[str]:
    """Add every job; returns their ids (tests read them). Each job is misfire-tolerant and never overlaps itself."""
    j = jobs or Jobs(db)
    plan = [
        ("pull_macro", j.pull_macro, CronTrigger(hour=5, minute=0, timezone=PT)),
        ("pull_bars", j.pull_bars, CronTrigger(hour=5, minute=15, timezone=PT)),
        ("pull_news_earnings", j.pull_news_earnings, CronTrigger(hour=6, minute=0, timezone=PT)),
        ("pull_fundamentals", j.pull_fundamentals, CronTrigger(hour=6, minute=10, timezone=PT)),
        ("pull_quotes", j.pull_quotes, CronTrigger(day_of_week="mon-fri", hour="6-13", minute="0,30", timezone=PT)),
    ]
    for job_id, fn, trigger in plan:
        scheduler.add_job(fn, trigger, id=job_id, name=job_id, replace_existing=True, coalesce=True,
                          max_instances=1, misfire_grace_time=600)
    return [p[0] for p in plan]
