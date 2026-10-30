"""The journal and the learning loop. Every idea is paper-traded at the next open after it was made and settled at the
next open on or after its horizon, against SPY over the same sessions, after estimated costs (a setting, in basis
points per fill). Results drive three things, and nothing else drives them:

- module weights: the mean excess return per settled idea (expectancy) moves a module's weight, pulled toward equal
  weight until the module has `weight_full_n` settled ideas; the scan multiplies a module's scores by its weight;
- module graduation: `graduation_n` settled ideas with positive expectancy marks the module's ideas live;
- lane graduation: the same test per lane (long, swing, puts) opens "copy with real money" for that lane.

Put ideas settle on the premium kept against the strike × 100 of collateral: the premium, less what the put would
cost to settle at the exit (the strike minus the stock's open, when below the strike). An idea with no stored bar ten
days after its open is marked unpriced: never settled on a guess."""

from __future__ import annotations

import datetime as dt
import json
import logging
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from ghostline import occ
from ghostline.claude.jobs import enqueue
from ghostline.db import Database
from ghostline.market.calendar import WEEKDAYS, Calendar

logger = logging.getLogger("ghostline.learning")
PT = ZoneInfo("America/Los_Angeles")
FUND = "SPY"
PCT = Decimal("0.0001")
UNPRICED_AFTER_DAYS = 10
LESSONS_PER_RUN = 10
LANES = ("long", "swing", "puts")


def settings(db: Database) -> dict[str, Decimal]:
    rows = db.fetch_all("SELECT key, value FROM learning_settings")
    out = {"graduation_n": Decimal(30), "cost_bps_per_side": Decimal(10), "weight_full_n": Decimal(20)}
    out.update({r["key"]: Decimal(r["value"]) for r in rows})
    return out


def next_open(made: dt.datetime, cal: Calendar = WEEKDAYS) -> dt.date:
    """The session whose open follows the moment the idea was made (a 06:20 PT scan trades at that day's open)."""
    day = made.astimezone(PT).date()
    if cal.is_session(day) and made < cal.opens_at(day):
        return day
    return cal.next_session(day)


def raw_weight(expectancy: Decimal) -> Decimal:
    """+5% mean excess doubles a weight; -3.75% or worse floors it at a quarter (a module is damped, never muted)."""
    return max(Decimal("0.25"), Decimal(1) + expectancy / 5)


def blended_weight(expectancy: Decimal | None, n: int, full_n: Decimal) -> Decimal:
    if expectancy is None or n == 0:
        return Decimal(1)
    blend = min(Decimal(1), Decimal(n) / full_n) if full_n > 0 else Decimal(1)
    return (Decimal(1) + blend * (raw_weight(expectancy) - 1)).quantize(PCT)


def graduates(n: int, expectancy: Decimal | None, needed: Decimal) -> bool:
    return n >= needed and expectancy is not None and expectancy > 0


