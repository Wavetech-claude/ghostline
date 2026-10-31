"""The worker's schedules, Pacific time (the plan): macro and the pre-market scan around 05:30, holdings checks every
30 minutes while the market is open, settlement and lessons after the 13:00 close, a weekly digest on Sunday.

Only production pulls market data. Staging reads production's database, copied over staging's every night by a host
job on the prod VM (homelab_skills infra/ghostline-staging-refresh; the owner's ruling, 2026-10-30: one set of keys,
pulled once), so staging registers no pull job."""

from __future__ import annotations

import datetime as dt
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ghostline import alerts, corporate, reconcile, storage
from ghostline.ai_trader import AITrader
from ghostline.claude import push
from ghostline.claude.jobs import Jobs as ClaudeJobs
from ghostline.claude.jobs import enqueue
from ghostline.config import Settings, load
from ghostline.db import Database
from ghostline.ghost import Ghost
from ghostline.ideas import Scan
from ghostline.invest import Planner
from ghostline.learning import Learning, weekly_digest
from ghostline.market.calendar import NEW_YORK, Calendar
from ghostline.market.jobs import Jobs
from ghostline.practice import Practice

PT = "America/Los_Angeles"
# every job that calls a data source; staging has none of them (see the module's docstring)
PULLS = ("pull_calendar", "pull_macro", "pull_tone", "build_universe", "pull_bars", "pull_corporate_actions",
         "pull_news_earnings", "pull_fundamentals", "pull_chains", "pull_quotes")


