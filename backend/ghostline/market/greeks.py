"""Implied volatility and delta for a chain whose source gives none (Alpaca's free indicative feed), from each
option's mid price (the bid alone when there is no ask) and the stock's price, with Black–Scholes
(modules/options_math.py, the same rate the sell-puts module uses). A row Black–Scholes cannot price is left without
them, and the sell-puts module then falls back to its 5-15% under the spot rule."""
from __future__ import annotations

import datetime as dt

from ghostline import occ
from ghostline.modules import options_math
from ghostline.modules.sell_puts import RATE


def fill(rows: list[dict], spot: float | None, today: dt.date, rate: float = RATE) -> list[dict]:
    """Each row in the Tradier shape gains greeks {"mid_iv", "delta"} when it has none and can be priced."""
    if not spot or spot <= 0:
        return rows
    for r in rows:
        if (r.get("greeks") or {}).get("delta") is not None:
            continue
        try:
            o = occ.parse(str(r.get("symbol") or ""))
        except ValueError:
            continue
        bid, ask = _f(r.get("bid")), _f(r.get("ask"))
        price = (bid + ask) / 2 if bid and ask else bid
        years = (o.expiry - today).days / 365
        if not price or years <= 0:
            continue
        iv = options_math.implied_vol(price, spot, float(o.strike), years, rate, o.kind)
        if iv is None:
            continue
        delta = (options_math.put_delta if o.kind == "put" else options_math.call_delta)(spot, float(o.strike),
                                                                                         years, rate, iv)
        r["greeks"] = {"mid_iv": round(iv, 6), "delta": round(delta, 6), "computed": True}
    return rows


def _f(v: object) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None
