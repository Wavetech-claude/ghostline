"""The worker's schedules, Pacific time (the plan): macro and the pre-market scan around 05:30, holdings checks every
30 minutes while the market is open, settlement and lessons after the 13:00 close, a weekly digest on Sunday."""

from __future__ import annotations

from typing import Any

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ghostline import alerts, storage
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
from ghostline.market.jobs import Jobs
from ghostline.practice import Practice

PT = "America/Los_Angeles"


def register(
    scheduler: Any,
    db: Database,
    jobs: Jobs | None = None,
    practice: Practice | None = None,
    settings: Settings | None = None,
    claude: ClaudeJobs | None = None,
) -> list[str]:
    """Add every job; returns their ids (tests read them). Each job is misfire-tolerant and never overlaps itself."""
    j = jobs or Jobs(db)
    p = practice or Practice(db)
    settings = settings or load()
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

    def scan_ideas() -> dict:
        s = Scan(db)
        out = s.run()
        out["expired"] = s.expire()
        return out

    plan = [
        ("pull_macro", j.pull_macro, CronTrigger(hour=5, minute=0, timezone=PT)),
        # GDELT sees staging and production as one address (both run on prod): they ask at different minutes
        ("pull_tone", j.pull_tone,
         CronTrigger(hour="0-4", minute="15,45" if settings.ghostline_env == "staging" else "0,30", timezone=PT)),
        ("build_universe", j.build_universe, CronTrigger(day_of_week="mon-fri", hour=5, minute=5, timezone=PT)),
        ("pull_bars", j.pull_bars, CronTrigger(hour=5, minute=15, timezone=PT)),
        ("pull_news_earnings", j.pull_news_earnings, CronTrigger(hour=6, minute=0, timezone=PT)),
        ("pull_fundamentals", j.pull_fundamentals, CronTrigger(hour=6, minute=10, timezone=PT)),
        ("pull_chains", j.pull_chains, CronTrigger(day_of_week="mon-fri", hour=6, minute=12, timezone=PT)),
        ("scan_ideas", scan_ideas, CronTrigger(day_of_week="mon-fri", hour=6, minute=20, timezone=PT)),
        ("pull_quotes", j.pull_quotes, CronTrigger(day_of_week="mon-fri", hour="6-13", minute="0,30", timezone=PT)),
        # practice: fill open orders on the fresh quotes, then close the session (day orders, settlement, expiries)
        (
            "fill_orders",
            p.fill_open_orders,
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
    ]
    for job_id, fn, trigger in plan:
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
