"""Post-earnings announcement drift, from vendor/tradermonty (pead_strategy.md, entry_exit_rules.md): a 3%+ earnings
gap, then on weekly candles a red pullback candle; the entry is a green week closing above that red candle's high,
the stop its low, the target 2R. Five weeks after earnings the setup expires."""

from __future__ import annotations

import datetime as dt

from ghostline.modules import Signal, floats, newest
from ghostline.modules import trend as T

MODULE = "pead"
WATCH_WEEKS = 5


def earnings_gap(bars: list[dict], when: dt.date) -> tuple[float | None, int | None, str]:
    """The gap around the report date: before the open (open vs the prior close) or after the close (the next open
    vs that close); the timing is unknown, so the larger move names itself."""
    idx = next((i for i, b in enumerate(bars) if b["ts"].date() >= when), None)
    if idx is None or idx == 0:
        return None, None, "no bar around the report date"
    bmo = T.pct(float(bars[idx]["open"]), float(bars[idx - 1]["close"]))
    amc = T.pct(float(bars[idx + 1]["open"]), float(bars[idx]["close"])) if idx + 1 < len(bars) else None
    if amc is not None and abs(amc) > abs(bmo):
        return amc, idx + 1, "amc"
    return bmo, idx, "bmo"


def signal(ticker: str, bars: list[dict], earnings: list[dict]) -> Signal | None:
    """None when there is no reported quarter inside the watch window (nothing to say)."""
    source, as_of = newest(bars)
    if len(bars) < 30:
        return None
    last_day = bars[-1]["ts"].date()
    recent = [
        e for e in earnings if e.get("actual") is not None and 0 <= (last_day - e["at"]).days <= WATCH_WEEKS * 7 + 3
    ]
    if not recent:
        return None
    report = max(recent, key=lambda e: e["at"])
    gap, gap_idx, timing = earnings_gap(bars, report["at"])
    ev: dict = {
        "report_date": report["at"].isoformat(),
        "gap_pct": None if gap is None else round(gap, 2),
        "timing": timing,
    }
    if gap is None or gap < 3:
        return Signal(
            ticker,
            MODULE,
            "swing",
            "long",
            0,
            30,
            f"{ticker} reported on {report['at']} without a 3% gap up ({'no gap' if gap is None else f'{gap:.1f}%'})",
            evidence=ev,
            source=source,
            as_of=as_of,
        ).clamp()
    weeks = T.weekly(bars[gap_idx:])
    weeks_since = (last_day - report["at"]).days // 7
    red = None
    for w in weeks[1:] if len(weeks) > 1 else []:
        if w["close"] < w["open"]:
            red = w  # the most recent red candle sets the levels
    price = floats(bars, "close")[-1]
    current = weeks[-1] if weeks else None
    if weeks_since > WATCH_WEEKS:
        stage, score = "expired", 0.0
    elif red is None:
        stage, score = "monitoring", 50.0
    elif current and current["close"] >= current["open"] and price > red["high"] and current is not red:
        stage, score = "breakout", 85.0
    else:
        stage, score = "signal_ready", 65.0
    if gap >= 5 and stage != "expired":
        score += 10
    entry = stop = target = None
    if red:
        entry, stop = red["high"], red["low"]
        target = entry + 2 * (entry - stop)
        v4 = T.avg([w["volume"] for w in weeks[-5:-1]]) or 0
        if stage == "breakout" and current and v4 and current["volume"] > v4:
            score += 5
        ev.update(
            {
                "red_candle": {
                    "week_start": red["start"].date().isoformat(),
                    "high": round(red["high"], 4),
                    "low": round(red["low"], 4),
                }
            }
        )
    ev.update({"stage": stage, "weeks_since_earnings": weeks_since, "price": round(price, 4)})
    why = {
        "expired": f"{ticker}: the five-week window after the {report['at']} report has passed",
        "monitoring": f"{ticker} gapped {gap:.1f}% on earnings; no red weekly candle yet",
        "signal_ready": f"{ticker} gapped {gap:.1f}% on earnings and pulled back; buy a weekly close above {entry:.2f}"
        if entry
        else "",
        "breakout": f"{ticker} broke above its post-earnings red candle ({entry:.2f}) on a green week" if entry else "",
    }[stage]
    return Signal(
        ticker,
        MODULE,
        "swing",
        "long",
        score,
        30,
        why,
        entry=entry and round(entry, 4),
        stop=stop and round(stop, 4),
        target=target and round(target, 4),
        actionable=stage == "breakout",
        evidence=ev,
        source=source,
        as_of=as_of,
    ).clamp()
