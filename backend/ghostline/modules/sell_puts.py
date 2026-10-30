"""Cash-secured puts to sell: 30–45 days out, 5–15% under the spot (delta −0.15 to −0.35 when the chain gives
it), no earnings before expiry, enough open interest and a sane spread; scored on the annualized premium yield.
The collateral is strike × 100 per contract. Black–Scholes rechecks the delta and the odds of expiring worthless."""

from __future__ import annotations

import datetime as dt
from typing import Any

from ghostline import occ
from ghostline.modules import Signal, options_math

MODULE = "sell_puts"
RATE = 0.04


def candidates(
    ticker: str,
    chain: list[dict],
    spot: float,
    today: dt.date,
    days_to_earnings: int | None,
    min_open_interest: float = 500.0,
) -> list[dict[str, Any]]:
    out = []
    for row in chain:
        if row.get("kind") != "put" or row.get("bid") is None or row.get("strike") is None:
            continue
        strike, bid = float(row["strike"]), float(row["bid"])
        ask = float(row["ask"]) if row.get("ask") is not None else None
        expiry = row["expiry"] if isinstance(row["expiry"], dt.date) else dt.date.fromisoformat(str(row["expiry"]))
        dte = (expiry - today).days
        if not 30 <= dte <= 45 or bid < 0.10:
            continue
        otm = (spot - strike) / spot * 100
        delta = float(row["delta"]) if row.get("delta") is not None else None
        if delta is not None:
            if not -0.35 <= delta <= -0.15:
                continue
        elif not 5 <= otm <= 15:
            continue
        if days_to_earnings is not None and 0 <= days_to_earnings <= dte:
            continue
        oi = float(row["open_interest"]) if row.get("open_interest") is not None else None
        if oi is None or oi < min_open_interest:
            continue
        mid = (bid + ask) / 2 if ask else bid
        spread_share = (ask - bid) / mid if ask and mid else None
        if spread_share is not None and spread_share > 0.25:
            continue
        annualized = bid / strike * 365 / dte * 100
        score = (
            90
            if annualized >= 30
            else 75
            if annualized >= 20
            else 60
            if annualized >= 12
            else 45
            if annualized >= 8
            else 30
        )
        if oi >= 1000:
            score += 5
        if spread_share is not None and spread_share <= 0.10:
            score += 5
        iv = float(row["iv"]) if row.get("iv") else None
        bs = {}
        if iv and iv > 0:
            years = dte / 365
            bs = {
                "bs_delta": round(options_math.put_delta(spot, strike, years, RATE, iv), 3),
                "bs_price": round(options_math.put_price(spot, strike, years, RATE, iv), 3),
                "p_expires_worthless": round(options_math.put_expires_worthless(spot, strike, years, RATE, iv), 3),
            }
        out.append(
            {
                "option_symbol": row["option_symbol"],
                "strike": strike,
                "expiry": expiry.isoformat(),
                "dte": dte,
                "bid": bid,
                "ask": ask,
                "delta": delta,
                "iv": iv,
                "open_interest": oi,
                "otm_pct": round(otm, 2),
                "annualized_yield_pct": round(annualized, 2),
                "collateral": strike * 100,
                "score": min(100, score),
                "spread_share": None if spread_share is None else round(spread_share, 3),
                **bs,
            }
        )
    out.sort(key=lambda c: (-c["score"], -c["annualized_yield_pct"]))
    return out


def signal(
    ticker: str,
    chain: list[dict],
    spot: float | None,
    today: dt.date,
    days_to_earnings: int | None,
    source: str | None,
    as_of: dt.datetime | None,
    min_open_interest: float = 500.0,
) -> Signal | None:
    if spot is None or not chain:
        return None
    cands = candidates(ticker, chain, spot, today, days_to_earnings, min_open_interest)
    if not cands:
        return Signal(
            ticker,
            MODULE,
            "puts",
            "short_put",
            0,
            45,
            f"{ticker}: no put 30–45 days out passes the delta, earnings, open-interest and spread checks",
            evidence={"chain_rows": len(chain), "spot": spot},
            source=source,
            as_of=as_of,
        ).clamp()
    best = cands[0]
    strike = best["strike"]
    stop = round(strike * 0.90, 4)  # close the put if the stock falls 10% under the strike
    why = (
        f"{ticker}: sell the {best['expiry']} {strike:g} put for {best['bid']:.2f} "
        f"({best['annualized_yield_pct']:.0f}% a "
        f"year on {best['collateral']:,.0f} collateral), {best['otm_pct']:.1f}% under {spot:.2f}, "
        f"{best['dte']} days, OI {best['open_interest']:,.0f}"
    )
    ev = {
        "spot": spot,
        "best": best,
        "others": cands[1:4],
        "stop_rule": "close if the stock trades 10% under the strike",
    }
    return Signal(
        ticker,
        MODULE,
        "puts",
        "short_put",
        best["score"],
        best["dte"],
        why,
        entry=best["bid"],
        stop=stop,
        target=0.0,
        actionable=True,
        evidence=ev,
        source=source,
        as_of=as_of,
        option_symbol=occ.parse(best["option_symbol"]).symbol,
    ).clamp()
