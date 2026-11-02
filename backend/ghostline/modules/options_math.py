"""Black–Scholes in plain Python (vendor/tradermonty black_scholes_methodology.md): a put's price, delta and the
probability it expires worthless, to recheck what the chain says. No dependency; `math.erf` is the normal CDF."""

from __future__ import annotations

import math


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def d1_d2(spot: float, strike: float, years: float, rate: float, iv: float) -> tuple[float, float]:
    if spot <= 0 or strike <= 0 or years <= 0 or iv <= 0:
        raise ValueError("spot, strike, time and volatility must be positive")
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * years) / (iv * math.sqrt(years))
    return d1, d1 - iv * math.sqrt(years)


def put_price(spot: float, strike: float, years: float, rate: float, iv: float) -> float:
    d1, d2 = d1_d2(spot, strike, years, rate, iv)
    return strike * math.exp(-rate * years) * norm_cdf(-d2) - spot * norm_cdf(-d1)


def put_delta(spot: float, strike: float, years: float, rate: float, iv: float) -> float:
    d1, _ = d1_d2(spot, strike, years, rate, iv)
    return norm_cdf(d1) - 1.0


def call_price(spot: float, strike: float, years: float, rate: float, iv: float) -> float:
    d1, d2 = d1_d2(spot, strike, years, rate, iv)
    return spot * norm_cdf(d1) - strike * math.exp(-rate * years) * norm_cdf(d2)


def call_delta(spot: float, strike: float, years: float, rate: float, iv: float) -> float:
    d1, _ = d1_d2(spot, strike, years, rate, iv)
    return norm_cdf(d1)


def implied_vol(price: float, spot: float, strike: float, years: float, rate: float, kind: str,
                low: float = 0.01, high: float = 5.0, tolerance: float = 1e-6) -> float | None:
    """The volatility at which Black–Scholes gives `price`, by bisection (the price rises with volatility). None when
    the price is outside what any volatility from 1% to 500% gives: under the intrinsic value, or absurdly high."""
    if price <= 0 or spot <= 0 or strike <= 0 or years <= 0:
        return None
    value = put_price if kind == "put" else call_price
    try:
        if not value(spot, strike, years, rate, low) <= price <= value(spot, strike, years, rate, high):
            return None
    except ValueError:
        return None
    for _ in range(100):
        mid = (low + high) / 2
        if value(spot, strike, years, rate, mid) < price:
            low = mid
        else:
            high = mid
        if high - low < tolerance:
            break
    return (low + high) / 2


def put_expires_worthless(spot: float, strike: float, years: float, rate: float, iv: float) -> float:
    """The risk-neutral probability the spot ends above the strike: N(d2)."""
    _, d2 = d1_d2(spot, strike, years, rate, iv)
    return norm_cdf(d2)
