"""Indicators computed in code, from stored bars: SMA, EMA, RSI, MACD, Bollinger bands, and support and resistance
levels from pivot clusters. Floats are fine here (these are chart lines, not money); every line carries None until it
has enough history, never a made-up value."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        out.append(sum(values[i - n + 1:i + 1]) / n if i + 1 >= n else None)
    return out


def ema(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    k = 2 / (n + 1)
    prev: float | None = None
    for i, v in enumerate(values):
        if i + 1 < n:
            out.append(None)
            continue
        if prev is None:
            prev = sum(values[i - n + 1:i + 1]) / n
        else:
            prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(values: list[float], n: int = 14) -> list[float | None]:
    """Wilder's RSI: the first value after n changes, then smoothed."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    out[n] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(n + 1, len(values)):
        avg_gain = (avg_gain * (n - 1) + gains[i - 1]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, list[float | None]]:
    f, s = ema(values, fast), ema(values, slow)
    line: list[float | None] = [None if (a is None or b is None) else a - b for a, b in zip(f, s, strict=True)]
    known = [v for v in line if v is not None]
    sig_known = ema(known, signal) if known else []
    sig: list[float | None] = [None] * (len(line) - len(sig_known)) + sig_known
    hist = [None if (a is None or b is None) else a - b for a, b in zip(line, sig, strict=True)]
    return {"macd": line, "signal": sig, "histogram": hist}


def bollinger(values: list[float], n: int = 20, k: float = 2.0) -> dict[str, list[float | None]]:
    mid = sma(values, n)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i, m in enumerate(mid):
        if m is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[i - n + 1:i + 1]
        sd = (sum((v - m) ** 2 for v in window) / n) ** 0.5
        upper.append(m + k * sd)
        lower.append(m - k * sd)
    return {"middle": mid, "upper": upper, "lower": lower}


@dataclass(frozen=True)
class Level:
    price: float
    kind: str        # support | resistance
    touches: int


def levels(highs: list[float], lows: list[float], closes: list[float], window: int = 5, tolerance: float = 0.01,
           max_levels: int = 6) -> list[Level]:
    """Pivot highs and lows, clustered within `tolerance` of each other; a cluster with more touches ranks higher.
    A level above the last close is resistance, below it support."""
    if len(closes) < window * 2 + 1:
        return []
    pivots: list[float] = []
    for i in range(window, len(closes) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            pivots.append(highs[i])
        if lows[i] == min(lows[i - window:i + window + 1]):
            pivots.append(lows[i])
    clusters: list[list[float]] = []
    for p in sorted(pivots):
        if clusters and abs(p - clusters[-1][-1]) / clusters[-1][-1] <= tolerance:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    last = closes[-1]
    out = [Level(round(sum(c) / len(c), 4), "resistance" if sum(c) / len(c) > last else "support", len(c))
           for c in clusters]
    out.sort(key=lambda lv: (-lv.touches, abs(lv.price - last)))
    chosen = out[:max_levels]
    counts = Counter(lv.kind for lv in chosen)
    _ = counts
    return sorted(chosen, key=lambda lv: lv.price)


def chart_payload(bars: list[dict]) -> dict:
    """Everything the chart screen draws, from stored bars: the indicators and the levels, aligned to the bars."""
    closes = [float(Decimal(b["close"])) for b in bars]
    highs = [float(Decimal(b["high"])) for b in bars]
    lows = [float(Decimal(b["low"])) for b in bars]
    m = macd(closes) if closes else {"macd": [], "signal": [], "histogram": []}
    bb = bollinger(closes) if closes else {"middle": [], "upper": [], "lower": []}
    return {
        "sma20": sma(closes, 20), "sma50": sma(closes, 50), "sma200": sma(closes, 200),
        "ema12": ema(closes, 12), "rsi14": rsi(closes, 14), "macd": m, "bollinger": bb,
        "levels": [{"price": lv.price, "kind": lv.kind, "touches": lv.touches} for lv in levels(highs, lows, closes)],
    }
