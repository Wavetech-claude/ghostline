"""The weekly test set: up to twelve questions whose answers are stored numbers (a ticker's last close, its 52-week
high, a book's cash, how many ideas a lane holds, a module's weight). Claude gets the same kind of facts bundle it
reads for chat and must answer from it; an answer is right when it carries the stored number (at any rounding). The
score shows whether the model reads the stored data faithfully, week after week; it never sees the score."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from ghostline.claude import grounding
from ghostline.db import Database
from ghostline.market import store
from ghostline.practice import Practice

MAX_QUESTIONS = 12
_NUM = re.compile(r"[-+]?\$?\d[\d,]*(?:\.\d+)?")


def questions(db: Database) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(questions with their expected numbers, the facts bundle that holds every answer)."""
    p = Practice(db)
    qs: list[dict[str, Any]] = []
    facts: dict[str, Any] = {"tickers": {}, "books": {}, "ideas_open_by_lane": {}, "module_weights": {}}
    tickers = [t for t in store.scan_tickers(db) if t not in store.MARKET_TICKERS] + ["SPY"]
    for t in tickers:
        bars = store.bars(db, t, "1D", limit=252)
        if len(bars) < 2:
            continue
        last = bars[-1]
        high = max(Decimal(b["high"]) for b in bars)
        facts["tickers"][t] = {
            "last_close": str(last["close"]),
            "last_bar_date": last["ts"].date().isoformat(),
            "high_52w": str(high),
            "source": last["source"],
        }
        qs.append({"question": f"What was {t}'s most recent stored daily close?", "expected": Decimal(last["close"])})
        if len([q for q in qs if "highest" in q["question"]]) < 2:
            qs.append({"question": f"What is {t}'s highest daily high in the stored 52 weeks?", "expected": high})
        if len(qs) >= 6:
            break
    for kind in ("practice", "ai"):
        cash = p.facts(p.book_id(kind)).cash
        facts["books"][kind] = {"cash": str(cash)}
        qs.append(
            {
                "question": f"How much cash does the {'practice' if kind == 'practice' else 'AI trader'} book hold?",
                "expected": cash,
            }
        )
    for lane, name in (("swing", "swing"), ("long", "long-term")):
        n = db.fetch_one(
            "SELECT count(*) AS n FROM ideas WHERE lane = %s AND status = 'open' AND expires_at > now()", (lane,)
        )["n"]
        facts["ideas_open_by_lane"][lane] = int(n)
        qs.append({"question": f"How many ideas are open in the {name} lane?", "expected": Decimal(int(n))})
    for m in db.fetch_all("SELECT module, weight FROM module_weights ORDER BY module LIMIT 2"):
        facts["module_weights"][m["module"]] = str(m["weight"])
        qs.append({"question": f"What is the {m['module']} module's current weight?", "expected": Decimal(m["weight"])})
    qs = qs[:MAX_QUESTIONS]
    for i, q in enumerate(qs, 1):
        q["n"] = i
    return qs, facts


def numbers(text: str) -> list[Decimal]:
    out = []
    for tok in _NUM.findall(text or ""):
        try:
            out.append(Decimal(tok.replace(",", "").replace("$", "")))
        except InvalidOperation:
            continue
    return out


def correct(answer: str, expected: Decimal) -> bool:
    """Right when a number in the answer is the stored one (any rounding to 0–4 places, as grounding accepts)."""
    got = numbers(answer)
    if expected == expected.to_integral_value() and abs(expected) < 1000:
        return any(g == expected for g in got)  # a count must be exact
    return any(grounding.matches(g, {expected}) for g in got)


def score(qs: list[dict[str, Any]], answers: dict[int, str]) -> dict[str, Any]:
    results = []
    for q in qs:
        a = answers.get(q["n"], "")
        results.append(
            {
                "n": q["n"],
                "question": q["question"],
                "expected": str(q["expected"]),
                "answer": a,
                "correct": correct(a, q["expected"]),
            }
        )
    right = sum(1 for r in results if r["correct"])
    total = len(results)
    return {
        "questions": total,
        "correct": right,
        "score_pct": (Decimal(right) / total * 100).quantize(Decimal("0.01")) if total else Decimal(0),
        "results": results,
    }
