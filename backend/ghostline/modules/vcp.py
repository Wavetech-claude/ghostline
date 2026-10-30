"""Minervini's Volatility Contraction Pattern, from vendor/tradermonty (vcp_methodology.md, scoring_system.md): a
Stage 2 stock (trend template) whose pullbacks tighten; buy above the pivot on volume, stop under the last low."""

from __future__ import annotations

from ghostline import indicators
from ghostline.modules import Signal, floats, newest
from ghostline.modules import trend as T

MODULE = "vcp"
LOOKBACK = 120


def contractions(bars: list[dict]) -> list[dict]:
    """From the swings of the lookback window: each swing high and the low after it is one contraction, depth in
    percent. Only the run of successively tighter contractions ending at the last swing counts."""
    window = bars[-LOOKBACK:]
    sw = T.swings(window)
    pairs = []
    for i in range(len(sw) - 1):
        if sw[i].kind == "high" and sw[i + 1].kind == "low":
            depth = T.pct(sw[i].price, sw[i + 1].price)  # high over low, positive
            pairs.append(
                {
                    "high": sw[i].price,
                    "low": sw[i + 1].price,
                    "depth_pct": round(depth, 2),
                    "days": sw[i + 1].index - sw[i].index,
                    "high_index": sw[i].index,
                    "low_index": sw[i + 1].index,
                }
            )
    # keep the tail where each contraction is at most 0.75 × the one before
    run = [pairs[-1]] if pairs else []
    for p in reversed(pairs[:-1]):
        if run[0]["depth_pct"] <= 0.75 * p["depth_pct"]:
            run.insert(0, p)
        else:
            break
    return run


def signal(ticker: str, bars: list[dict], spy: list[dict]) -> Signal:
    source, as_of = newest(bars)
    if len(bars) < 260:
        return Signal(
            ticker,
            MODULE,
            "swing",
            "long",
            0,
            40,
            f"{ticker}: fewer than 260 daily bars stored",
            evidence={"bars": len(bars)},
            source=source,
            as_of=as_of,
        ).clamp()
    passed, checks = T.trend_template(bars, spy)
    closes, vols = floats(bars, "close"), floats(bars, "volume")
    price = closes[-1]
    rs = T.relative_strength(bars, spy)
    ev: dict = {
        "trend_template_passed": passed,
        "checks": checks,
        "rs_vs_spy_pct": None if rs is None else round(rs, 2),
    }
    trend_score = passed / 7 * 100
    if passed < 6:
        return Signal(
            ticker,
            MODULE,
            "swing",
            "long",
            trend_score * 0.25,
            40,
            f"{ticker} is not in a Stage 2 uptrend ({passed} of 7 trend points)",
            evidence=ev,
            source=source,
            as_of=as_of,
        ).clamp()
    cons = contractions(bars)
    ev["contractions"] = cons
    if len(cons) < 2:
        return Signal(
            ticker,
            MODULE,
            "swing",
            "long",
            trend_score * 0.25 + 10,
            40,
            f"{ticker} is in a Stage 2 uptrend but has no tightening base yet",
            evidence=ev,
            source=source,
            as_of=as_of,
        ).clamp()
    final = cons[-1]
    pivot, last_low = final["high"], final["low"]
    t1 = cons[0]["depth_pct"]
    # contraction quality
    quality = {4: 90, 3: 80, 2: 60}.get(min(len(cons), 4), 40)
    if final["depth_pct"] < 5:
        quality += 10
    if final["depth_pct"] < 0.4 * t1:
        quality += 10
    if t1 > 30:
        quality -= 10
    # volume: dry-up over the ten bars before the last, against the 50-day average
    v50 = T.avg(vols[-50:]) or 1.0
    dry = (T.avg(vols[-11:-1]) or 0) / v50
    volume_score = 90 if dry < 0.3 else 75 if dry < 0.5 else 60 if dry < 0.7 else 40 if dry <= 1.0 else 20
    breakout_vol = vols[-1] / v50
    above = T.pct(price, pivot)
    confirmed = above >= 0 and breakout_vol >= 1.5
    if 0 <= above <= 3 and confirmed:
        volume_score += 10
    # pivot proximity
    if above > 20:
        prox = 20
    elif above > 10:
        prox = 35
    elif above > 5:
        prox = 50
    elif above > 3:
        prox = 65 + (10 if confirmed else 0)
    elif above >= 0:
        prox = 90 + (10 if confirmed else 0)
    elif above >= -2:
        prox = 90
    elif above >= -5:
        prox = 75
    elif above >= -8:
        prox = 60
    elif above >= -10:
        prox = 45
    elif above >= -15:
        prox = 30
    else:
        prox = 10
    rs_s = T.rs_score(rs) or 0
    composite = (
        trend_score * 0.25 + min(quality, 100) * 0.25 + min(volume_score, 100) * 0.20 + prox * 0.15 + rs_s * 0.15
    )
    # execution state
    s50 = T.last(indicators.sma(closes, 50)) or 0
    s200 = T.last(indicators.sma(closes, 200)) or 0
    if price < s50 < s200:
        state = "invalid"
    elif price < last_low or price < s50:
        state = "damaged"
    elif T.pct(price, s200) > 50 or above > 10:
        state = "overextended"
    elif above > 5:
        state = "extended"
    elif above > 3 or (above >= 0 and not confirmed):
        state = "early_post_breakout"
    elif above >= 0:
        state = "breakout"
    else:
        state = "pre_breakout"
    caps = {"invalid": 49, "damaged": 49, "overextended": 59, "extended": 69, "early_post_breakout": 89}
    composite = min(composite, caps.get(state, 100))
    wide_and_loose = final["depth_pct"] > 15 and final["days"] < 10
    if wide_and_loose:
        composite = min(composite, 69)
    stop = last_low * 0.98
    risk_pct = T.pct(pivot, stop)
    actionable = state in ("pre_breakout", "breakout") and -8 <= above <= 3 and dry <= 1.0 and 0 < risk_pct <= 15
    ev.update(
        {
            "pivot": round(pivot, 4),
            "last_low": round(last_low, 4),
            "distance_from_pivot_pct": round(above, 2),
            "dry_up_ratio": round(dry, 3),
            "breakout_volume_ratio": round(breakout_vol, 2),
            "state": state,
            "wide_and_loose": wide_and_loose,
            "risk_pct": round(risk_pct, 2),
            "components": {
                "trend": round(trend_score, 1),
                "contraction": quality,
                "volume": volume_score,
                "pivot": prox,
                "rs": rs_s,
            },
        }
    )
    why = (
        f"{ticker}: {len(cons)} tightening contractions (T1 {t1:.1f}% → {final['depth_pct']:.1f}%), "
        f"{abs(above):.1f}% {'above' if above >= 0 else 'below'} the pivot {pivot:.2f}, volume dry-up {dry:.2f}, "
        f"state {state.replace('_', ' ')}"
    )
    return Signal(
        ticker,
        MODULE,
        "swing",
        "long",
        composite,
        40,
        why,
        entry=round(pivot, 4),
        stop=round(stop, 4),
        target=round(pivot * 1.20, 4),
        actionable=actionable,
        evidence=ev,
        source=source,
        as_of=as_of,
    ).clamp()
