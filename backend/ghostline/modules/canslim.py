"""O'Neil's CANSLIM, from vendor/tradermonty (canslim_methodology.md): quarterly earnings (C), annual growth (A),
new highs (N), supply and demand (S), institutional sponsorship (I, not available here), market direction (M).
Components the stored data cannot measure are None, named in the evidence, and left out of the weighted score."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ghostline import indicators
from ghostline.modules import Signal, floats, newest
from ghostline.modules import trend as T

MODULE = "canslim"
WEIGHTS = {"C": 19, "A": 25, "N": 19, "S": 19, "I": 13, "M": 6}
MINIMA = {"C": 60, "A": 50, "N": 40, "S": 40, "M": 40}


def quarterly_growth(earnings: list[dict]) -> tuple[float | None, dict]:
    """Latest reported EPS against the report about a year earlier (330–400 days): growth in percent."""
    actual = sorted([e for e in earnings if e.get("actual") is not None], key=lambda e: e["at"], reverse=True)
    if not actual:
        return None, {"quarters": 0}
    latest = actual[0]
    prior = [e for e in actual if 330 <= (latest["at"] - e["at"]).days <= 400]
    if not prior or float(prior[0]["actual"]) <= 0:
        return None, {"latest": str(latest["actual"]), "prior_year": None}
    g = T.pct(float(latest["actual"]), float(prior[0]["actual"]))
    return g, {
        "latest": str(latest["actual"]),
        "prior_year": str(prior[0]["actual"]),
        "latest_at": latest["at"].isoformat(),
    }


def c_score(growth: float | None) -> float | None:
    if growth is None:
        return None
    return 100 if growth >= 50 else 80 if growth >= 30 else 60 if growth >= 18 else 40 if growth >= 10 else 0


def annual_growth(income: list[dict]) -> tuple[float | None, dict]:
    """3-year EPS CAGR from annual statements (newest first), the stability bonus and the revenue check."""
    rows = [r for r in income if r.get("eps") is not None]
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    if len(rows) < 4 or float(rows[3]["eps"]) <= 0 or float(rows[0]["eps"]) <= 0:
        return None, {"years": len(rows)}
    eps = [float(r["eps"]) for r in rows[:4]]
    cagr = ((eps[0] / eps[3]) ** (1 / 3) - 1) * 100
    stable = all(eps[i] > eps[i + 1] for i in range(3))
    rev = [float(r["revenue"]) for r in rows[:4] if r.get("revenue") is not None]
    rev_cagr = ((rev[0] / rev[3]) ** (1 / 3) - 1) * 100 if len(rev) == 4 and rev[3] > 0 and rev[0] > 0 else None
    return cagr, {"eps": eps, "stable": stable, "revenue_cagr_pct": None if rev_cagr is None else round(rev_cagr, 2)}


def a_score(cagr: float | None, meta: dict) -> float | None:
    if cagr is None:
        return None
    stable, rev = meta.get("stable", False), meta.get("revenue_cagr_pct")
    if cagr >= 40 and stable and (rev is None or rev >= 20):
        s = 90
    elif cagr >= 30 and stable:
        s = 70
    elif cagr >= 25:
        s = 50
    elif cagr >= 15:
        s = 30
    else:
        s = 0
    if stable:
        s += 10
    if rev is not None and rev < 0.5 * cagr:
        s *= 0.8
    return min(100, s)


def n_score(bars: list[dict]) -> tuple[float, dict]:
    hi, _ = T.range_52w(bars)
    closes, vols = floats(bars, "close"), floats(bars, "volume")
    dist = T.pct(closes[-1], hi)
    v50 = T.avg(vols[-50:]) or 1.0
    breakout = closes[-1] >= hi * 0.999 and vols[-1] >= 1.4 * v50
    s = 80 if dist >= -5 else 70 if dist >= -10 else 60 if dist >= -15 else 40 if dist >= -25 else 20
    if breakout:
        s = min(100, s + 20)
    return s, {"distance_from_52w_high_pct": round(dist, 2), "breakout_on_volume": breakout}


def s_score(bars: list[dict]) -> tuple[float | None, dict]:
    window = bars[-61:]
    up, down = [], []
    for i in range(1, len(window)):
        c, p, v = float(window[i]["close"]), float(window[i - 1]["close"]), float(window[i]["volume"])
        (up if c > p else down if c < p else []).append(v)
    if not up or not down:
        return None, {"up_days": len(up), "down_days": len(down)}
    ratio = (sum(up) / len(up)) / (sum(down) / len(down))
    s = (
        100
        if ratio >= 2
        else 80
        if ratio >= 1.5
        else 60
        if ratio >= 1
        else 40
        if ratio >= 0.7
        else 20
        if ratio >= 0.5
        else 0
    )
    return s, {"accumulation_ratio": round(ratio, 3)}


def m_score(spy: list[dict]) -> tuple[float | None, dict]:
    closes = floats(spy, "close")
    e50 = T.last(indicators.ema(closes, 50))
    if e50 is None:
        return None, {"spy_bars": len(closes)}
    d = T.pct(closes[-1], e50)
    s = 80 if d >= 2 else 70 if d >= 1 else 60 if d >= 0 else 40 if d >= -2 else 20 if d >= -3 else 0
    return s, {"spy_vs_ema50_pct": round(d, 2)}


def signal(
    ticker: str, bars: list[dict], spy: list[dict], earnings: list[dict], fundamentals: dict[str, Any] | None
) -> Signal:
    source, as_of = newest(bars)
    if len(bars) < 260:
        return Signal(
            ticker,
            MODULE,
            "long",
            "long",
            0,
            120,
            f"{ticker}: fewer than 260 daily bars stored",
            evidence={"bars": len(bars)},
            source=source,
            as_of=as_of,
        ).clamp()
    growth, c_meta = quarterly_growth(earnings)
    cagr, a_meta = annual_growth((fundamentals or {}).get("income_annual") or [])
    n, n_meta = n_score(bars)
    s, s_meta = s_score(bars)
    m, m_meta = m_score(spy)
    comps: dict[str, float | None] = {
        "C": c_score(growth),
        "A": a_score(cagr, a_meta),
        "N": n,
        "S": s,
        "I": None,
        "M": m,
    }
    known = {k: v for k, v in comps.items() if v is not None}
    weight = sum(WEIGHTS[k] for k in known)
    composite = sum(v * WEIGHTS[k] for k, v in known.items()) / weight if weight else 0.0
    unknown = [k for k, v in comps.items() if v is None]
    below = [k for k, v in known.items() if k in MINIMA and v < MINIMA[k]]
    actionable = not below and all(k in known for k in ("C", "N", "S", "M"))
    price = floats(bars, "close")[-1]
    ev = {
        "components": comps,
        "unknown": unknown,
        "below_minimum": below,
        "quarterly_eps_growth_pct": growth and round(growth, 2),
        "annual_eps_cagr_pct": cagr and round(cagr, 2),
        **{f"c_{k}": v for k, v in c_meta.items()},
        **{f"a_{k}": v for k, v in a_meta.items()},
        **n_meta,
        **s_meta,
        **m_meta,
    }
    why = (
        f"{ticker}: CANSLIM {composite:.0f} from {', '.join(f'{k} {v:.0f}' for k, v in known.items())}"
        + (f"; unknown {', '.join(unknown)}" if unknown else "")
        + (f"; under the minimum: {', '.join(below)}" if below else "")
    )
    return Signal(
        ticker,
        MODULE,
        "long",
        "long",
        composite,
        120,
        why,
        entry=round(price, 4),
        stop=round(price * 0.92, 4),
        target=round(price * 1.25, 4),
        actionable=actionable,
        evidence=ev,
        source=source,
        as_of=as_of,
    ).clamp()


def today() -> dt.date:
    return dt.date.today()
