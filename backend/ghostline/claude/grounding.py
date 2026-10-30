"""Every number in a memo must be a stored value. The facts bundle carries every number Claude was given (and the
derived ones the code computed); a number in the text that matches none of them, at any rounding, is struck and
replaced with "[n/a]", and the memo is marked not grounded. Years, small counts and list markers are not numbers
about money or prices and pass."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

_NUM = re.compile(
    r"(?<![\w.])[-+]?\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?(?![\w-])"   # 1,234.56
    r"|(?<![\w.])[-+]?\$?\d+(?:\.\d+)?%?(?![\w-])"                  # 1234.56, -3%, $12
)
_PASS_LOW, _PASS_HIGH = 0, 12  # counts and ordinals: "3 of 7", "2 analysts"
_YEAR_LOW, _YEAR_HIGH = 1990, 2100


def numbers_in(obj: Any, out: set[Decimal] | None = None) -> set[Decimal]:
    """Every number inside a facts bundle (dict, list, str, number), as Decimals."""
    out = set() if out is None else out
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float, Decimal)):
        try:
            out.add(Decimal(str(obj)))
        except InvalidOperation:
            pass
    elif isinstance(obj, str):
        for m in _NUM.finditer(obj):
            d = _parse(m.group(0))
            if d is not None:
                out.add(d)
    elif isinstance(obj, dict):
        for v in obj.values():
            numbers_in(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            numbers_in(v, out)
    return out


def _parse(token: str) -> Decimal | None:
    t = token.replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def matches(value: Decimal, facts: set[Decimal]) -> bool:
    """A fact at any rounding (0–4 places), or within 0.5% of one, or its sign flipped, or ×100 / ÷100 (a share
    shown as a percent)."""
    cands = {value, -value, value / 100, -value / 100, value * 100, -value * 100}
    for f in facts:
        for c in cands:
            if c == f:
                return True
            for places in range(0, 5):
                q = Decimal(1).scaleb(-places)
                try:
                    if f.quantize(q) == c or c.quantize(q) == f:
                        return True
                except InvalidOperation:
                    continue
            if f != 0 and abs((c - f) / f) <= Decimal("0.005"):
                return True
    return False


def ground(text: str, facts: Any) -> tuple[str, list[str]]:
    """(the text with unknown numbers struck, the struck tokens)."""
    known = numbers_in(facts)
    struck: list[str] = []

    def repl(m: re.Match) -> str:
        token = m.group(0)
        d = _parse(token)
        if d is None:
            return token
        plain = "." not in token and "%" not in token and "$" not in token and "," not in token
        if plain and (_PASS_LOW <= d <= _PASS_HIGH or _YEAR_LOW <= d <= _YEAR_HIGH):
            return token
        if matches(d, known):
            return token
        struck.append(token)
        return "[n/a]"

    return _NUM.sub(repl, text), struck
