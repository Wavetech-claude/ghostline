"""The macro regime, from vendor/tradermonty (regime_detection_methodology.md): monthly cross-asset ratios, their
6- and 12-month averages, crossovers and momentum, weighted into a transition score and a named regime. The yield
curve comes from FRED (DGS10 − DGS2); the ratios from the stored ETF bars."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ghostline.modules import Signal

MODULE = "macro_regime"
ETFS = ("SPY", "RSP", "IWM", "HYG", "LQD", "TLT", "XLY", "XLP")
COMPONENTS = {  # name → (numerator, denominator, weight, up-direction label, down-direction label)
    "concentration": ("RSP", "SPY", 25, "broadening", "concentrating"),
    "credit": ("HYG", "LQD", 15, "easing", "tightening"),
    "size": ("IWM", "SPY", 15, "small_cap_leading", "large_cap_leading"),
    "equity_bond": ("SPY", "TLT", 15, "risk_on", "risk_off"),
    "sector": ("XLY", "XLP", 10, "risk_on", "risk_off"),
}


def monthly(points: list[tuple[dt.date, float]]) -> list[tuple[dt.date, float]]:
    """The last value of each calendar month."""
    out: dict[tuple[int, int], tuple[dt.date, float]] = {}
    for d, v in points:
        out[(d.year, d.month)] = (d, v)
    return [out[k] for k in sorted(out)]


def monthly_ratio(num: list[dict], den: list[dict]) -> list[tuple[dt.date, float]]:
    by_day = {b["ts"].date(): float(b["close"]) for b in den}
    pts = [
        (b["ts"].date(), float(b["close"]) / by_day[b["ts"].date()])
        for b in num
        if b["ts"].date() in by_day and by_day[b["ts"].date()]
    ]
    return monthly(pts)


def _sma(values: list[float], n: int) -> list[float | None]:
    return [sum(values[i - n + 1 : i + 1]) / n if i + 1 >= n else None for i in range(len(values))]


def component(series: list[tuple[dt.date, float]]) -> dict[str, Any]:
    """The three layers on one monthly series: crossover (0–40), momentum shift (0–30), confirmation (0–30)."""
    values = [v for _, v in series]
    if len(values) < 13:
        return {"score": None, "direction": "unknown", "months": len(values)}
    s6, s12 = _sma(values, 6), _sma(values, 12)
    gaps = [(a - b) / b for a, b in zip(s6, s12, strict=True) if a is not None and b is not None]
    cross_score, cross_age, cross = 0.0, None, None
    for age in range(1, min(7, len(gaps))):
        now, before = gaps[-age], gaps[-age - 1]
        if before <= 0 < now:
            cross, cross_age = "golden", age
            break
        if before >= 0 > now:
            cross, cross_age = "death", age
            break
    if cross:
        cross_score = 40 if cross_age <= 2 else 20
    elif abs(gaps[-1]) < 0.01:
        cross_score = 25 * (1 - abs(gaps[-1]) / 0.01)
    roc3 = (values[-1] / values[-4] - 1) * 100
    roc12 = (values[-1] / values[-13] - 1) * 100
    reversal = (roc12 < 0 < roc3) or (roc12 > 0 > roc3)
    momentum = min(abs(roc3), 5) / 5 * 30 if reversal else min(abs(roc3), 5) / 5 * 15
    confirm = (
        (10 if cross else 0)
        + (10 if cross and ((cross == "golden") == (roc3 > 0)) else 0)
        + (10 if len(gaps) > 1 and abs(gaps[-1]) > abs(gaps[-2]) else 0)
    )
    direction = "up" if gaps[-1] > 0 and roc3 >= 0 else "down" if gaps[-1] < 0 and roc3 <= 0 else "mixed"
    return {
        "score": round(cross_score + momentum + confirm, 1),
        "direction": direction,
        "crossover": cross,
        "crossover_months_ago": cross_age,
        "sma_gap_pct": round(gaps[-1] * 100, 2),
        "roc_3m_pct": round(roc3, 2),
        "roc_12m_pct": round(roc12, 2),
        "months": len(values),
    }


def regime(bars: dict[str, list[dict]], macro: dict[str, list[tuple[dt.date, float]]]) -> dict[str, Any]:
    comps: dict[str, Any] = {}
    labels: dict[str, str] = {}
    for name, (num, den, weight, up, down) in COMPONENTS.items():
        c = component(monthly_ratio(bars.get(num) or [], bars.get(den) or []))
        c["weight"] = weight
        comps[name] = c
        labels[name] = up if c["direction"] == "up" else down if c["direction"] == "down" else "mixed"
    ten, two = dict(macro.get("DGS10") or []), dict(macro.get("DGS2") or [])
    curve = monthly([(d, ten[d] - two[d]) for d in sorted(ten) if d in two])
    yc = component(curve)
    yc["weight"] = 20
    yc["spread_now"] = round(curve[-1][1], 3) if curve else None
    comps["yield_curve"] = yc
    labels["yield_curve"] = (
        "steepening" if yc["direction"] == "up" else "flattening" if yc["direction"] == "down" else "mixed"
    )
    known = {k: v for k, v in comps.items() if v.get("score") is not None}
    weight = sum(v["weight"] for v in known.values())
    composite = sum(v["score"] * v["weight"] for v in known.values()) / weight if weight else 0.0
    # stock-bond correlation over the last 12 monthly returns
    spy, tlt = monthly_ratio(bars.get("SPY") or [], bars.get("SPY") or []), None
    corr = _corr(bars.get("SPY") or [], bars.get("TLT") or [])
    scores = {"concentration": 0, "broadening": 0, "contraction": 0, "inflationary": 0}
    scores["concentration"] += (
        2 * (labels["concentration"] == "concentrating")
        + 2 * (labels["size"] == "large_cap_leading")
        + (labels["credit"] in ("easing", "mixed"))
    )
    scores["broadening"] += (
        2 * (labels["concentration"] == "broadening")
        + 2 * (labels["size"] == "small_cap_leading")
        + (labels["credit"] in ("easing", "mixed"))
        + (labels["sector"] == "risk_on")
    )
    scores["contraction"] += (
        2 * (labels["credit"] == "tightening")
        + 2 * (labels["sector"] == "risk_off")
        + (labels["equity_bond"] == "risk_off")
    )
    scores["inflationary"] += 3 * (corr is not None and corr > 0.3) + (labels["equity_bond"] == "risk_off")
    best = max(scores, key=lambda k: scores[k])
    signaling = sum(1 for v in known.values() if v["score"] >= 40)
    name = "transitional" if signaling >= 3 and scores[best] < 3 else best if scores[best] >= 2 else "unclear"
    confidence = (
        "high" if scores[best] >= 4 else "moderate" if scores[best] >= 3 else "low" if scores[best] >= 2 else "very_low"
    )
    avg = sum(v["score"] for v in known.values()) / len(known) if known else 0
    transition = (
        "high"
        if signaling >= 4 and avg >= 50
        else "moderate"
        if signaling >= 3 and avg >= 40
        else "low"
        if signaling >= 2 and avg >= 30
        else "minimal"
    )
    del spy, tlt
    return {
        "regime": name,
        "confidence": confidence,
        "transition_probability": transition,
        "composite": round(composite, 1),
        "components": comps,
        "labels": labels,
        "regime_scores": scores,
        "stock_bond_correlation": corr,
        "unknown": [k for k, v in comps.items() if v.get("score") is None],
    }


def _corr(a: list[dict], b: list[dict]) -> float | None:
    ma, mb = (
        monthly([(x["ts"].date(), float(x["close"])) for x in a]),
        monthly([(x["ts"].date(), float(x["close"])) for x in b]),
    )
    ra = {d.strftime("%Y-%m"): v for d, v in ma}
    rb = {d.strftime("%Y-%m"): v for d, v in mb}
    keys = sorted(set(ra) & set(rb))[-13:]
    if len(keys) < 13:
        return None
    xa = [ra[keys[i]] / ra[keys[i - 1]] - 1 for i in range(1, len(keys))]
    xb = [rb[keys[i]] / rb[keys[i - 1]] - 1 for i in range(1, len(keys))]
    mx, my = sum(xa) / len(xa), sum(xb) / len(xb)
    cov = sum((x - mx) * (y - my) for x, y in zip(xa, xb, strict=True))
    vx, vy = sum((x - mx) ** 2 for x in xa) ** 0.5, sum((y - my) ** 2 for y in xb) ** 0.5
    return round(cov / (vx * vy), 3) if vx and vy else None


def signal(bars: dict[str, list[dict]], macro: dict[str, list[tuple[dt.date, float]]]) -> Signal:
    r = regime(bars, macro)
    spy = bars.get("SPY") or []
    source, as_of = (spy[-1].get("source"), spy[-1].get("as_of")) if spy else (None, None)
    why = (
        f"Macro regime: {r['regime']} ({r['confidence']} confidence), "
        f"transition probability {r['transition_probability']}"
        + (f"; unknown: {', '.join(r['unknown'])}" if r["unknown"] else "")
    )
    return Signal(
        "MARKET", MODULE, "macro", "neutral", r["composite"], 365, why, evidence=r, source=source, as_of=as_of
    ).clamp()
