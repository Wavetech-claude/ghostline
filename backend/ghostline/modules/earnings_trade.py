"""The earnings-reaction score, from vendor/tradermonty (earnings-trade-analyzer scoring_methodology.md): gap size,
the 20-day trend into the report, volume 20d/60d, the 200-day and 50-day positions; A/B/C/D grades."""

from __future__ import annotations

from ghostline import indicators
from ghostline.modules import Signal, floats, newest
from ghostline.modules import trend as T
from ghostline.modules.pead import earnings_gap

MODULE = "earnings_trade"


def _table(value: float, steps: list[tuple[float, float]], floor: float) -> float:
    for at_least, score in steps:
        if value >= at_least:
            return score
    return floor


def signal(ticker: str, bars: list[dict], earnings: list[dict]) -> Signal | None:
    source, as_of = newest(bars)
    if len(bars) < 221:
        return None
    last_day = bars[-1]["ts"].date()
    recent = [e for e in earnings if e.get("actual") is not None and 0 <= (last_day - e["at"]).days <= 12]
    if not recent:
        return None
    report = max(recent, key=lambda e: e["at"])
    gap, gap_idx, timing = earnings_gap(bars, report["at"])
    if gap is None or gap_idx is None or gap_idx < 61:
        return None
    closes, vols = floats(bars, "close"), floats(bars, "volume")
    pre = closes[gap_idx - 1]
    trend20 = T.pct(pre, closes[gap_idx - 21])
    vol_ratio = (T.avg(vols[gap_idx - 20 : gap_idx]) or 0) / (T.avg(vols[gap_idx - 60 : gap_idx]) or 1)
    price = closes[-1]
    s200, s50 = T.last(indicators.sma(closes, 200)), T.last(indicators.sma(closes, 50))
    d200, d50 = T.pct(price, s200 or price), T.pct(price, s50 or price)
    scores = {
        "gap": _table(abs(gap), [(10, 100), (7, 85), (5, 70), (3, 55), (1, 35)], 15),
        "trend": _table(trend20, [(15, 100), (10, 85), (5, 70), (0, 50), (-5, 30)], 15),
        "volume": _table(vol_ratio, [(2, 100), (1.5, 80), (1.2, 60), (1, 40)], 20),
        "ma200": _table(d200, [(20, 100), (10, 85), (5, 70), (0, 55), (-5, 35)], 15),
        "ma50": _table(d50, [(10, 100), (5, 80), (0, 60), (-5, 35)], 15),
    }
    composite = (
        scores["gap"] * 0.25
        + scores["trend"] * 0.30
        + scores["volume"] * 0.20
        + scores["ma200"] * 0.15
        + scores["ma50"] * 0.10
    )
    grade = "A" if composite >= 85 else "B" if composite >= 70 else "C" if composite >= 55 else "D"
    direction = "long" if gap > 0 else "neutral"
    stop = price * 0.93
    ev = {
        "report_date": report["at"].isoformat(),
        "gap_pct": round(gap, 2),
        "timing": timing,
        "pre_earnings_20d_pct": round(trend20, 2),
        "volume_ratio_20_60": round(vol_ratio, 3),
        "vs_ma200_pct": round(d200, 2),
        "vs_ma50_pct": round(d50, 2),
        "scores": scores,
        "grade": grade,
    }
    why = (
        f"{ticker}: earnings reaction grade {grade} ({composite:.0f}), gap {gap:+.1f}% on {report['at']}, "
        f"{trend20:+.1f}% into the report, volume {vol_ratio:.2f}× its 60-day pace"
    )
    return Signal(
        ticker,
        MODULE,
        "swing",
        direction,
        composite,
        20,
        why,
        entry=round(price, 4),
        stop=round(stop, 4),
        target=round(price + 2 * (price - stop), 4),
        actionable=gap > 0 and grade in ("A", "B") and price >= 10,
        evidence=ev,
        source=source,
        as_of=as_of,
    ).clamp()
