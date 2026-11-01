"""Your edge: what the owner does with the app's suggestions (reconciliation part 4; the owner, 2026-11-01).

Module weights and graduation keep learning from paper results only: every idea is paper-traded, so they measure
each module without the owner's choices in them. This measures the owner's side, per module and lane:

- took, declined, lapsed (a decision each) and still open; the take rate is took over decided;
- the real result of what was taken (closed lots only: profit over cost), and the execution gap: that real result
  against the idea's own paper result, where both exist;
- what the skipped ideas (declined or lapsed) did on paper, against SPY: negative means skipping avoided a loss.

Ranking: open ideas sort by score × the fit factor, 0.75 to 1.25 from the take rate with one took and one skip
assumed (so it is 1.00 until the owner decides, and moves slowly). Nothing is ever hidden or refused by it."""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from decimal import Decimal
from typing import Any

from ghostline import ledger, reconcile
from ghostline.db import Database
from ghostline.ledger import Trade

TWO = Decimal("0.01")
FIT_LOW, FIT_SPAN = Decimal("0.75"), Decimal("0.50")
NOTE = ("Module weights and graduation learn from paper results only; this shows what you do with the suggestions "
        "and only reorders open ideas.")


def fit_factor(took: int, decided: int) -> Decimal:
    """0.75 + 0.5 × (took + 1) / (decided + 2): 1.00 with no decision, bounded away from 0.75 and 1.25."""
    return FIT_LOW + FIT_SPAN * Decimal(took + 1) / Decimal(decided + 2)


def _real_pct_by_opening(rows: list[dict]) -> dict[int, Decimal]:
    """Closed lots' profit over their cost, in percent, per opening trade."""
    pnl: dict[int, Decimal] = defaultdict(Decimal)
    cost: dict[int, Decimal] = defaultdict(Decimal)
    closed, _ = ledger.settle([Trade.from_row(r) for r in rows])
    for c in closed:
        if c.opened_trade_ids and c.cost:
            pnl[c.opened_trade_ids[0]] += c.pnl
            cost[c.opened_trade_ids[0]] += c.cost
    return {k: pnl[k] / cost[k] * 100 for k in pnl if cost[k]}


def _avg(xs: list[Decimal]) -> str | None:
    return str((sum(xs, Decimal(0)) / len(xs)).quantize(TWO)) if xs else None


def compute(db: Database, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.UTC)
    d = reconcile.decisions(db)
    real_pct = _real_pct_by_opening(d["rows"])
    groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"took": 0, "declined": 0, "lapsed": 0, "open": 0, "real": [], "gap": [], "skipped": []})
    for i in db.fetch_all(
            "SELECT i.id, i.lane, i.module, i.expires_at, p.status AS paper_status, p.result_pct, p.excess_pct "
            "FROM ideas i LEFT JOIN paper_results p ON p.idea_id = i.id WHERE i.status <> 'refused'"):
        taken = d["took_idea"].get(i["id"], [])
        outcome = ("took" if taken else "declined" if ("idea", i["id"]) in d["declined"]
                   else "lapsed" if i["expires_at"] <= now else "open")
        g = groups[(i["module"], i["lane"])]
        g[outcome] += 1
        settled = i["paper_status"] == "settled"
        if outcome == "took":
            for t in taken:
                if t["id"] in real_pct:
                    g["real"].append(real_pct[t["id"]])
                    if settled and i["result_pct"] is not None:
                        g["gap"].append(real_pct[t["id"]] - Decimal(i["result_pct"]))
        elif outcome in ("declined", "lapsed") and settled and i["excess_pct"] is not None:
            g["skipped"].append(Decimal(i["excess_pct"]))
    out, totals = [], {"took": 0, "declined": 0, "lapsed": 0, "open": 0, "decided": 0}
    for (module, lane), g in sorted(groups.items()):
        decided = g["took"] + g["declined"] + g["lapsed"]
        for k in ("took", "declined", "lapsed", "open"):
            totals[k] += g[k]
        totals["decided"] += decided
        out.append({"module": module, "lane": lane, "took": g["took"], "declined": g["declined"],
                    "lapsed": g["lapsed"], "open": g["open"], "decided": decided,
                    "take_rate": str((Decimal(g["took"]) / decided).quantize(TWO)) if decided else None,
                    "fit": str(fit_factor(g["took"], decided).quantize(Decimal("0.0001"))),
                    "real_pct_avg": _avg(g["real"]), "real_n": len(g["real"]),
                    "gap_pct_avg": _avg(g["gap"]), "gap_n": len(g["gap"]),
                    "skipped_excess_avg": _avg(g["skipped"]), "skipped_n": len(g["skipped"])})
    return {"groups": out, "totals": totals, "note": NOTE}


def fit_factors(db: Database, now: dt.datetime | None = None) -> dict[tuple[str, str], Decimal]:
    return {(g["module"], g["lane"]): Decimal(g["fit"]) for g in compute(db, now)["groups"]}
