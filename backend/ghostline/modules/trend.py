"""Shared price arithmetic for the modules: moving averages, 52-week range, swings, weekly candles, relative
strength against SPY, Minervini's trend template. Pure functions over bar dicts (ts, open, high, low, close, volume)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from ghostline import indicators
from ghostline.modules import floats


def last(values: list[float | None]) -> float | None:
    for v in reversed(values):
        if v is not None:
            return v
    return None


def pct(a: float, b: float) -> float:
    """a against b, in percent: (a / b − 1) × 100."""
    return (a / b - 1.0) * 100.0 if b else 0.0


def avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def range_52w(bars: list[dict]) -> tuple[float, float]:
    window = bars[-252:]
    return max(floats(window, "high")), min(floats(window, "low"))


def atr(bars: list[dict], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        h, lo, pc = float(bars[i]["high"]), float(bars[i]["low"]), float(bars[i - 1]["close"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return avg(trs[-n:])


@dataclass(frozen=True)
class Swing:
    index: int
    kind: str  # high | low
    price: float


def swings(bars: list[dict], window: int = 5) -> list[Swing]:
    """Alternating swing highs and lows: a bar whose high (low) is the highest (lowest) of the bars `window` either
    side. Two highs in a row keep the higher, two lows the lower."""
    highs, lows = floats(bars, "high"), floats(bars, "low")
    out: list[Swing] = []
    for i in range(window, len(bars) - window):
        if highs[i] == max(highs[i - window : i + window + 1]):
            cand = Swing(i, "high", highs[i])
        elif lows[i] == min(lows[i - window : i + window + 1]):
            cand = Swing(i, "low", lows[i])
        else:
            continue
        if out and out[-1].kind == cand.kind:
            better = cand.price > out[-1].price if cand.kind == "high" else cand.price < out[-1].price
            if better:
                out[-1] = cand
            continue
        out.append(cand)
    return out


def weekly(bars: list[dict]) -> list[dict]:
    """Weekly candles from daily bars (ISO weeks): open of the first day, close of the last, high, low, volume."""
    out: list[dict] = []
    for b in bars:
        ts: dt.datetime = b["ts"]
        key = ts.isocalendar()[:2]
        if out and out[-1]["week"] == key:
            w = out[-1]
            w["high"] = max(w["high"], float(b["high"]))
            w["low"] = min(w["low"], float(b["low"]))
            w["close"] = float(b["close"])
            w["volume"] += float(b["volume"])
            w["end"] = ts
        else:
            out.append(
                {
                    "week": key,
                    "start": ts,
                    "end": ts,
                    "open": float(b["open"]),
                    "high": float(b["high"]),
                    "low": float(b["low"]),
                    "close": float(b["close"]),
                    "volume": float(b["volume"]),
                }
            )
    return out


def relative_strength(bars: list[dict], spy: list[dict]) -> float | None:
    """Minervini-weighted return against SPY: 40% of the last 3 months, 20% each of 6, 9 and 12 months."""
    closes, bench = floats(bars, "close"), floats(spy, "close")
    if len(closes) < 253 or len(bench) < 253:
        return None
    total = 0.0
    for weight, n in ((0.4, 63), (0.2, 126), (0.2, 189), (0.2, 252)):
        total += weight * (pct(closes[-1], closes[-1 - n]) - pct(bench[-1], bench[-1 - n]))
    return total


def rs_score(rs: float | None) -> float | None:
    if rs is None:
        return None
    for floor, score in ((50, 100), (30, 95), (20, 90), (10, 80), (5, 70), (0, 60), (-5, 50), (-10, 40), (-20, 20)):
        if rs >= floor:
            return float(score)
    return 0.0


def trend_template(bars: list[dict], spy: list[dict]) -> tuple[int, dict[str, bool | None]]:
    """Minervini's 7 points; the count passed and each check (None when it cannot be measured)."""
    closes = floats(bars, "close")
    checks: dict[str, bool | None] = {}
    if len(closes) < 260:
        return 0, {"enough_history": False}
    s50, s150, s200 = last(indicators.sma(closes, 50)), last(indicators.sma(closes, 150)), indicators.sma(closes, 200)
    s200_now, s200_then = s200[-1], s200[-23]
    price = closes[-1]
    hi, lo = range_52w(bars)
    rs = relative_strength(bars, spy)
    checks["above_150_and_200"] = price > s150 and price > s200_now
    checks["150_above_200"] = s150 > s200_now
    checks["200_rising_22d"] = s200_now > s200_then
    checks["above_50"] = price > s50
    checks["25pct_above_52w_low"] = pct(price, lo) >= 25
    checks["within_25pct_of_52w_high"] = pct(price, hi) >= -25
    checks["rs_above_70"] = None if rs is None else (rs_score(rs) or 0) >= 70
    return sum(1 for v in checks.values() if v), checks