def register(
    scheduler: Any,
    db: Database,
    jobs: Jobs | None = None,
    practice: Practice | None = None,
    settings: Settings | None = None,
    claude: ClaudeJobs | None = None,
) -> list[str]:
    """Add every job; returns their ids (tests read them). Each job is misfire-tolerant and never overlaps itself."""
    settings = settings or load()
    j = jobs or Jobs(db)
    p = practice or Practice(db)
    c = claude or ClaudeJobs(db, settings, storage.for_settings(settings))

    def claude_poll() -> dict | None:
        return c.run_next()

    def morning_brief() -> int:
        return enqueue(db, "brief", {"push": True})

    ghost = Ghost(db)
    planner = Planner(db)
    ai = AITrader(db)
    options = AITrader(db, book="options")

    def exits() -> dict:
        return {"ai": ai.run_exits(), "options": options.run_exits()}

    def alerts_check() -> dict:
        return alerts.check(db, settings)

    def learning_run() -> dict:
        return Learning(db).run()

    def weekly_eval() -> int:
        return enqueue(db, "eval", {})

    def digest_and_push() -> dict:
        d = weekly_digest(db)
        title = d["content"].split("\n", 1)[0].lstrip("# ")
        sent = push.send(db, settings, "Your week in Ghostline", title, {"kind": "digest", "week_of": d["week_of"]})
        if sent.get("sent"):
            db.execute("UPDATE weekly_digests SET pushed_at = now() WHERE id = %s", (d["id"],))
        return {**d, "push": sent}

    def fill_orders() -> dict:
        if not before_close(Calendar.load(db), dt.datetime.now(dt.UTC)):
            return {"skipped": "after the close: practice orders fill only while the market is open"}
        return p.fill_open_orders()

    def apply_corporate_actions() -> dict:
        return corporate.apply(db)

    def reconcile_reminder() -> dict:
        return reconcile.remind(db, settings)

    def scan_ideas() -> dict:
        s = Scan(db)
        out = s.run()
        out["expired"] = s.expire()
        return out

    plan = [
        ("pull_calendar", j.pull_calendar, CronTrigger(hour=4, minute=50, timezone=PT)),
        ("pull_macro", j.pull_macro, CronTrigger(hour=5, minute=0, timezone=PT)),
        ("pull_tone", j.pull_tone, CronTrigger(hour="0-4", minute="0,30", timezone=PT)),
        ("build_universe", j.build_universe, CronTrigger(day_of_week="mon-fri", hour=5, minute=5, timezone=PT)),
        ("pull_bars", j.pull_bars, CronTrigger(hour=5, minute=15, timezone=PT)),
        # splits and dividends: pulled (production), then applied to every environment's books before the scan
        ("pull_corporate_actions", j.pull_corporate_actions, CronTrigger(hour=5, minute=20, timezone=PT)),
        ("apply_corporate_actions", apply_corporate_actions, CronTrigger(hour=5, minute=25, timezone=PT)),
        ("pull_news_earnings", j.pull_news_earnings, CronTrigger(hour=6, minute=0, timezone=PT)),
        ("pull_fundamentals", j.pull_fundamentals, CronTrigger(hour=6, minute=10, timezone=PT)),
        ("pull_chains", j.pull_chains, CronTrigger(day_of_week="mon-fri", hour=6, minute=12, timezone=PT)),
        ("scan_ideas", scan_ideas, CronTrigger(day_of_week="mon-fri", hour=6, minute=20, timezone=PT)),
        ("pull_quotes", j.pull_quotes, CronTrigger(day_of_week="mon-fri", hour="6-13", minute="0,30", timezone=PT)),
        # practice: fill open orders on the fresh quotes, then close the session (day orders, settlement, expiries)
        (
            "fill_orders",
            fill_orders,
            CronTrigger(day_of_week="mon-fri", hour="6-13", minute="3,33", timezone=PT),
        ),
        ("close_session", p.close_session, CronTrigger(day_of_week="mon-fri", hour=13, minute=5, timezone=PT)),
        # Claude: the queue drains one job at a time; the brief is queued at 05:30 PT and pushed when written
        ("claude_poll", claude_poll, IntervalTrigger(seconds=20)),
        ("morning_brief", morning_brief, CronTrigger(day_of_week="mon-fri", hour=5, minute=30, timezone=PT)),
        # phase 6: the ghost follows the AI book's flows; the AI trader enters after the scan and checks its exits
        # with the fresh quotes; options practice sells puts; approved "Invest this" plans are placed at the open;
        # price alerts are checked with every quote pull
        ("ghost_sync", ghost.sync, CronTrigger(minute="*/30", timezone=PT)),
        ("invest_place", planner.place_due, CronTrigger(day_of_week="mon-fri", hour=6, minute=35, timezone=PT)),
        ("ai_entries", ai.run_entries, CronTrigger(day_of_week="mon-fri", hour=6, minute=40, timezone=PT)),
        ("options_entries", options.run_entries, CronTrigger(day_of_week="mon-fri", hour=6, minute=45, timezone=PT)),
        ("ai_exits", exits, CronTrigger(day_of_week="mon-fri", hour="6-13", minute="5,35", timezone=PT)),
        ("alerts_check", alerts_check, CronTrigger(day_of_week="mon-fri", hour="6-13", minute="6,36", timezone=PT)),
        # phase 7: settle and learn after the close; on Sunday the test set, then the digest
        ("learning_run", learning_run, CronTrigger(day_of_week="mon-fri", hour=13, minute=15, timezone=PT)),
        ("weekly_eval", weekly_eval, CronTrigger(day_of_week="sun", hour=7, minute=0, timezone=PT)),
        ("weekly_digest", digest_and_push, CronTrigger(day_of_week="sun", hour=7, minute=30, timezone=PT)),
        # reconciliation: on the month's first trading day, a reminder to check the real book against the statement
        ("reconcile_reminder", reconcile_reminder, CronTrigger(day="1-7", hour=7, minute=0, timezone=PT)),
    ]
    if settings.ghostline_env == "staging":
        plan = [p for p in plan if p[0] not in PULLS]
    for job_id, fn, trigger in plan:
        if "day_of_week='mon-fri'" in str(trigger):
            fn = on_sessions(db, fn)
        scheduler.add_job(
            fn,
            trigger,
            id=job_id,
            name=job_id,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=600,
        )
    return [p[0] for p in plan]


def before_close(cal: Calendar, now: dt.datetime) -> bool:
    """Whether today's session has not closed yet. An early close (13:00 New York, 10:00 PT) ends it early."""
    today = now.astimezone(NEW_YORK).date()
    return cal.is_session(today) and now < cal.closes_at(today)


def on_sessions(db: Database, fn: Any) -> Any:
    """A weekday job is a session job: on a day the exchange is closed (a holiday) it does nothing and says why."""
    def run() -> Any:
        today = dt.datetime.now(NEW_YORK).date()
        if not Calendar.load(db).is_session(today):
            return {"skipped": f"the market is closed on {today.isoformat()}"}
        return fn()
    run.__name__ = getattr(fn, "__name__", "session_job")
    return run