class Learning:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.s = settings(db)

    # ── prices ──
    def _open_on_or_after(self, ticker: str, day: dt.date) -> tuple[Decimal, dt.date] | None:
        row = self.db.fetch_one(
            "SELECT open, ts FROM market_bars WHERE ticker = %s AND timeframe = '1D' "
            "AND ts::date >= %s ORDER BY ts LIMIT 1",
            (ticker, day),
        )
        return (Decimal(row["open"]), row["ts"].date()) if row else None

    # ── the journal ──
    def open_results(self) -> int:
        """A pending result for every idea that has none yet: every idea the scan made, passed or refused (the
        module's call is what is measured; the owner's rules decide what the owner trades)."""
        rows = self.db.fetch_all(
            "SELECT i.id, i.ticker, i.lane, i.module, i.created_at, s.horizon_days FROM ideas i "
            "JOIN signals s ON s.id = i.signal_id LEFT JOIN paper_results p ON p.idea_id = i.id WHERE p.idea_id IS NULL"
        )
        cal = Calendar.load(self.db)
        for r in rows:
            opened = next_open(r["created_at"], cal)
            closes = opened + dt.timedelta(days=int(r["horizon_days"] or 0))
            self.db.execute(
                "INSERT INTO paper_results (idea_id, ticker, lane, module, opened_on, closes_on) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (idea_id) DO NOTHING",
                (r["id"], r["ticker"], r["lane"], r["module"], opened, closes),
            )
        return len(rows)

    def settle(self, today: dt.date | None = None) -> dict[str, int]:
        today = today or dt.datetime.now(dt.UTC).astimezone(PT).date()
        costs = 2 * self.s["cost_bps_per_side"] / 100  # percent, both fills
        out = {"settled": 0, "waiting": 0, "unpriced": 0}
        for r in self.db.fetch_all(
            "SELECT p.*, i.entry AS idea_entry, i.option_symbol FROM paper_results p "
            "JOIN ideas i ON i.id = p.idea_id WHERE p.status = 'pending' AND p.closes_on <= %s "
            "ORDER BY p.closes_on",
            (today,),
        ):
            under = r["ticker"]
            entry = self._open_on_or_after(under, r["opened_on"])
            spy_in = self._open_on_or_after(FUND, r["opened_on"])
            exit_ = self._open_on_or_after(under, r["closes_on"])
            spy_out = self._open_on_or_after(FUND, r["closes_on"])
            if not (entry and spy_in and exit_ and spy_out):
                if (today - r["closes_on"]).days > UNPRICED_AFTER_DAYS:
                    self.db.execute(
                        "UPDATE paper_results SET status = 'unpriced', note = %s WHERE idea_id = %s",
                        (f"no stored open for {under} or {FUND} within {UNPRICED_AFTER_DAYS} days", r["idea_id"]),
                    )
                    out["unpriced"] += 1
                else:
                    out["waiting"] += 1
                continue
            (e, e_day), (x, x_day), (si, _), (so, _) = entry, exit_, spy_in, spy_out
            if r["lane"] == "puts":
                if not r.get("option_symbol") or r.get("idea_entry") is None:
                    self.db.execute(
                        "UPDATE paper_results SET status = 'unpriced', note = 'a put idea without its option "
                        "or premium' WHERE idea_id = %s",
                        (r["idea_id"],),
                    )
                    out["unpriced"] += 1
                    continue
                strike = occ.parse(r["option_symbol"]).strike
                premium = Decimal(r["idea_entry"])
                kept = premium - max(Decimal(0), strike - x)
                result = kept / strike * 100 - costs
                note = f"premium {premium}, strike {strike}, stock {x} at the exit"
            else:
                result = (x / e - 1) * 100 - costs
                note = None
            spy = (so / si - 1) * 100
            self.db.execute(
                "UPDATE paper_results SET status = 'settled', entry = %s, exit = %s, entry_date = %s, exit_date = %s, "
                "result_pct = %s, spy_entry = %s, spy_exit = %s, spy_pct = %s, costs_pct = %s, excess_pct = %s, "
                "note = %s, settled_at = now() WHERE idea_id = %s",
                (
                    e,
                    x,
                    e_day,
                    x_day,
                    result.quantize(PCT),
                    si,
                    so,
                    spy.quantize(PCT),
                    costs.quantize(PCT),
                    (result - spy).quantize(PCT),
                    note,
                    r["idea_id"],
                ),
            )
            out["settled"] += 1
        return out

    # ── what the results drive ──
    def update_weights(self) -> list[dict]:
        rows = self.db.fetch_all(
            "SELECT m.module, count(p.idea_id) FILTER (WHERE p.status = 'settled') AS n, "
            "count(p.idea_id) FILTER (WHERE p.status = 'settled' AND p.excess_pct > 0) AS wins, "
            "avg(p.excess_pct) FILTER (WHERE p.status = 'settled') AS expectancy "
            "FROM (SELECT DISTINCT module FROM signals WHERE lane <> 'macro') m "
            "LEFT JOIN paper_results p ON p.module = m.module GROUP BY m.module"
        )
        out = []
        for r in rows:
            n = int(r["n"] or 0)
            exp = Decimal(r["expectancy"]).quantize(PCT) if r["expectancy"] is not None else None
            w = blended_weight(exp, n, self.s["weight_full_n"])
            grad = graduates(n, exp, self.s["graduation_n"])
            self.db.execute(
                "INSERT INTO module_weights (module, n, wins, expectancy, weight, graduated, updated_at) VALUES "
                "(%s, %s, %s, %s, %s, %s, now()) ON CONFLICT (module) DO UPDATE SET n = EXCLUDED.n, "
                "wins = EXCLUDED.wins, "
                "expectancy = EXCLUDED.expectancy, weight = EXCLUDED.weight, graduated = EXCLUDED.graduated, "
                "updated_at = now()",
                (r["module"], n, int(r["wins"] or 0), exp, w, grad),
            )
            out.append({"module": r["module"], "n": n, "expectancy": exp, "weight": w, "graduated": grad})
        return out

    def update_lanes(self) -> list[dict]:
        out = []
        for lane in LANES:
            r = self.db.fetch_one(
                "SELECT count(*) AS n, avg(excess_pct) AS expectancy FROM paper_results "
                "WHERE lane = %s AND status = 'settled'",
                (lane,),
            )
            n = int(r["n"] or 0)
            exp = Decimal(r["expectancy"]).quantize(PCT) if r["expectancy"] is not None else None
            grad = graduates(n, exp, self.s["graduation_n"])
            self.db.execute(
                "UPDATE lane_graduation SET settled_n = %s, expectancy = %s, positive_expectancy = %s, "
                "graduated = %s, updated_at = now() WHERE lane = %s",
                (n, exp, bool(exp is not None and exp > 0), grad, lane),
            )
            out.append({"lane": lane, "n": n, "expectancy": exp, "graduated": grad})
        return out

    def queue_lessons(self) -> int:
        """One Claude lesson per newly settled idea, at most ten a run (the rest follow on the next run)."""
        rows = self.db.fetch_all(
            "SELECT p.idea_id FROM paper_results p LEFT JOIN lessons l ON l.idea_id = p.idea_id "
            "WHERE p.status = 'settled' AND l.idea_id IS NULL AND NOT EXISTS (SELECT 1 FROM claude_jobs j WHERE "
            "j.kind = 'lesson' AND j.status IN ('queued', 'running') AND (j.payload->>'idea_id')::bigint = p.idea_id) "
            "ORDER BY p.settled_at LIMIT %s",
            (LESSONS_PER_RUN,),
        )
        for r in rows:
            enqueue(self.db, "lesson", {"idea_id": int(r["idea_id"])})
        return len(rows)

    def run(self, today: dt.date | None = None) -> dict[str, Any]:
        """After the close: open results for new ideas, settle what is due, move weights and graduations, queue
        lessons."""
        opened = self.open_results()
        settled = self.settle(today)
        return {
            "opened": opened,
            **settled,
            "modules": self.update_weights(),
            "lanes": self.update_lanes(),
            "lessons_queued": self.queue_lessons(),
        }

    def weights(self) -> dict[str, Decimal]:
        return {
            r["module"]: Decimal(r["weight"]) for r in self.db.fetch_all("SELECT module, weight FROM module_weights")
        }


