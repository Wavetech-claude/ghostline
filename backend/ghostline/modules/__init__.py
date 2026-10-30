"""Strategy modules. Each turns stored data into the same signal record: ticker, module, direction, score, horizon,
entry, stop, target, evidence and the time of the newest row it read. Borrowed methods (`vendor/`) became code here;
Claude never improvises a screen. Floats are fine for scores; prices are rounded to cents when stored."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Signal:
    ticker: str
    module: str
    lane: str  # long | swing | puts | macro
    direction: str  # long | short | short_put | neutral
    score: float  # 0..100
    horizon_days: int
    reason: str  # one plain sentence
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    actionable: bool = False  # inside the entry window right now
    evidence: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    as_of: dt.datetime | None = None
    option_symbol: str | None = None

    def clamp(self) -> Signal:
        self.score = max(0.0, min(100.0, round(self.score, 2)))
        return self


def floats(bars: list[dict], key: str) -> list[float]:
    return [float(b[key]) for b in bars]


def newest(bars: list[dict]) -> tuple[str | None, dt.datetime | None]:
    if not bars:
        return None, None
    return bars[-1].get("source"), bars[-1].get("as_of")
