"""OCC option symbols: ROOT + yymmdd + P|C + strike × 1000 as eight digits (AAPL261121P00150000)."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal

_PATTERN = re.compile(r"^([A-Z]{1,6})(\d{6})([PC])(\d{8})$")


@dataclass(frozen=True)
class Option:
    symbol: str
    underlying: str
    expiry: dt.date
    kind: str           # put | call
    strike: Decimal

    @property
    def collateral_per_contract(self) -> Decimal:
        """A cash-secured put locks strike × 100 until it expires."""
        return self.strike * 100


def parse(symbol: str) -> Option:
    s = (symbol or "").strip().upper()
    m = _PATTERN.match(s)
    if not m:
        raise ValueError(f"not an OCC option symbol: {symbol!r}")
    root, ymd, pc, strike = m.groups()
    try:
        expiry = dt.datetime.strptime(ymd, "%y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"not an OCC option symbol (date): {symbol!r}") from exc
    return Option(s, root, expiry, "put" if pc == "P" else "call", Decimal(strike) / 1000)


def build(underlying: str, expiry: dt.date, kind: str, strike: Decimal) -> str:
    pc = "P" if kind == "put" else "C"
    return f"{underlying.upper()}{expiry:%y%m%d}{pc}{int(strike * 1000):08d}"