# ── the weekly digest (written in code: every number is a stored one) ───────────────────────────────────────────
def weekly_digest(db: Database, today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.datetime.now(dt.UTC).astimezone(PT).date()
    week_of = today - dt.timedelta(days=today.weekday())  # the Monday of this week (Sunday covers Mon-Sun)
    end = week_of + dt.timedelta(days=6)
    settled = db.fetch_all(
        "SELECT ticker, lane, module, result_pct, spy_pct, excess_pct FROM paper_results "
        "WHERE status = 'settled' AND settled_at::date BETWEEN %s AND %s ORDER BY excess_pct DESC",
        (week_of, end),
    )
    modules = db.fetch_all("SELECT module, n, expectancy, weight, graduated FROM module_weights ORDER BY module")
    lanes = db.fetch_all("SELECT lane, settled_n, expectancy, graduated FROM lane_graduation ORDER BY lane")
    ev = db.fetch_one("SELECT questions, correct, score_pct, at FROM eval_runs ORDER BY id DESC LIMIT 1")
    lines = [f"# Week of {week_of.isoformat()}", ""]
    if settled:
        wins = sum(1 for r in settled if r["excess_pct"] is not None and r["excess_pct"] > 0)
        lines += [f"## Settled this week: {len(settled)} ideas, {wins} beat SPY", ""]
        for r in settled[:10]:
            lines.append(
                f"- {r['ticker']} ({r['module']}, {r['lane']}): {r['result_pct']}% vs SPY {r['spy_pct']}% "
                f"→ {r['excess_pct']}%"
            )
    else:
        lines += ["## Settled this week: none", ""]
    lines += ["", "## Modules"]
    lines += [
        f"- {m['module']}: {m['n']} settled, expectancy {m['expectancy'] if m['expectancy'] is not None else '—'}%, "
        f"weight {m['weight']}{', graduated' if m['graduated'] else ''}"
        for m in modules
    ] or ["- none yet"]
    lines += ["", "## Lanes"]
    lines += [
        f"- {lane['lane']}: {lane['settled_n']} settled, expectancy "
        f"{lane['expectancy'] if lane['expectancy'] is not None else '—'}%"
        f"{' — graduated' if lane['graduated'] else ''}"
        for lane in lanes
    ]
    if ev:
        lines += [
            "",
            f"## Test set: {ev['correct']} of {ev['questions']} right ({ev['score_pct']}%), {ev['at']:%Y-%m-%d}",
        ]
    content = "\n".join(lines)
    facts = {
        "week_of": week_of.isoformat(),
        "settled": len(settled),
        "modules": [{k: str(v) if isinstance(v, Decimal) else v for k, v in m.items()} for m in modules],
        "lanes": [{k: str(v) if isinstance(v, Decimal) else v for k, v in lane.items()} for lane in lanes],
    }
    row = db.fetch_one(
        "INSERT INTO weekly_digests (week_of, content, facts) VALUES (%s, %s, %s::jsonb) "
        "ON CONFLICT (week_of) DO UPDATE SET content = EXCLUDED.content, facts = EXCLUDED.facts, "
        "created_at = now() RETURNING id",
        (week_of, content, json.dumps(facts, default=str)),
    )
    with db.connection() as conn:
        conn.commit()
    return {"id": int(row["id"]), "week_of": week_of.isoformat(), "content": content}
