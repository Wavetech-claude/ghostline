"""The ledger's arithmetic, pure and in Decimal: positions by FIFO lots, realized P&L after fees, unrealized P&L at a
given price, and the totals of a filtered set. Every number the app shows about money comes through here."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

CENT = Decimal("0.01")


def money(x: Decimal | int | float | str) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


# an option trade's quantity is in contracts and its fill is the premium per share: money is × 100
MULTIPLIER = {"stock": Decimal(1), "option": Decimal(100)}


@dataclass(frozen=True)
class Trade:
    id: int
    book_id: int
    ticker: str
    asset: str          # stock | option
    side: str           # buy | sell | sell_to_open | buy_to_close
    quantity: Decimal
    fill: Decimal
    fees: Decimal
    at: Any
    option_symbol: str | None = None    # options: the contract; each contract is its own lot queue

    @classmethod
    def from_row(cls, r: dict) -> Trade:
        return cls(id=r["id"], book_id=r["book_id"], ticker=r["ticker"], asset=r["asset"], side=r["side"],
                   quantity=Decimal(r["quantity"]), fill=Decimal(r["fill"]), fees=Decimal(r["fees"] or 0), at=r["at"],
                   option_symbol=r.get("option_symbol"))

    @property
    def opens(self) -> bool:
        return self.side in ("buy", "sell_to_open")

    @property
    def short(self) -> bool:
        """A short leg: opened by a sale (its premium is a credit) and closed by a purchase."""
        return self.side in ("sell_to_open", "buy_to_close")

    @property
    def multiplier(self) -> Decimal:
        return MULTIPLIER.get(self.asset, Decimal(1))


@dataclass
class Lot:
    trade_id: int
    quantity: Decimal
    cost_per_unit: Decimal      # fill plus the opening fee spread over the units
    at: Any


@dataclass(frozen=True)
class Closed:
    """One closing trade matched against its lots."""
    trade_id: int
    book_id: int
    ticker: str
    quantity: Decimal
    proceeds: Decimal           # what the close brought in, after its fee
    cost: Decimal               # what the matched lots cost, their fees included
    at: Any
    opened_trade_ids: tuple[int, ...]

    @property
    def pnl(self) -> Decimal:
        return money(self.proceeds - self.cost)

    @property
    def pnl_pct(self) -> Decimal | None:
        return None if self.cost == 0 else (self.pnl / self.cost * 100).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Position:
    book_id: int
    ticker: str
    quantity: Decimal
    cost_basis: Decimal         # the open lots' cost, fees included

    @property
    def average_cost(self) -> Decimal:
        return money(self.cost_basis / self.quantity) if self.quantity else money(0)

    def unrealized(self, price: Decimal | None) -> Decimal | None:
        return None if price is None else money(self.quantity * price - self.cost_basis)

    def value(self, price: Decimal | None) -> Decimal | None:
        return None if price is None else money(self.quantity * price)


@dataclass
class Book:
    closed: list[Closed] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)


def settle(trades: list[Trade]) -> tuple[list[Closed], list[Position]]:
    """FIFO per (book, ticker, asset, option contract): opening trades become lots; closing trades consume them
    oldest first. A close larger than the open lots closes what exists (the ledger says what it has, never what it
    guesses). A short leg
    (sold to open) carries its premium as a negative cost and its close pays, so its proceeds are negative; the
    P&L is proceeds minus cost either way."""
    lots: dict[tuple[int, str, str, str], list[Lot]] = defaultdict(list)
    closed: list[Closed] = []
    for t in sorted(trades, key=lambda x: (x.at, x.id)):
        key = (t.book_id, t.ticker, t.asset, t.option_symbol or "")     # two puts on one stock never share lots
        m = t.multiplier
        if t.opens:
            fee_per_unit = t.fees / t.quantity if t.quantity else Decimal(0)
            per_unit = -(t.fill * m - fee_per_unit) if t.short else t.fill * m + fee_per_unit
            lots[key].append(Lot(t.id, t.quantity, per_unit, t.at))
            continue
        left = t.quantity
        cost = Decimal(0)
        matched: list[int] = []
        while left > 0 and lots[key]:
            lot = lots[key][0]
            take = min(left, lot.quantity)
            cost += take * lot.cost_per_unit
            lot.quantity -= take
            left -= take
            matched.append(lot.trade_id)
            if lot.quantity == 0:
                lots[key].pop(0)
        closed_qty = t.quantity - left
        if closed_qty > 0:
            proceeds = -(closed_qty * t.fill * m + t.fees) if t.short else closed_qty * t.fill * m - t.fees
            closed.append(Closed(t.id, t.book_id, t.ticker, closed_qty, money(proceeds), money(cost), t.at,
                                 tuple(dict.fromkeys(matched))))
    positions = []
    for (book_id, ticker, asset, _), open_lots in lots.items():
        qty = sum((lot.quantity for lot in open_lots), Decimal(0))
        if qty > 0 and asset == "stock":
            basis = sum((lot.quantity * lot.cost_per_unit for lot in open_lots), Decimal(0))
            positions.append(Position(book_id, ticker, qty, money(basis)))
    return closed, sorted(positions, key=lambda p: (p.book_id, p.ticker))


def day_pnl(quantity: Decimal, price: Decimal | None, previous_close: Decimal | None,
            session: list[Trade]) -> Decimal | None:
    """A stock position's change in value over one session: the shares held at the previous close count from that
    close, a share bought in the session from its fill, a share sold in the session up to its fill (fees included).
    value now − value at the previous close − money put in during the session. None when a needed price is unknown."""
    bought = sum((t.quantity for t in session if t.side == "buy"), Decimal(0))
    sold = sum((t.quantity for t in session if t.side == "sell"), Decimal(0))
    held_at_close = quantity - bought + sold
    if (quantity > 0 and price is None) or (held_at_close > 0 and previous_close is None):
        return None
    put_in = sum((t.quantity * t.fill + t.fees for t in session if t.side == "buy"), Decimal(0)) - sum(
        (t.quantity * t.fill - t.fees for t in session if t.side == "sell"), Decimal(0))
    now = quantity * price if quantity > 0 and price is not None else Decimal(0)
    then = held_at_close * previous_close if held_at_close > 0 and previous_close is not None else Decimal(0)
    return money(now - then - put_in)


def totals(closed: list[Closed]) -> dict[str, Any]:
    """Count, win rate, average win, average loss, net P&L of a set of closed trades."""
    wins = [c.pnl for c in closed if c.pnl > 0]
    losses = [c.pnl for c in closed if c.pnl < 0]
    n = len(closed)
    return {
        "trades": n,
        "win_rate": (Decimal(len(wins)) / n * 100).quantize(CENT) if n else None,
        "average_win": money(sum(wins) / len(wins)) if wins else None,
        "average_loss": money(sum(losses) / len(losses)) if losses else None,
        "net_pnl": money(sum((c.pnl for c in closed), Decimal(0))),
    }


def cash_balance(flows: list[Decimal], trades: list[Trade]) -> Decimal:
    """Deposits and withdrawals, minus what was paid for opens, plus what closes brought in, minus fees."""
    bal = sum(flows, Decimal(0))
    for t in trades:
        if t.side in ("buy", "buy_to_close"):
            bal -= t.quantity * t.fill * t.multiplier + t.fees
        else:
            bal += t.quantity * t.fill * t.multiplier - t.fees
    return money(bal)
