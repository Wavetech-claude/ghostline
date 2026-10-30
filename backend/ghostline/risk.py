"""The risk rules: the owner sets them in the app, the backend enforces them, the AI cannot change them.

Every rule is a share of the balance, a count or a switch, never a dollar amount. Until the owner has saved every
rule, no idea passes (first run). A rule switched off does not apply. Each pass or refusal names the rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# key → (kind, the plan's suggested value, plain words). The suggestions are shown in the app as suggestions only.
RULES: dict[str, tuple[str, Decimal, str]] = {
    "max_loss_per_idea_share": ("share", Decimal("0.015"), "Max loss per idea, as a share of the account"),
    "cash_secured_puts_only": ("switch", Decimal(1), "Sell puts only when cash covers the strike × 100"),
    "earnings_blackout_days": ("count", Decimal(5), "No new idea within this many days before earnings"),
    "liquidity_min_volume": ("count", Decimal(500000), "Average daily volume floor (shares)"),
    "liquidity_min_open_interest": ("count", Decimal(500), "Open-interest floor for an option"),
    "liquidity_max_spread_share": ("share", Decimal("0.01"), "Bid-ask spread cap, as a share of the price"),
    "max_open_ideas": ("count", Decimal(5), "Cap on open ideas"),
    "drawdown_pause_share": ("share", Decimal("0.10"), "Pause new ideas after this paper drawdown"),
    "day_trades_practice_only": ("switch", Decimal(1), "Day trades stay in practice until that lane graduates"),
}
KEYS = tuple(RULES)


@dataclass(frozen=True)
class Rule:
    key: str
    value: Decimal
    enabled: bool


@dataclass(frozen=True)
class Decision:
    result: str             # pass | refuse
    rule: str | None
    reason: str

    @property
    def passed(self) -> bool:
        return self.result == "pass"


@dataclass(frozen=True)
class Idea:
    """What the rules look at. Unknown facts are None and refuse the rule that needs them (never pass by silence)."""
    kind: str                           # long | swing | puts | day
    max_loss: Decimal | None            # the idea's worst case, in money
    collateral: Decimal | None = None   # puts: strike × 100 × contracts
    days_to_earnings: int | None = None
    about_earnings: bool = False
    avg_volume: Decimal | None = None
    open_interest: Decimal | None = None
    spread_share: Decimal | None = None
    is_day_trade: bool = False
    book: str = "practice"              # real | practice | ai


@dataclass(frozen=True)
class Account:
    balance: Decimal
    cash_set_aside: Decimal
    open_ideas: int
    paper_drawdown_share: Decimal


class RuleSet:
    def __init__(self, rules: dict[str, Rule]) -> None:
        self.rules = rules

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]]) -> RuleSet:
        return cls({r["key"]: Rule(r["key"], Decimal(r["value"]), bool(r["enabled"])) for r in rows
                    if r["key"] in RULES})

    @property
    def missing(self) -> list[str]:
        return [k for k in KEYS if k not in self.rules]

    def _on(self, key: str) -> Rule | None:
        r = self.rules.get(key)
        return r if r and r.enabled else None

    def check_idea(self, idea: Idea, account: Account) -> Decision:
        if self.missing:
            return Decision("refuse", None, f"risk rules not set: {', '.join(self.missing)}")
        if (r := self._on("drawdown_pause_share")) and account.paper_drawdown_share >= r.value:
            return Decision("refuse", r.key, f"paused: paper drawdown {account.paper_drawdown_share} ≥ {r.value}")
        if (r := self._on("max_open_ideas")) and account.open_ideas >= r.value:
            return Decision("refuse", r.key, f"{account.open_ideas} open ideas, the cap is {r.value}")
        if r := self._on("max_loss_per_idea_share"):
            if idea.max_loss is None:
                return Decision("refuse", r.key, "the idea's max loss is unknown")
            if account.balance <= 0 or idea.max_loss > account.balance * r.value:
                return Decision("refuse", r.key,
                                f"max loss {idea.max_loss} exceeds {r.value} of the balance {account.balance}")
        if (r := self._on("earnings_blackout_days")) and not idea.about_earnings:
            if idea.days_to_earnings is None:
                return Decision("refuse", r.key, "the earnings date is unknown")
            if 0 <= idea.days_to_earnings <= r.value:
                return Decision("refuse", r.key, f"earnings in {idea.days_to_earnings} days, blackout is {r.value}")
        if r := self._on("liquidity_min_volume"):
            if idea.avg_volume is None or idea.avg_volume < r.value:
                return Decision("refuse", r.key, f"average volume {idea.avg_volume} is under the floor {r.value}")
        if r := self._on("liquidity_max_spread_share"):
            if idea.spread_share is None or idea.spread_share > r.value:
                return Decision("refuse", r.key, f"spread {idea.spread_share} is over the cap {r.value}")
        if idea.kind == "puts":
            if r := self._on("liquidity_min_open_interest"):
                if idea.open_interest is None or idea.open_interest < r.value:
                    return Decision("refuse", r.key, f"open interest {idea.open_interest} is under the floor {r.value}")
            if r := self._on("cash_secured_puts_only"):
                if idea.collateral is None or idea.collateral > account.cash_set_aside:
                    return Decision("refuse", r.key, f"collateral {idea.collateral} exceeds the cash set aside "
                                                     f"{account.cash_set_aside}")
        if (r := self._on("day_trades_practice_only")) and idea.is_day_trade and idea.book != "practice":
            return Decision("refuse", r.key, "day trades stay in practice until the lane graduates")
        return Decision("pass", None, "every rule passed")


def suggestions() -> list[dict[str, Any]]:
    return [{"key": k, "kind": kind, "suggested": str(v), "label": label} for k, (kind, v, label) in RULES.items()]
