"""Market data: free sources, each behind one interface, every row stamped with its source and as-of time, every call
counted against the source's free tier. The sources never compute anything; ghostline.indicators does."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


class NotConfigured(Exception):
    """The source's key is not set: its jobs skip, with a logged reason, until the owner adds it."""


class RateLimited(Exception):
    """The source's free-tier counter is at its limit for this window."""


class SourceError(Exception):
    """A source answered with an error, or could not be reached. The text names the source, the path, the status and
    the source's own short reason; it never carries the request's query, where FRED's key travels."""


@dataclass(frozen=True)
class Bar:
    ts: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Quote:
    ticker: str
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    ts: dt.datetime
    delayed: bool = True


@dataclass(frozen=True)
class NewsItem:
    ticker: str | None
    headline: str
    url: str
    published_at: dt.datetime
    tone: Decimal | None = None


@dataclass(frozen=True)
class Earning:
    ticker: str
    at: dt.date
    estimate: Decimal | None
    actual: Decimal | None


@dataclass(frozen=True)
class Filing:
    accession: str
    ticker: str
    form: str
    filed_at: dt.date
    url: str | None


@dataclass(frozen=True)
class MacroPoint:
    series: str
    ts: dt.date
    value: Decimal | None


@dataclass
class Pulled:
    """What one pull brought back, with the source and the time it was pulled (the as_of every stored row carries)."""
    source: str
    as_of: dt.datetime
    rows: list[Any] = field(default_factory=list)


def dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001 — a value that is not a number is "no value", never a guess
        return None


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
